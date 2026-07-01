"""
產品圖挑選的確定性單元測試（無 LLM，秒級）。

覆蓋兩層：
  1. core.product_matcher.find_products_in_text —— 掃回答文字抓出所有點名產品
     （單一 / 比較多產品 / 中文別名 / prefix 去重 / 無命中）。
  2. api.chat_service._resolve_product_images —— 交集 retrieval product_id 擋幻覺、
     檔案存在才給、blocked 由呼叫端負責（此處只測 resolve 本身）。

跟 LLM golden set 互補：改動挑圖邏輯後跑這個，比跑整組 LLM 快且穩。
"""

from api.chat_service import _resolve_product_images
from core.product_matcher import find_products_in_text

_IDS = ["starforge_x1", "starforge_titan_9000", "novapad_pro", "visionbook_17", "titanbook_ws2"]


def test_single_product_named():
    assert find_products_in_text("我推薦 StarForge X1，它很適合你", _IDS) == ["starforge_x1"]


def test_comparison_returns_all_named():
    # detect_product_filter 遇比較句回 None；挑圖要兩台都抓到。
    assert find_products_in_text("比較 NovaPad Pro 跟 VisionBook 17", _IDS) == [
        "novapad_pro",
        "visionbook_17",
    ]


def test_chinese_alias_matches():
    assert find_products_in_text("星鋒 X1 是最好的選擇", _IDS) == ["starforge_x1"]


def test_prefix_dedup_keeps_specific():
    # 'starforge' 與 'starforge_titan_9000' 都命中時只留較長者。
    assert find_products_in_text("推薦 StarForge Titan 9000", _IDS) == ["starforge_titan_9000"]


def test_no_product_named():
    assert find_products_in_text("這個產品很好", _IDS) == []


def test_empty_text():
    assert find_products_in_text("", _IDS) == []
    assert find_products_in_text("   ", _IDS) == []


def test_result_follows_caller_order():
    # 回傳順序跟隨傳入的 product_ids，與文字出現順序無關（可重現）。
    assert find_products_in_text("先講 VisionBook 17 再講 NovaPad Pro", _IDS) == [
        "novapad_pro",
        "visionbook_17",
    ]


def test_resolve_maps_named_to_url():
    rows = [{"product_id": "starforge_x1"}, {"product_id": "novapad_pro"}]
    out = _resolve_product_images("我推薦 StarForge X1", rows)
    assert out == [{"product_id": "starforge_x1", "url": "/product_images/starforge_x1.png"}]


def test_resolve_bounds_hallucinated_name():
    # 回答點名了 novapad_pro，但它不在 retrieval 裡 → 不給圖（擋幻覺）。
    rows = [{"product_id": "starforge_x1"}]
    assert _resolve_product_images("我推薦 NovaPad Pro", rows) == []


def test_resolve_no_retrieval():
    assert _resolve_product_images("我推薦 StarForge X1", []) == []
    assert _resolve_product_images("我推薦 StarForge X1", [{"product_id": None}]) == []
