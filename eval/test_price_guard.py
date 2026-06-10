"""
core.price_guard.is_price_query 的確定性單元測試（無 LLM，秒級）。

case 資料外部化在 eval/price_guard_cases.json —— 新增 / 調整 case 只要編那份
JSON（照 {query, price, note?} 欄位填），毋須改這支 Python。

重點守的行為：EN/ZH 價格意圖偵測（how much / MSRP / discount / 售價 / 多少錢 /
折扣 …）與負控（'多大'、'多重'、規格題、空字串不誤觸）。

跑法：pytest eval/test_price_guard.py
"""

import json
import pathlib

import pytest

from core.price_guard import is_price_query

_CASES_PATH = pathlib.Path(__file__).parent / "price_guard_cases.json"
_CASES = json.loads(_CASES_PATH.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", _CASES, ids=[c["query"] or "<empty>" for c in _CASES])
def test_is_price_query(case: dict) -> None:
    assert is_price_query(case["query"]) is case["price"]
