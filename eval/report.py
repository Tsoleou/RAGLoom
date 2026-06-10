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
from html import escape
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
    L.append("# RAGLoom Eval Report")
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


# --- HTML report -----------------------------------------------------

_CSS = """
:root { color-scheme: light dark; }
body { font: 15px/1.5 -apple-system, Segoe UI, Roboto, sans-serif; max-width: 1000px;
       margin: 2rem auto; padding: 0 1.2rem; color: #1c1c1e; background: #fff; }
h1 { font-size: 1.6rem; margin-bottom: .2rem; }
h2 { font-size: 1.15rem; margin-top: 2rem; border-bottom: 2px solid #eee; padding-bottom: .3rem; }
.meta { color: #555; font-size: .92rem; }
.meta code { background: #f2f2f4; padding: .05rem .35rem; border-radius: 4px; }
table { border-collapse: collapse; width: 100%; margin: .6rem 0; font-size: .9rem; }
th, td { border: 1px solid #e3e3e6; padding: .35rem .6rem; text-align: left; }
th { background: #f7f7f9; font-weight: 600; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .86em; }
.g { background: #e3f4e8; } .a { background: #fcf3da; } .r { background: #fbe3e3; } .n { background: #f4f4f5; color: #999; }
.note { color: #777; font-size: .86rem; font-style: italic; margin: .3rem 0 0; }
"""


def _cls(v):
    if not isinstance(v, (int, float)):
        return "n"
    return "g" if v >= 0.8 else ("a" if v >= 0.5 else "r")


def _cell(v, nd=2, suffix=""):
    """A colour-coded numeric <td> for a 0–1 score."""
    return f'<td class="num {_cls(v)}">{_fmt(v, nd)}{suffix if isinstance(v,(int,float)) else ""}</td>'


