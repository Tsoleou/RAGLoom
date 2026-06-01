"""
Constraint extraction 的確定性單元測試（無 LLM，秒級）。

跟 eval/runner.py（LLM-based）互補：這支只測 core.constraint_filter.extract_constraints
的純 regex 抽取邏輯 —— 各 spec × op × 單位、消歧、以及 codebase-flaw-auditor 抓到的
回歸守門 case。抽取層改動後跑這個，比跑整個 LLM golden set 快且穩。

跑法：
    source venv/bin/activate && python -m eval.test_constraint_extraction
"""

from core.constraint_filter import extract_constraints


def _one(query):
    """回傳 (spec, op, value) 或 None（空集合）。"""
    cs = extract_constraints(query)
    if not cs:
        return None
    assert len(cs) == 1, f"{query!r} 抽出多於一個約束: {cs}"
    c = cs[0]
    return (c.spec, c.op, c.value)


# (query, expected)。expected = (spec, op, value) 或 None（應回空，保守不過濾）。
CASES = [
    # ── 各 spec 正例 ──────────────────────────────────────────────
    ("不到 1 公斤的筆電", ("weight", "lt", 1.0)),
    ("laptops under 2kg", ("weight", "lt", 2.0)),
    ("不超過 1500 公克", ("weight", "lte", 1.5)),          # 否定詞 trap（'超過'⊂'不超過'）
    ("不低於 2 公斤", ("weight", "gte", 2.0)),             # 否定詞 trap（'低於'⊂'不低於'）
    ("900克以下", ("weight", "lte", 0.9)),
    ("螢幕 16 吋以上", ("screen", "gte", 16.0)),
    ("17吋以上的大螢幕", ("screen", "gte", 17.0)),
    ("display at least 16 inch", ("screen", "gte", 16.0)),
    ("續航至少 20 小時", ("battery", "gte", 20.0)),
    ("battery over 18 hours", ("battery", "gt", 18.0)),
    ("續航低於 5 小時的", ("battery", "lt", 5.0)),
    ("記憶體至少 32GB", ("ram", "gte", 32.0)),
    ("memory over 16GB", ("ram", "gt", 16.0)),
    ("內存 64GB 以上", ("ram", "gte", 64.0)),
    ("硬碟至少 1TB", ("storage", "gte", 1024.0)),
    ("storage over 512GB", ("storage", "gt", 512.0)),
    ("儲存空間不到 256GB", ("storage", "lt", 256.0)),
    ("2TB 以上儲存空間", ("storage", "gte", 2048.0)),
    # ── 回歸守門（codebase-flaw-auditor High/Medium）─────────────
    ("硬碟 512G 以下", ("storage", "lte", 512.0)),         # High-1: 裸 G 不被當 weight 克
    ("記憶體 8G 以上", ("ram", "gte", 8.0)),               # High-1 變體
    ("螢幕至少 16 吋，電池 5 小時以下", None),               # High-2: 混合方向 → 空
    ("VRAM 8GB 以上", None),                               # Medium-1: VRAM 非系統 ram
    # ── 負控（保守不過濾）────────────────────────────────────────
    ("32GB 以上", None),                                  # 裸 GB 無 spec 詞
    ("512G 以下", None),                                  # 裸 G 無 spec 詞
    ("16GB 記憶體", None),                                # 無比較詞
    ("有沒有輕一點的筆電", None),                          # 無數字
    ("介紹 StarForge X1", None),                          # 純產品名
    ("比較 X1 跟 Phantom", None),                         # 比較題
]


def main() -> int:
    fails = []
    for query, expected in CASES:
        got = _one(query)
        if got != expected:
            fails.append((query, expected, got))
    total = len(CASES)
    print(f"constraint extraction: {total - len(fails)}/{total} passed")
    for query, expected, got in fails:
        print(f"  FAIL {query!r}: want {expected}, got {got}")
    return 1 if fails else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
