"""
Constraint Filter — query 數值約束過濾（確定性，無 LLM）。

問題：gemma3:4b 做不了數值「比較」（Exp1 實證：critic 判 1.8kg < 1kg 仍 pass），
所以「不到 1 公斤」這種約束無法靠任何 LLM 節點落實。

設計（Exp2a Option A）= 三層全確定性：
  L1 規格表：解析 reference CSV → {product_id: {spec: canonical_value}}（query-independent）。
  L2 抽取：regex 從 query 抽 Constraint(spec, op, value)。單位型別檢查 = 每個 spec 只認
     自己的單位（質量/吋/小時/GB），所以「22 小時」不會被誤抽成 weight=22kg。
  L3 過濾：每個候選用 product_id 查 L1 規格表比較，丟掉違反約束的。
     對 retrieved chunks 與 reference CSV 列都過濾（後者避免違反產品從 always-on
     reference 那條路回到 prompt）。L3 是 spec-agnostic，加 spec 不用動它。

為什麼不用 LLM 抽取：定義域很窄（規格×比較×數字+單位），regex 失敗可審、不幻覺、
可重現。延伸既有的 code-level enforcement 哲學（Guardrail / ScopeGate / PriceGuard）。

支援的 spec（表驅動，見 SPECS）：weight / ram / storage / screen / battery。
- 單位唯一的 spec（weight=kg、screen=吋、battery=hr）：unit 本身就足以辨識。
- RAM 與 storage 共用 GB/TB：必須靠 query 裡的鄰近名詞（記憶體 vs 硬碟/SSD）消歧；
  裸「32GB 以上」無名詞 → 歧義 → 不抽（保守，交給檢索，不誤判）。
- v1 限制：一個 query 只抽「一個」約束（op 整句偵測一次）。混合方向的多約束
  （如「螢幕≥16吋 + 電池≤5hr」）無法用單一 op 分配 → 偵測到 up+down 並存就回 []
  （保守，交給檢索，不誤刪）。同方向多約束只會抽第一個匹配的 spec，其餘忽略。
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
    spec: str       # weight | ram | storage | screen | battery
    op: str         # lt | lte | gt | gte
    value: float    # canonical 單位（見 SPECS[spec].canonical_unit）

    def describe(self) -> str:
        sym = {"lt": "<", "lte": "<=", "gt": ">", "gte": ">="}.get(self.op, self.op)
        unit = SPECS[self.spec].canonical_unit if self.spec in SPECS else ""
        return f"{self.spec} {sym} {self.value}{unit}"


# ── Spec 表（spec 驅動的單一事實來源）──────────────────────────────
#
# 加一個 spec = 在 SPECS 加一筆，L1/L2/L3 都靠這張表運作，不用改邏輯。
@dataclass
class SpecDef:
    name: str               # canonical spec 名
    canonical_unit: str     # 顯示用單位
    csv_col: str            # reference CSV 欄名
    unit_factors: dict      # {單位字串: 乘到 canonical 的係數}
    spec_keywords: list     # query 裡用來「指認這個 spec」的詞（消歧用）
    needs_keyword: bool     # True = 必須出現 spec_keyword 才抽（GB 共用者用）
    exclude_keywords: list = None  # query 含這些詞時不認領此 spec（避免子字串誤判）


# 比較關鍵詞 → op。長詞優先（「不超過」先於「超過」、「不低於」先於「低於」）。
# spec-agnostic：所有 spec 共用同一組方向詞。
_OP_KEYWORDS: list[tuple[str, str]] = [
    ("不超過", "lte"), ("不超出", "lte"), ("最多", "lte"), ("以內", "lte"),
    ("以下", "lte"), ("或以下", "lte"),
    ("at most", "lte"), ("up to", "lte"), ("no more than", "lte"),
    ("不低於", "gte"), ("至少", "gte"), ("以上", "gte"), ("起跳", "gte"),
    ("at least", "gte"), ("minimum", "gte"),
    ("不到", "lt"), ("低於", "lt"), ("少於", "lt"), ("小於", "lt"), ("輕於", "lt"),
    ("less than", "lt"), ("under", "lt"), ("below", "lt"),
    ("超過", "gt"), ("大於", "gt"), ("多於", "gt"), ("重於", "gt"), ("高於", "gt"),
    ("more than", "gt"), ("over", "gt"), ("above", "gt"),
]

SPECS: dict[str, SpecDef] = {
    "weight": SpecDef(
        name="weight", canonical_unit="kg", csv_col="重量",
        unit_factors={
            "公斤": 1.0, "千克": 1.0, "kgs": 1.0, "kg": 1.0,
            "公克": 0.001, "grams": 0.001, "gram": 0.001, "克": 0.001,
            "磅": 0.453592, "pounds": 0.453592, "pound": 0.453592, "lbs": 0.453592, "lb": 0.453592,
        },
        # 刻意不收 bare "g"：它與容量「512G」(storage/ram 省略 B 的常見寫法) 相撞，
        # 且 weight 掃描在前 → 「硬碟 512G 以下」會被誤抽成 weight 0.512kg → 假性
        # 拒答。中文「克/公克」、英文「grams」仍可表達公克；裸英文「900g」→ no-op
        # (catalog 最輕 0.99kg，本來就無解，無害)。
        spec_keywords=["重量", "重", "weight", "weigh"],
        needs_keyword=False,  # 質量單位唯一，unit 本身就足以辨識
    ),
    "screen": SpecDef(
        name="screen", canonical_unit="吋", csv_col="螢幕尺寸",
        unit_factors={"吋": 1.0, "inch": 1.0, "inches": 1.0, "\"": 1.0},
        spec_keywords=["螢幕", "螢幕尺寸", "面板", "screen", "display"],
        needs_keyword=False,  # 吋/inch 唯一
    ),
    "battery": SpecDef(
        name="battery", canonical_unit="hr", csv_col="電池",
        unit_factors={"小時": 1.0, "hr": 1.0, "hrs": 1.0, "hour": 1.0, "hours": 1.0, "h": 1.0},
        spec_keywords=["續航", "電池", "battery", "battery life"],
        needs_keyword=False,  # 時間單位唯一
    ),
    "ram": SpecDef(
        name="ram", canonical_unit="GB", csv_col="RAM",
        unit_factors={"gb": 1.0, "g": 1.0, "tb": 1024.0},
        spec_keywords=["記憶體", "內存", "ram", "memory"],
        needs_keyword=True,  # GB 與 storage 共用 → 必須有 spec 詞消歧
        # VRAM/顯卡記憶體 = 顯卡記憶體，不是系統 RAM（也不是我們支援的 spec）。
        # 「ram」是「vram」子字串，不排除會把「VRAM 8GB 以上」誤抽成系統 ram。
        exclude_keywords=["vram", "顯卡記憶體", "顯存", "顯卡"],
    ),
    "storage": SpecDef(
        name="storage", canonical_unit="GB", csv_col="儲存",
        unit_factors={"gb": 1.0, "g": 1.0, "tb": 1024.0, "t": 1024.0},
        spec_keywords=["儲存", "存儲", "硬碟", "容量", "空間", "storage", "ssd", "disk", "drive"],
        needs_keyword=True,  # GB 與 ram 共用 → 必須有 spec 詞消歧
    ),
}


def _has_keyword(query_lower: str, keywords: list) -> bool:
    return any(kw.lower() in query_lower for kw in keywords)


_OP_DIRECTION = {"lt": "down", "lte": "down", "gt": "up", "gte": "up"}


def _detect_op(query: str) -> str | None:
    """掃描 query 找比較方向。長關鍵詞優先（'不超過' 先於 '超過'）。"""
    q = query.lower()
    for kw, op in sorted(_OP_KEYWORDS, key=lambda x: len(x[0]), reverse=True):
        if kw.lower() in q:
            return op
    return None


def _has_opposite_directions(query: str) -> bool:
    """True if the query carries BOTH an up- and a down-direction comparator
    (e.g. 螢幕至少16吋 + 電池5小時以下) — an ambiguous mixed-direction request the
    single-op extractor can't resolve, so we bail to []. Mirrors _detect_op's
    longest-match-wins by CONSUMING each matched keyword before scanning the
    rest, so a negated single comparator ('不超過' which contains '超過') counts
    as one direction, not two."""
    q = query.lower()
    seen = set()
    for kw, op in sorted(_OP_KEYWORDS, key=lambda x: len(x[0]), reverse=True):
        kwl = kw.lower()
        if kwl in q:
            seen.add(_OP_DIRECTION[op])
            q = q.replace(kwl, " ")  # 消化掉，避免短子字串（超過 ⊂ 不超過）被重複數
    return "up" in seen and "down" in seen


def _num_unit_match(query: str, spec: SpecDef):
    """在 query 找「數字 + 此 spec 的單位」。回 (value_canonical, unit_str) 或 None。
    負向前瞻擋 Latin 單位後接字母（'16GB' 的 g 不被當 weight 的 g）。"""
    alt = "|".join(sorted((re.escape(u) for u in spec.unit_factors), key=len, reverse=True))
    pat = re.compile(rf"(\d+(?:\.\d+)?)\s*({alt})(?![a-zA-Z])", re.IGNORECASE)
    m = pat.search(query)
    if not m:
        return None
    factor = spec.unit_factors[m.group(2).lower()]
    return float(m.group(1)) * factor, m.group(2)


def extract_constraints(query: str) -> list[Constraint]:
    """regex 抽取數值約束（spec 表驅動）。

    一個 query 抽「一個」約束（op 整句偵測一次）。對每個 spec 依序試：
    需同時 (1) 數字+該 spec 的單位 (2) 比較詞 (3) needs_keyword 者還要 spec 關鍵詞。
    第一個滿足的 spec 勝出。全不滿足 → []（保守，不過濾）。
    """
    if not query or not query.strip():
        return []
    if _has_opposite_directions(query):
        return []  # 混合方向（螢幕≥16吋 + 電池≤5hr）→ 單一 op 無法分配 → 保守不過濾
    op = _detect_op(query)
    if op is None:
        return []  # 無比較詞（「1公斤的筆電」「16GB記憶體」）→ 保守不過濾
    q_lower = query.lower()
    # 固定順序：unit 唯一的先試，GB 共用的後試（且需 keyword），避免裸 GB 誤命中。
    for spec_name in ("weight", "screen", "battery", "ram", "storage"):
        spec = SPECS[spec_name]
        if spec.exclude_keywords and _has_keyword(q_lower, spec.exclude_keywords):
            continue  # query 含排除詞（如 vram）→ 此 spec 不認領
        if spec.needs_keyword and not _has_keyword(q_lower, spec.spec_keywords):
            continue
        hit = _num_unit_match(query, spec)
        if hit is not None:
            value, _ = hit
            return [Constraint(spec=spec_name, op=op, value=value)]
    return []


# ── L1：從 reference CSV 建規格表 ───────────────────────────────────
_CSV_PID_COL = "product_id"


def _parse_spec_cell(cell: str, spec: SpecDef) -> float | None:
    """CSV 欄值（如『1.7kg』『32GB LPDDR5』『14吋』『22hr』）→ canonical 數值。
    解析不出 → None。CSV 值的單位用該 spec 自己的 unit_factors。"""
    alt = "|".join(sorted((re.escape(u) for u in spec.unit_factors), key=len, reverse=True))
    m = re.search(rf"(\d+(?:\.\d+)?)\s*({alt})(?![a-zA-Z])", cell or "", re.IGNORECASE)
    if not m:
        return None
    return float(m.group(1)) * spec.unit_factors[m.group(2).lower()]


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
    """解析 reference CSV → {product_id: {spec: canonical_value}}（所有 SPECS）。
    query-independent，init 建一次。某 spec 欄不存在或某格解析不出 → 該 spec 略過
    （產品仍會有其他成功解析的 spec）。"""
    rows = _csv_rows(reference_text)
    if len(rows) < 2:
        return {}
    header = rows[0]
    try:
        pid_i = header.index(_CSV_PID_COL)
    except ValueError:
        return {}
    # 預先找出每個 spec 的欄位 index（缺欄就跳過該 spec）
    col_idx = {name: header.index(s.csv_col) for name, s in SPECS.items() if s.csv_col in header}
    table: dict[str, dict[str, float]] = {}
    for row in rows[1:]:
        if len(row) <= pid_i:
            continue
        pid = row[pid_i].strip()
        if not pid:
            continue
        specs: dict[str, float] = {}
        for name, ci in col_idx.items():
            if ci < len(row):
                val = _parse_spec_cell(row[ci], SPECS[name])
                if val is not None:
                    specs[name] = val
        if specs:
            table[pid] = specs
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
