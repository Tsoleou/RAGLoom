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

# Catalog including the bare-stem product `visionbook` — a real, distinct product
# that is ALSO a prefix of visionbook_17 / visionbook_studio. The retrieved-only
# _IDS above never exercises span-aware dedup; this does.
_CATALOG = _IDS + ["visionbook", "visionbook_studio", "luminos_s14"]


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


# ── Span-aware dedup: bare stem vs submodel (P3 / P4) ──────────────────────────

def test_conamed_base_and_submodel_both_kept():
    # 回答同時獨立點名基礎款 VisionBook 與 VisionBook 17 → 兩個都要留（P3）。
    # 舊的長度去重會把 base 'visionbook' 當作 'visionbook_17' 的字首吃掉。
    assert find_products_in_text("基礎款 VisionBook 也不錯，但 VisionBook 17 更強", _CATALOG) == [
        "visionbook_17",  # order follows _CATALOG
        "visionbook",
    ]


def test_bare_stem_only_prefix_is_dropped():
    # 只出現 'VisionBook 17'，base 'visionbook' 僅是字首 → 只留較 specific 者。
    assert find_products_in_text("我推薦 VisionBook 17", _CATALOG) == ["visionbook_17"]


def test_alias_full_transliteration_matches():
    # 全音譯 '諾瓦帕' 不再被較短的 '諾瓦' 截斷（P2）。
    assert find_products_in_text("我推薦 諾瓦帕 Pro", _CATALOG) == ["novapad_pro"]


def test_resolve_submodel_named_base_retrieved_shows_no_wrong_image():
    # 只檢索到 base 'visionbook'，但回答在講 VisionBook Studio。用完整 catalog 消歧後
    # 'visionbook' 只是 studio 的字首 → base 不算被點名 → 不貼錯圖（P4）。
    rows = [{"product_id": "visionbook"}]
    assert _resolve_product_images("VisionBook Studio 的螢幕很棒", rows, _CATALOG) == []


def test_resolve_conamed_base_and_submodel_shows_both():
    # 兩台都檢索到且都被獨立點名 → 兩張圖都給，順序跟隨 retrieval（P3 端到端）。
    rows = [{"product_id": "visionbook"}, {"product_id": "visionbook_17"}]
    out = _resolve_product_images("VisionBook 與 VisionBook 17 都不錯", rows, _CATALOG)
    assert out == [
        {"product_id": "visionbook", "url": "/product_images/visionbook.png"},
        {"product_id": "visionbook_17", "url": "/product_images/visionbook_17.png"},
    ]
