"""
Eval report generator.

Runs the batch eval over the full golden set using a chosen graph (default:
the retrieval_eval profile) and writes a self-contained Markdown report to
eval_results/.

Usage:
    source venv/bin/activate
    python eval/report.py                         # uses config/profiles/retrieval_eval.json
    python eval/report.py --graph path/to/g.json  # any saved {nodes,edges} graph
    python eval/report.py --out eval_results/my_report.md

The report is built from a single /api/eval/batch response, so it always
matches what the live endpoint produces. score_distribution has no scalar
score (it's descriptive), so it is surfaced as a retrieval-health section
rather than in the macro averages.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean

# Allow `python eval/report.py` from the repo root (script dir, not CWD, is on
# sys.path by default). CWD must still be the repo root for data paths.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

# --- helpers ---------------------------------------------------------


def _fmt(v, nd=3):
    if isinstance(v, (int, float)):
        return f"{v:.{nd}f}"
    return "—" if v is None else str(v)


def _metric_score(metrics: dict, key: str):
    m = (metrics or {}).get(key)
    return m.get("score") if isinstance(m, dict) else None


def _metric_detail(metrics: dict, key: str, field):
    m = (metrics or {}).get(key)
    if not isinstance(m, dict):
        return None
    return (m.get("details") or {}).get(field)


def _graph_config(graph: dict) -> dict:
    cfg = {"embedding_model": "—", "top_k": "—", "rerank_model": "—"}
    for n in graph.get("nodes", []):
        p = n.get("params") or {}
        if n.get("type") == "retriever":
            cfg["embedding_model"] = p.get("embedding_model", cfg["embedding_model"])
            cfg["top_k"] = p.get("top_k", cfg["top_k"])
        if n.get("type") == "retrieval_judge":
            cfg["rerank_model"] = p.get("model", cfg["rerank_model"])
    return cfg


# --- report builder --------------------------------------------------


def build_markdown(resp: dict, graph: dict, graph_name: str, when: str, elapsed_s: float) -> str:
    per_case = resp.get("per_case") or []
    agg = resp.get("aggregate") or {}
    skipped = resp.get("skipped") or []
    macro = agg.get("macro") or {}
    per_cat = agg.get("per_category") or {}
    worst = agg.get("worst_k") or []
    cfg = _graph_config(graph)

    L: list[str] = []
    L.append(f"# RAGLoom Eval Report")
    L.append("")
    L.append(f"- **Generated:** {when}")
    L.append(f"- **Graph:** `{graph_name}`")
    L.append(f"- **Embedder:** `{cfg['embedding_model']}`  |  **top_k:** {cfg['top_k']}  |  **Rerank:** `{cfg['rerank_model']}`")
    L.append(f"- **Cases:** {len(per_case)} run, {len(skipped)} skipped  |  **Wall time:** {elapsed_s:.0f}s")
    L.append("")

    # Macro
    L.append("## Macro (scalar metrics)")
    L.append("")
    L.append("| Metric | Mean | n |")
    L.append("|---|---|---|")
    for key in ("coverage", "diversity", "facts_coverage"):
        m = macro.get(key) or {}
        L.append(f"| {key} | {_fmt(m.get('mean'))} | {m.get('n', 0)} |")
    L.append("")
    L.append("> `score_distribution` is descriptive (no pass/fail score) — see **Retrieval health** below.")
    L.append("")

    # Retrieval health from score_distribution details
    top1s = [v for v in (_metric_detail(c.get("metrics"), "score_distribution", "top1") for c in per_case) if isinstance(v, (int, float))]
    gaps = [v for v in (_metric_detail(c.get("metrics"), "score_distribution", "gap_top1_topk") for c in per_case) if isinstance(v, (int, float))]
    means = [v for v in (_metric_detail(c.get("metrics"), "score_distribution", "mean") for c in per_case) if isinstance(v, (int, float))]
    L.append("## Retrieval health (from score_distribution)")
    L.append("")
    if top1s:
        L.append("| Stat | Mean across cases |")
        L.append("|---|---|")
        L.append(f"| top-1 score | {_fmt(mean(top1s))} |")
        L.append(f"| top-K mean score | {_fmt(mean(means)) if means else '—'} |")
        L.append(f"| top1−topK gap | {_fmt(mean(gaps)) if gaps else '—'} |")
        L.append("")
        L.append("> Low top-1 with a small gap = \"all noise\" retrieval (every chunk scores alike).")
    else:
        L.append("_No score_distribution data (no score_distribution_metric node in graph)._")
    L.append("")

    # Per category
    L.append("## Per category")
    L.append("")
    L.append("| Category | Coverage | Facts | Diversity |")
    L.append("|---|---|---|---|")
    for cat, mets in sorted(per_cat.items()):
        cov = (mets.get("coverage") or {}).get("mean")
        fc = (mets.get("facts_coverage") or {}).get("mean")
        dv = (mets.get("diversity") or {}).get("mean")
        L.append(f"| {cat} | {_fmt(cov, 2)} | {_fmt(fc, 2)} | {_fmt(dv, 2)} |")
    L.append("")
    L.append("> `—` for refusal/guardrail/scope categories is by design — they test gating, not fact retrieval.")
    L.append("")

    # Worst-K
    if worst:
        L.append(f"## Worst {len(worst)} cases (by composite score)")
        L.append("")
        L.append("| Case | Category | Composite | Missing metrics |")
        L.append("|---|---|---|---|")
        for w in worst:
            mm = ", ".join(w.get("missing_metrics") or []) or "—"
            L.append(f"| `{w.get('case_id')}` | {w.get('category')} | {_fmt(w.get('composite_score'))} | {mm} |")
        L.append("")

    # Per-case detail
    L.append("## All cases")
    L.append("")
    L.append("| Case | Category | Coverage | Facts | Diversity | top-1 | gap |")
    L.append("|---|---|---|---|---|---|---|")
    for c in sorted(per_case, key=lambda x: (x.get("category") or "", x.get("case_id") or "")):
        m = c.get("metrics") or {}
        cov_s = _metric_score(m, "coverage")
        hit = _metric_detail(m, "coverage", "hit")
        rank = _metric_detail(m, "coverage", "rank")
        cov_str = "—" if cov_s is None else (f"{_fmt(cov_s,2)} ({'hit #'+str(rank) if hit else 'miss'})")
        L.append(
            f"| `{c.get('case_id')}` | {c.get('category')} | {cov_str} | "
            f"{_fmt(_metric_score(m,'facts_coverage'),2)} | {_fmt(_metric_score(m,'diversity'),2)} | "
            f"{_fmt(_metric_detail(m,'score_distribution','top1'),2)} | "
            f"{_fmt(_metric_detail(m,'score_distribution','gap_top1_topk'),2)} |"
        )
    L.append("")

    if skipped:
        L.append("## Skipped")
        L.append("")
        for s in skipped:
            L.append(f"- `{s.get('case_id')}` — {s.get('reason')}")
        L.append("")

    return "\n".join(L)


# --- main ------------------------------------------------------------


def main():
    import time

    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="config/profiles/retrieval_eval.json")
    ap.add_argument("--out", default=None)
    ap.add_argument("--worst-k", type=int, default=10)
    args = ap.parse_args()

    graph_path = Path(args.graph)
    graph = json.loads(graph_path.read_text())

    from api import server
    server._settings.api_local_token = ""  # in-process test client; no auth
    client = TestClient(server.app)

    payload = {
        "graph": {"nodes": graph["nodes"], "edges": graph["edges"]},
        "scope": {"mode": "all"},
        "worst_k": args.worst_k,
    }
    t0 = time.time()
    r = client.post("/api/eval/batch", json=payload)
    elapsed = time.time() - t0
    r.raise_for_status()
    resp = r.json()

    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    md = build_markdown(resp, graph, graph_path.stem, when, elapsed)

    out = Path(args.out) if args.out else Path("eval_results") / f"eval_report_{datetime.now():%Y%m%d_%H%M}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    print(f"\n>>> wrote report: {out}  ({len(resp.get('per_case') or [])} cases, {elapsed:.0f}s)")


if __name__ == "__main__":
    main()
