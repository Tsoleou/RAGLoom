"""
core.guardrail.check_query 的確定性單元測試（無 LLM，秒級）。

case 資料外部化在 eval/guardrail_cases.json —— 新增 / 調整 case 只要編那份 JSON
（照 {query, blocked, matched?, keywords?, note?} 欄位填），毋須改這支 Python。

重點守的行為：ASCII word boundary（短關鍵字如 'hp' 不誤中 'hpx'、'dell' 不誤中
'modelling'）、CJK 邊界（'asus筆電' 要中）、大小寫不敏感、自訂關鍵字清單。

跑法：pytest eval/test_guardrail.py
"""

import json
import pathlib

import pytest

from core.guardrail import check_query

_CASES_PATH = pathlib.Path(__file__).parent / "guardrail_cases.json"
_CASES = json.loads(_CASES_PATH.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _CASES, ids=[c["query"] for c in _CASES])
def test_check_query(case: dict) -> None:
    allowed, _msg, matched = check_query(
        case["query"], blocked_keywords=case.get("keywords")
    )
    # check_query returns allowed=True when NOT blocked.
    assert allowed == (not case["blocked"])
    if case["blocked"]:
        assert matched == case["matched"]
