"""
Constraint Filter — query 數值約束過濾（確定性，無 LLM）。

問題：gemma3:4b 做不了數值「比較」（Exp1 實證：critic 判 1.8kg < 1kg 仍 pass），
所以「不到 1 公斤」這種約束無法靠任何 LLM 節點落實。

設計（Exp2a Option A）= 三層全確定性：
  L1 規格表：解析 reference CSV → {product_id: {weight: kg}}（query-independent）。
  L2 抽取：regex 從 query 抽 Constraint(spec, op, value)。單位型別檢查讓「22 hours」
     不會被誤抽成 weight=22kg（時間單位不在質量單位表 → 不匹配）。
  L3 過濾：每個候選用 product_id 查 L1 規格表比較，丟掉違反約束的。
     對 retrieved chunks 與 reference CSV 列都過濾（後者避免違反產品從 always-on
     reference 那條路回到 prompt）。

為什麼不用 LLM 抽取：定義域很窄（規格×比較×數字+單位），regex 失敗可審、不幻覺、
可重現。延伸既有的 code-level enforcement 哲學（Guardrail / ScopeGate / PriceGuard）。

v1 只支援 weight（單位唯一無歧義）。RAM/storage（共用 GB，需鄰近名詞消歧）、
screen 待架構驗證後再擴。
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass

from core.vector_store import RetrievalResult


# ── 空集合短路 ──────────────────────────────────────────────────────
#
# 當約束把候選「全部」濾掉（沒有產品符合），不能把空 context 丟給 generator：
# gemma3:4b 拿到空 context 不會誠實說「沒有」，會為了 helpful 捏造假產品
# （實測：問「不到 0.5 公斤」會掰出不存在的 LZ500 980g）。所以全濾光時改用
# code-level canned 拒答，跟 Guardrail / ScopeGate / PriceGuard 同一個 pattern。
class ConstraintBlocked(Exception):
    """Raised when a numeric constraint filters out ALL candidates — no product
    matches. Mirrors ScopeBlocked so the graph engine short-circuits with a
    canned refusal instead of letting a 4B model hallucinate a fake product."""

    def __init__(self, reason: str, refusal_message: str, matched_keyword: str):
        super().__init__(reason)
        self.reason = reason
        self.refusal_message = refusal_message
        self.matched_keyword = matched_keyword


_REFUSAL_EN = (
    "I don't have a laptop at this booth that fits that exact requirement. "
    "Want to loosen the criteria a little, or tell me what you'll use it for?"
)
_REFUSAL_ZH = (
    "這邊沒有完全符合這個條件的筆電耶。要不要把條件放寬一點，"
    "或是跟我說說你主要會拿來做什麼？"
)


def refusal_message(query: str, format_hint=None) -> str:
    """Canned bilingual 'no match' refusal. JSON-wrapped for chatbot format."""
    text = _REFUSAL_ZH if re.search(r"[一-鿿]", query or "") else _REFUSAL_EN
    if isinstance(format_hint, dict) or format_hint == "json":
        return json.dumps({"reply": text, "emotion": "idle"}, ensure_ascii=False)
    return text


# ── 值物件 ──────────────────────────────────────────────────────────
@dataclass
class Constraint:
    spec: str       # weight（v1 only）
    op: str         # lt | lte | gt | gte
    value: float    # canonical 單位：weight=kg

    def describe(self) -> str:
        sym = {"lt": "<", "lte": "<=", "gt": ">", "gte": ">="}.get(self.op, self.op)
        unit = "kg" if self.spec == "weight" else ""
        return f"{self.spec} {sym} {self.value}{unit}"


# ── L2：regex 抽取 ──────────────────────────────────────────────────
#
# 質量單位 → 轉成 kg 的係數。「單位型別檢查」就靠這張表：時間（hr/小時）、
# 長度（吋/inch）不在裡面 → number+unit regex 不匹配 → 不會誤抽成 weight。
_MASS_UNIT_FACTOR = {
    "公斤": 1.0, "千克": 1.0, "kgs": 1.0, "kg": 1.0,
    "公克": 0.001, "grams": 0.001, "gram": 0.001, "克": 0.001, "g": 0.001,
    "磅": 0.453592, "pounds": 0.453592, "pound": 0.453592, "lbs": 0.453592, "lb": 0.453592,
}
# alternation 長的在前，避免「公克」被「克」搶、「kg」被「g」搶。
_MASS_UNIT_ALT = "|".join(
    sorted(_MASS_UNIT_FACTOR.keys(), key=len, reverse=True)
)
# 負向前瞻 (?![a-zA-Z]) 擋住 Latin 單位後接字母的誤匹配：否則 bare 'g'(0.001kg)
# 會吃到「16GB」的 G，把 RAM 查詢誤抽成 weight。對 CJK 單位無影響（公斤後接「的」）。
_NUM_MASS_RE = re.compile(
    rf"(\d+(?:\.\d+)?)\s*({_MASS_UNIT_ALT})(?![a-zA-Z])",
    re.IGNORECASE,
)

# 比較關鍵詞 → op。掃描時長詞優先（「不超過」要先於「超過」、「不低於」先於「低於」），
# 所以這裡用 list 並在比對時依長度排序。
_OP_KEYWORDS: list[tuple[str, str]] = [
    # lte（含上界）
    ("不超過", "lte"), ("不超出", "lte"), ("最多", "lte"), ("以內", "lte"),
    ("以下", "lte"), ("或以下", "lte"),
    ("at most", "lte"), ("up to", "lte"), ("no more than", "lte"), ("no heavier than", "lte"),
    # gte（含下界）
    ("不低於", "gte"), ("至少", "gte"), ("以上", "gte"), ("起跳", "gte"),
    ("at least", "gte"), ("minimum", "gte"),
    # lt（嚴格小於）
    ("不到", "lt"), ("低於", "lt"), ("少於", "lt"), ("小於", "lt"), ("輕於", "lt"),
    ("less than", "lt"), ("under", "lt"), ("below", "lt"), ("lighter than", "lt"),
    # gt（嚴格大於）
    ("超過", "gt"), ("大於", "gt"), ("多於", "gt"), ("重於", "gt"), ("高於", "gt"),
    ("more than", "gt"), ("over", "gt"), ("above", "gt"), ("heavier than", "gt"),
]


def _detect_op(query: str) -> str | None:
    """掃描 query 找比較方向。長關鍵詞優先（'不超過' 先於 '超過'）。"""
    q = query.lower()
    for kw, op in sorted(_OP_KEYWORDS, key=lambda x: len(x[0]), reverse=True):
        if kw.lower() in q:
            return op
    return None


def extract_constraints(query: str) -> list[Constraint]:
    """regex 抽取數值約束。v1：weight。

    需同時滿足：(1) query 含「數字+質量單位」 (2) 含比較關鍵詞。
    缺任一 → 回 []（保守，不過濾）。時間/長度單位天生不匹配質量單位 → 自動排除。
    """
    if not query or not query.strip():
        return []
    m = _NUM_MASS_RE.search(query)
    if not m:
        return []  # 沒有「數字+質量單位」→ 無 weight 約束（22小時 / 16吋 / 純產品名都走這）
    op = _detect_op(query)
    if op is None:
        return []  # 有重量數字但無比較詞（「1公斤的筆電」）→ 保守不過濾
    value_kg = float(m.group(1)) * _MASS_UNIT_FACTOR[m.group(2).lower()]
    return [Constraint(spec="weight", op=op, value=value_kg)]


# ── L1：從 reference CSV 建規格表 ───────────────────────────────────
_CSV_WEIGHT_COL = "重量"
_CSV_PID_COL = "product_id"


def _parse_mass_cell(cell: str) -> float | None:
    """CSV 重量欄『1.7kg』→ 1.7（canonical kg）。解析不出 → None。"""
    m = _NUM_MASS_RE.search(cell or "")
    if not m:
        return None
    return float(m.group(1)) * _MASS_UNIT_FACTOR[m.group(2).lower()]


def _csv_rows(reference_text: str) -> list[list[str]]:
    """從 reference 文字抽出 CSV 列（跳過 '# filename' 註解行與空行）。"""
    lines = [
        ln for ln in reference_text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    if not lines:
        return []
    return list(csv.reader(lines))


def build_spec_table(reference_text: str) -> dict[str, dict[str, float]]:
    """解析 reference CSV → {product_id: {weight: kg}}。query-independent，init 建一次。"""
    rows = _csv_rows(reference_text)
    if len(rows) < 2:
        return {}
    header = rows[0]
    try:
        pid_i = header.index(_CSV_PID_COL)
        w_i = header.index(_CSV_WEIGHT_COL)
    except ValueError:
        return {}
    table: dict[str, dict[str, float]] = {}
    for row in rows[1:]:
        if len(row) <= max(pid_i, w_i):
            continue
        pid = row[pid_i].strip()
        w = _parse_mass_cell(row[w_i])
        if pid and w is not None:
            table[pid] = {"weight": w}
    return table


# ── L3：比較 + 過濾 ─────────────────────────────────────────────────
def _satisfies(actual: float, op: str, target: float) -> bool:
    if op == "lt":
        return actual < target
    if op == "lte":
        return actual <= target
    if op == "gt":
        return actual > target
    if op == "gte":
        return actual >= target
    return True


def _product_keep(
    product_id: str | None,
    constraints: list[Constraint],
    spec_table: dict[str, dict[str, float]],
) -> tuple[bool, str]:
    """產品是否通過所有約束。未知產品 / 表無該 spec → 保留（safe default）。"""
    specs = spec_table.get(product_id or "")
    if specs is None:
        return True, f"unknown product '{product_id}' → kept"
    for c in constraints:
        actual = specs.get(c.spec)
        if actual is None:
            continue  # 表沒這個 spec → 無法判斷 → 此約束視為通過
        if not _satisfies(actual, c.op, c.value):
            return False, f"{c.describe()} vs {actual} → VIOLATE"
    return True, "ok"


def any_product_matches(
    constraints: list[Constraint],
    spec_table: dict[str, dict[str, float]],
) -> bool:
    """True if at least one product in the CATALOG (spec table) satisfies all
    constraints. This is the basis for the 'no match' refusal — it must be
    catalog-scoped, not retrieval-scoped: the canned message claims "we have
    none", so it has to check the whole catalog. A retrieval-scoped check would
    falsely refuse whenever top-K retrieval misses a qualifying product (which
    demonstrably happens here — see the cross-lingual retrieval misses), telling
    the visitor "we have nothing" when we do. Errs toward NOT refusing: a product
    whose spec we can't evaluate counts as a possible match."""
    if not spec_table:
        return True  # no catalog to disprove against → don't refuse
    for specs in spec_table.values():
        ok = True
        for c in constraints:
            actual = specs.get(c.spec)
            if actual is None:
                continue  # can't evaluate → can't disprove → treat as match
            if not _satisfies(actual, c.op, c.value):
                ok = False
                break
        if ok:
            return True
    return False


