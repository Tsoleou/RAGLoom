"""
比較不同 embedder 的 eval 報告 — 以「檢索命中率」為主軸、按語言拆開。

每份 eval_results/*.json 已內含 run.embedding_model 與每個 case 的
expected_language + scores.retrieval，所以這支只讀報告、不碰 pipeline，
也不必 join golden_set。

用法：
    python -m eval.compare_embedders                       # 自動抓每個 embedder 最新一份
    python -m eval.compare_embedders a.json b.json c.json  # 指定報告
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "eval_results"


def load_report(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def pick_latest_per_embedder() -> list[Path]:
    """每個 embedding_model 取 mtime 最新的一份報告。

    回傳的第一份即 baseline：固定優先 nomic-embed-text，否則退回 mtime 最舊那份，
    以免「最近重跑了哪個 model」意外改掉 baseline（mtime 不可控）。其餘維持 mtime 排序。
    """
    latest: dict[str, tuple[float, Path]] = {}
    for p in RESULTS_DIR.glob("eval_*.json"):
        try:
            model = load_report(p)["run"]["embedding_model"]
        except (KeyError, json.JSONDecodeError):
            continue
        mtime = p.stat().st_mtime
        if model not in latest or mtime > latest[model][0]:
            latest[model] = (mtime, p)

    def sort_key(item: tuple[str, tuple[float, Path]]) -> tuple[bool, float]:
        model, (mtime, _path) = item
        return (not model.lower().startswith("nomic"), mtime)

    return [p for _, (_, p) in sorted(latest.items(), key=sort_key)]


def retrieval_stats(results: list[dict]) -> dict[str, tuple[int, int]]:
    """回傳 {scope: (hit, total)}，scope ∈ {all, English, Chinese}。
    只計 scores.retrieval 非 None 的 case（有 expected_product 才算檢索測試）。"""
    buckets: dict[str, list[float]] = {"all": [], "English": [], "Chinese": []}
    for r in results:
        score = r["scores"].get("retrieval")
        if score is None:
            continue
        buckets["all"].append(score)
        lang = r.get("expected_language")
        if lang in buckets:
            buckets[lang].append(score)
    return {k: (int(sum(v)), len(v)) for k, v in buckets.items()}


def fmt_hit(hit: int, total: int) -> str:
    if total == 0:
        return "  n/a "
    return f"{hit:>2}/{total:<2} {hit/total*100:>3.0f}%"


def main() -> None:
    paths = [Path(a) for a in sys.argv[1:]] or pick_latest_per_embedder()
    if len(paths) < 2:
        print("需要至少 2 份報告才能比較。先跑 eval。", file=sys.stderr)
        sys.exit(1)

    reports = [(load_report(p)["run"]["embedding_model"], load_report(p)) for p in paths]

    # ── 對照表 ──────────────────────────────────────────────
    bar = "=" * 78
    print(bar)
    print("  Embedder 比較 — retrieval-hit 為主軸（只計有 expected_product 的 case）")
    print(bar)
    name_w = max(len(m) for m, _ in reports) + 2
    header = f"  {'embedder':<{name_w}} {'pass_rate':>10} {'retr/all':>11} {'retr/EN':>11} {'retr/中文':>12} {'faith':>7}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for model, rep in reports:
        rs = retrieval_stats(rep["results"])
        pr = rep["summary"]["pass_rate"]
        faith = rep["summary"]["per_dimension"].get("faithfulness")
        faith_s = "n/a" if faith is None else f"{faith:.2f}"
        print(
            f"  {model:<{name_w}} {pr*100:>9.0f}% "
            f"{fmt_hit(*rs['all']):>11} {fmt_hit(*rs['English']):>11} "
            f"{fmt_hit(*rs['Chinese']):>12} {faith_s:>7}"
        )
    print()

    # ── baseline miss → 新 embedder hit（以第一份為 baseline）──
    base_model, base_rep = reports[0]
    base_retr = {
        r["case_id"]: r["scores"].get("retrieval")
        for r in base_rep["results"]
    }
    print(f"  以 '{base_model}' 為 baseline，列出 baseline 漏掉但其他 embedder 救回的 case：")
    any_gain = False
    for model, rep in reports[1:]:
        gains = []
        for r in rep["results"]:
            cid = r["case_id"]
            new = r["scores"].get("retrieval")
            old = base_retr.get(cid)
            if old == 0.0 and new == 1.0:
                lang = r.get("expected_language", "?")
                gains.append(f"{cid} [{lang}]")
        if gains:
            any_gain = True
            print(f"    + {model}:")
            for g in gains:
                print(f"        {g}")
        else:
            print(f"    {model}: 無新增命中")
    if not any_gain:
        print("    （沒有任何 case 從 miss 變 hit）")
    print(bar)


if __name__ == "__main__":
    main()
