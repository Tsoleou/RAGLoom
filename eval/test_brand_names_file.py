"""
品牌名 metadata 檔案層（product_aliases.json）的確定性單元測試（無 LLM，秒級）。

覆蓋四件事：
  1. load_brand_names —— 檔案存在時三個 matcher 函式吃到檔案內容
     （模擬客戶臨時新增品牌，零 code 生效）。
  2. Fallback —— 檔案不存在 / JSON 壞掉 / 單筆 schema 錯誤時退回內建表，
     絕不讓 chat turn 掛掉。
  3. mtime 重載 —— 客戶在跑動中的 kiosk 直接改檔，下一次查詢就生效。
  4. Prompt 洩漏迴歸 —— 別名檔是 code 層 metadata，load_reference_text
     絕不能把它串進 [Product Reference]（本設計的核心約束）。
"""

import json
import os

import core.product_matcher as pm
from core.loader import load_reference_text
from core.product_matcher import (
    detect_product_filter,
    find_products_in_text,
    load_brand_names,
    restore_english_names,
)

_IDS = ["starforge_x1", "zephyr_z5"]


def _write_names(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _point_to(monkeypatch, path):
    monkeypatch.setattr(pm, "DEFAULT_ALIASES_PATH", path)


# ── 檔案層生效：三個 matcher 函式 ──────────────────────────────────


def test_new_brand_from_file_drives_all_three_matchers(monkeypatch, tmp_path):
    f = tmp_path / "product_aliases.json"
    _write_names(f, {"zephyr": {"display": "ZephyrBook", "aliases": ["西風"]}})
    _point_to(monkeypatch, f)

    # 查詢路由
    assert detect_product_filter("介紹一下西風Z5", _IDS) == "zephyr_z5"
    # 音譯還原（含 display 大小寫與補空格）
    assert restore_english_names("西風Z5很適合你") == "ZephyrBook Z5很適合你"
    # 挑圖比對
    assert find_products_in_text("推薦 西風 Z5", _IDS) == ["zephyr_z5"]


def test_display_defaults_to_capitalize_when_missing(monkeypatch, tmp_path):
    f = tmp_path / "product_aliases.json"
    _write_names(f, {"zephyr": {"aliases": ["西風"]}})
    _point_to(monkeypatch, f)
    assert restore_english_names("西風Z5") == "Zephyr Z5"


# ── Fallback：壞檔絕不掛 chat ──────────────────────────────────────


def test_missing_file_falls_back_to_builtin_table(monkeypatch, tmp_path):
    _point_to(monkeypatch, tmp_path / "nope.json")
    aliases, display = load_brand_names()
    assert aliases == pm.DEFAULT_BRAND_ALIASES
    assert display == pm.BRAND_DISPLAY
    # 內建表行為照舊
    assert restore_english_names("星鋒 X1 很棒") == "StarForge X1 很棒"


def test_broken_json_falls_back_without_raising(monkeypatch, tmp_path):
    f = tmp_path / "product_aliases.json"
    f.write_text("{not valid json", encoding="utf-8")
    _point_to(monkeypatch, f)
    aliases, _ = load_brand_names()
    assert aliases == pm.DEFAULT_BRAND_ALIASES


def test_bad_entry_skipped_good_entries_survive(monkeypatch, tmp_path):
    f = tmp_path / "product_aliases.json"
    _write_names(f, {
        "zephyr": {"display": "ZephyrBook", "aliases": ["西風"]},
        "broken": {"display": "Broken", "aliases": "不是list"},
    })
    _point_to(monkeypatch, f)
    aliases, _ = load_brand_names()
    assert "zephyr" in aliases and "broken" not in aliases


# ── mtime 重載：跑動中改檔即生效 ───────────────────────────────────


def test_file_edit_reloads_without_restart(monkeypatch, tmp_path):
    f = tmp_path / "product_aliases.json"
    _write_names(f, {"zephyr": {"display": "ZephyrBook", "aliases": ["西風"]}})
    _point_to(monkeypatch, f)
    assert "zephyr" in load_brand_names()[0]

    _write_names(f, {"zephyr": {"display": "ZephyrBook", "aliases": ["西風", "澤菲"]}})
    os.utime(f, (os.stat(f).st_atime, os.stat(f).st_mtime + 1))  # 保證 mtime 前進
    assert "澤菲" in load_brand_names()[0]["zephyr"]


def test_unchanged_file_hits_cache(monkeypatch, tmp_path):
    f = tmp_path / "product_aliases.json"
    _write_names(f, {"zephyr": {"display": "ZephyrBook", "aliases": ["西風"]}})
    _point_to(monkeypatch, f)
    first = load_brand_names()[0]
    assert load_brand_names()[0] is first  # 同 mtime → 同物件，沒重讀


# ── Prompt 洩漏迴歸：別名絕不進 [Product Reference] ────────────────


def test_aliases_json_never_enters_reference_text():
    ref = load_reference_text("./knowledge_base/_reference")
    assert ref, "reference 目錄應該讀得到 CSV"
    assert "product_aliases.json" not in ref
    # 別名字串本身也不能出現（CSV 裡沒有中文暱稱欄）
    for alts in pm.DEFAULT_BRAND_ALIASES.values():
        for alt in alts:
            assert alt not in ref, f"別名 {alt} 洩漏進 reference 注入文字"