def build_html(resp: dict, graph: dict, graph_name: str, when: str, elapsed_s: float) -> str:
    per_case = resp.get("per_case") or []
    agg = resp.get("aggregate") or {}
    skipped = resp.get("skipped") or []
    macro = agg.get("macro") or {}
    per_cat = agg.get("per_category") or {}
    worst = agg.get("worst_k") or []
    cfg = _graph_config(graph)

    H: list[str] = []
    H.append("<!doctype html><html><head><meta charset='utf-8'>")
    H.append("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    H.append(f"<title>RAGLoom Eval Report — {escape(when)}</title>")
    H.append(f"<style>{_CSS}</style></head><body>")
    H.append("<h1>RAGLoom Eval Report</h1>")
    H.append(
        f"<p class='meta'>Generated <b>{escape(when)}</b> · Graph <code>{escape(graph_name)}</code> · "
        f"Embedder <code>{escape(str(cfg['embedding_model']))}</code> · top_k {escape(str(cfg['top_k']))} · "
        f"Rerank <code>{escape(str(cfg['rerank_model']))}</code><br>"
        f"{len(per_case)} cases run, {len(skipped)} skipped · {elapsed_s:.0f}s</p>"
    )

    # Macro
    H.append("<h2>Macro (scalar metrics)</h2><table>")
    H.append("<tr><th>Metric</th><th>Mean</th><th>n</th></tr>")
    for key in ("coverage", "diversity", "facts_coverage"):
        m = macro.get(key) or {}
        H.append(f"<tr><td>{key}</td>{_cell(m.get('mean'), 3)}<td class='num'>{m.get('n', 0)}</td></tr>")
    H.append("</table>")
    H.append("<p class='note'>score_distribution is descriptive (no pass/fail score) — see Retrieval health.</p>")

    # Retrieval health
    top1s = [v for v in (_metric_detail(c.get("metrics"), "score_distribution", "top1") for c in per_case) if isinstance(v, (int, float))]
    gaps = [v for v in (_metric_detail(c.get("metrics"), "score_distribution", "gap_top1_topk") for c in per_case) if isinstance(v, (int, float))]
    means = [v for v in (_metric_detail(c.get("metrics"), "score_distribution", "mean") for c in per_case) if isinstance(v, (int, float))]
    H.append("<h2>Retrieval health (from score_distribution)</h2>")
    if top1s:
        H.append("<table><tr><th>Stat</th><th>Mean across cases</th></tr>")
        H.append(f"<tr><td>top-1 score</td><td class='num'>{_fmt(mean(top1s))}</td></tr>")
        H.append(f"<tr><td>top-K mean score</td><td class='num'>{_fmt(mean(means)) if means else '—'}</td></tr>")
        H.append(f"<tr><td>top1−topK gap</td><td class='num'>{_fmt(mean(gaps)) if gaps else '—'}</td></tr>")
        H.append("</table>")
        H.append("<p class='note'>Low top-1 with a small gap = \"all noise\" retrieval (every chunk scores alike).</p>")
    else:
        H.append("<p class='note'>No score_distribution data.</p>")

    # Per category
    H.append("<h2>Per category</h2><table>")
    H.append("<tr><th>Category</th><th>Coverage</th><th>Facts</th><th>Diversity</th></tr>")
    for cat, mets in sorted(per_cat.items()):
        cov = (mets.get("coverage") or {}).get("mean")
        fc = (mets.get("facts_coverage") or {}).get("mean")
        dv = (mets.get("diversity") or {}).get("mean")
        H.append(f"<tr><td>{escape(cat)}</td>{_cell(cov)}{_cell(fc)}{_cell(dv)}</tr>")
    H.append("</table>")
    H.append("<p class='note'>Greyed cells for refusal/guardrail/scope are by design — they test gating, not fact retrieval.</p>")

    # Worst-K
    if worst:
        H.append(f"<h2>Worst {len(worst)} cases (by composite score)</h2><table>")
        H.append("<tr><th>Case</th><th>Category</th><th>Composite</th><th>Missing metrics</th></tr>")
        for w in worst:
            mm = escape(", ".join(w.get("missing_metrics") or []) or "—")
            H.append(
                f"<tr><td><code>{escape(str(w.get('case_id')))}</code></td><td>{escape(str(w.get('category')))}</td>"
                f"{_cell(w.get('composite_score'), 3)}<td>{mm}</td></tr>"
            )
        H.append("</table>")

    # All cases
    H.append("<h2>All cases</h2><table>")
    H.append("<tr><th>Case</th><th>Category</th><th>Coverage</th><th>Facts</th><th>Diversity</th><th>top-1</th><th>gap</th></tr>")
    for c in sorted(per_case, key=lambda x: (x.get("category") or "", x.get("case_id") or "")):
        m = c.get("metrics") or {}
        cov_s = _metric_score(m, "coverage")
        hit = _metric_detail(m, "coverage", "hit")
        rank = _metric_detail(m, "coverage", "rank")
        cov_txt = "—" if cov_s is None else f"{_fmt(cov_s,2)} ({'hit #'+str(rank) if hit else 'miss'})"
        H.append(
            f"<tr><td><code>{escape(str(c.get('case_id')))}</code></td><td>{escape(str(c.get('category')))}</td>"
            f"<td class='num {_cls(cov_s)}'>{cov_txt}</td>"
            f"{_cell(_metric_score(m,'facts_coverage'))}{_cell(_metric_score(m,'diversity'))}"
            f"<td class='num'>{_fmt(_metric_detail(m,'score_distribution','top1'),2)}</td>"
            f"<td class='num'>{_fmt(_metric_detail(m,'score_distribution','gap_top1_topk'),2)}</td></tr>"
        )
    H.append("</table>")

    if skipped:
        H.append("<h2>Skipped</h2><ul>")
        for s in skipped:
            H.append(f"<li><code>{escape(str(s.get('case_id')))}</code> — {escape(str(s.get('reason')))}</li>")
        H.append("</ul>")

    H.append("</body></html>")
    return "\n".join(H)


# --- main ------------------------------------------------------------


def main():
    import time

    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", default="config/profiles/retrieval_eval.json")
    ap.add_argument("--out", default=None, help="output path; extension overrides --format for a single file")
    ap.add_argument("--format", choices=["html", "md", "both"], default="html")
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
    builders = {"md": build_markdown, "html": build_html}

    # Decide which formats + paths to write.
    if args.out:
        ext = Path(args.out).suffix.lstrip(".").lower()
        if args.format == "both":
            base = Path(args.out).with_suffix("")
            targets = [(f, base.with_suffix("." + f)) for f in ("md", "html")]
        else:
            fmt = ext if ext in builders else args.format
            targets = [(fmt, Path(args.out))]
    else:
        stamp = f"{datetime.now():%Y%m%d_%H%M}"
        fmts = ["md", "html"] if args.format == "both" else [args.format]
        targets = [(f, Path("eval_results") / f"eval_report_{stamp}.{f}") for f in fmts]

    n = len(resp.get("per_case") or [])
    for fmt, out in targets:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(builders[fmt](resp, graph, graph_path.stem, when, elapsed))
        print(f">>> wrote {fmt} report: {out}  ({n} cases, {elapsed:.0f}s)")


if __name__ == "__main__":
    main()
