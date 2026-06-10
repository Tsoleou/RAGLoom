"""
Constraint extraction 的確定性單元測試（無 LLM，秒級）。

case 資料外部化在 eval/constraint_cases.json —— 新增 / 調整 case 只要編那份
JSON（照 {query, expected, note?} 欄位填），毋須改這支 Python。本檔只是薄薄的
執行層：把每個 case 餵給 core.constraint_filter.extract_constraints 比對。

跟 eval/runner.py（LLM-based）互補：抽取層改動後跑這個，比跑整個 LLM golden
set 快且穩。

兩種跑法：
    pytest eval/test_constraint_extraction.py          # CI / 標準
    python -m eval.test_constraint_extraction           # 快速 standalone（免 pytest）
"""

import json
import pathlib

from core.constraint_filter import extract_constraints

_CASES_PATH = pathlib.Path(__file__).parent / "constraint_cases.json"


def load_cases() -> list[dict]:
    """讀 constraint_cases.json 的 case 列表（單一資料來源）。"""
    data = json.loads(_CASES_PATH.read_text(encoding="utf-8"))
    return data["cases"]


def _extract_tuple(query: str):
    """回傳 (spec, op, value) 或 None（空集合 = 保守不過濾）。"""
    cs = extract_constraints(query)
    if not cs:
        return None
    assert len(cs) == 1, f"{query!r} 抽出多於一個約束: {cs}"
    c = cs[0]
    return (c.spec, c.op, c.value)


def _expected_tuple(case: dict):
    """把 JSON 的 expected 物件轉成 (spec, op, value) 或 None。"""
    exp = case["expected"]
    if exp is None:
        return None
    return (exp["spec"], exp["op"], exp["value"])


# ── pytest discovery ───────────────────────────────────────────────
# pytest 為 optional import，這樣 `python -m eval.test_constraint_extraction`
# 在沒裝 pytest 的環境也能跑（走下方 main()）。
try:
    import pytest

    _CASES = load_cases()

    @pytest.mark.parametrize("case", _CASES, ids=[c["query"] for c in _CASES])
    def test_extract_constraint(case: dict) -> None:
        assert _extract_tuple(case["query"]) == _expected_tuple(case)

except ImportError:
    pass


# ── standalone runner ──────────────────────────────────────────────
def main() -> int:
    cases = load_cases()
    fails = []
    for case in cases:
        got = _extract_tuple(case["query"])
        expected = _expected_tuple(case)
        if got != expected:
            fails.append((case["query"], expected, got))
    total = len(cases)
    print(f"constraint extraction: {total - len(fails)}/{total} passed")
    for query, expected, got in fails:
        print(f"  FAIL {query!r}: want {expected}, got {got}")
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