def filter_results(
    results: list[RetrievalResult],
    constraints: list[Constraint],
    spec_table: dict[str, dict[str, float]],
) -> tuple[list[RetrievalResult], list[dict]]:
    """用約束過濾 retrieval 結果（product-level lookup）。回 (kept, trace)。"""
    if not constraints:
        return results, []
    kept: list[RetrievalResult] = []
    trace: list[dict] = []
    for r in results:
        pid = r.chunk.metadata.get("product_id")
        keep, reason = _product_keep(pid, constraints, spec_table)
        trace.append({"product_id": pid, "kept": keep, "detail": reason})
        if keep:
            kept.append(r)
    dropped = len(results) - len(kept)
    if dropped:
        print(f"[ConstraintFilter] {[c.describe() for c in constraints]} "
              f"→ kept {len(kept)}/{len(results)} chunks ({dropped} dropped)")
    return kept, trace


def filter_reference_rows(
    reference_text: str,
    constraints: list[Constraint],
    spec_table: dict[str, dict[str, float]],
) -> str:
    """過濾 reference CSV：丟掉違反約束的產品列，保留註解行、表頭、合規列。

    這是 Option A 的關鍵第二刀——reference 整表 always-on 注入 prompt，不濾的話
    違反約束的產品（如 1.8kg 機種）會從這條路回到 generator 眼前。
    """
    if not constraints or not reference_text.strip():
        return reference_text

    # 先找 product_id 欄位 index（從 CSV 表頭）。
    rows = _csv_rows(reference_text)
    if len(rows) < 2:
        return reference_text
    try:
        pid_i = rows[0].index(_CSV_PID_COL)
    except ValueError:
        return reference_text

    out: list[str] = []
    header_seen = False
    for ln in reference_text.splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or "," not in ln:
            out.append(ln)
            continue
        if not header_seen:
            out.append(ln)  # 表頭保留
            header_seen = True
            continue
        try:
            fields = next(csv.reader([ln]))
            pid = fields[pid_i].strip() if len(fields) > pid_i else ""
        except Exception:
            out.append(ln)  # 解析失敗 → 保守保留
            continue
        keep, _ = _product_keep(pid, constraints, spec_table)
        if keep:
            out.append(ln)
    return "\n".join(out)
