"""
產品名音譯還原的確定性單元測試（無 LLM，秒級）。

覆蓋三層：
  1. core.product_matcher.restore_english_names —— 把生成回答裡的中文音譯
     （星鋒 X1）還原成英文原名（StarForge X1）：繁/簡、黏字補空格、
     longest-first、JSON envelope 安全。
  2. P5 迴歸 —— '流明' 已從 luminos 別名移除，規格句 '300流明' 不再誤配品牌；
     '璐米諾' 仍正常路由。
  3. core.product_matcher.find_untranslated_mentions —— 未知音譯偵測
     （log-only heuristic）：抓 '星輝X1'，放過 '這台X1' 與一般規格詞。
"""

import json

from core.product_matcher import (
    detect_product_filter,
    find_products_in_text,
    find_untranslated_mentions,
    restore_english_names,
)

_IDS = ["starforge_x1", "novapad_pro", "visionbook_17", "luminos_s14", "titanbook_ws2"]


# ── restore_english_names ──────────────────────────────────────────


def test_restore_traditional_alias():
    assert restore_english_names("這台星鋒 X1 超讚") == "這台StarForge X1 超讚"


def test_restore_inserts_space_when_fused_with_ascii():
    # 音譯直接黏著型號時，還原後要補空格，不能變 StarForgeX1。
    assert restore_english_names("星鋒X1好用嗎") == "StarForge X1好用嗎"


def test_restore_simplified_alias():
    assert restore_english_names("星锋 X1 很棒") == "StarForge X1 很棒"


def test_restore_longest_alias_first():
    # '諾瓦帕' 必須先於 '諾瓦' 被換掉，否則殘留 '帕' 黏在英文名後面。
    assert restore_english_names("諾瓦帕Pro 16 適合學生") == "NovaPad Pro 16 適合學生"
    assert restore_english_names("諾瓦 Air 也不錯") == "NovaPad Air 也不錯"


def test_restore_luminos_brand_alias():
    assert restore_english_names("璐米諾 S14 適合商務人士") == "Luminos S14 適合商務人士"


def test_restore_leaves_lumens_unit_alone():
    # P5：'流明' 是亮度單位，不是品牌別名，一個字都不能動。
    text = "螢幕亮度高達 400 流明，戶外也清晰"
    assert restore_english_names(text) == text


def test_restore_multiple_brands_in_one_reply():
    out = restore_english_names("星鋒X1跟泰坦書WS2都很強")
    assert out == "StarForge X1跟TitanBook WS2都很強"


def test_restore_is_safe_inside_json_envelope():
    # chatbot persona 的回覆是 JSON envelope 字串，替換後必須仍是合法 JSON。
    raw = json.dumps({"reply": "星鋒 X1 很棒", "emotion": "happy"}, ensure_ascii=False)
    out = restore_english_names(raw)
    assert json.loads(out) == {"reply": "StarForge X1 很棒", "emotion": "happy"}


def test_restore_noop_on_english_and_empty():
    assert restore_english_names("StarForge X1 is great") == "StarForge X1 is great"
    assert restore_english_names("") == ""


# ── P5 迴歸：流明不再是 luminos 別名 ───────────────────────────────


def test_lumens_spec_sentence_matches_no_product():
    assert find_products_in_text("300流明的亮度非常適合戶外使用", _IDS) == []


def test_luminos_still_routes_via_remaining_alias():
    assert detect_product_filter("介紹一下璐米諾 S14", _IDS) == "luminos_s14"


def test_simplified_alias_now_routes():
    assert detect_product_filter("介紹一下星锋X1", _IDS) == "starforge_x1"


def test_restored_text_feeds_image_picker_matching():
    # 還原後的文字要能被挑圖邏輯命中（end-to-end 銜接點）。
    restored = restore_english_names("我推薦星鋒X1")
    assert find_products_in_text(restored, _IDS) == ["starforge_x1"]


# ── find_untranslated_mentions（log-only heuristic）────────────────


def test_unknown_transliteration_is_flagged():
    assert find_untranslated_mentions("星輝X1好用嗎", _IDS) == ["星輝X1"]


def test_ordinary_prose_before_model_token_not_flagged():
    # '這台X1' 是正常指代，不是音譯。
    assert find_untranslated_mentions("這台X1很棒", _IDS) == []


def test_spec_terms_not_flagged():
    assert find_untranslated_mentions("支援Thunderbolt 4與Wi-Fi 7", _IDS) == []
    # 純數字 token（visionbook_17 的 '17'）不參與，規格句不誤報。
    assert find_untranslated_mentions("續航17小時", _IDS) == []


def test_clean_restored_text_not_flagged():
    assert find_untranslated_mentions("StarForge X1 真的很好用", _IDS) == []
