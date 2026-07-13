"""Batch-eval endpoints: run the editor graph across golden-set cases, and list
available cases for the batch-scope UI."""

import asyncio
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from api.eval_service import _load_golden_set_cases, _select_cases, run_batch
from api.schemas import BatchEvalRequest, EvalReportRequest

router = APIRouter()

_BATCH_EVAL_TIMEOUT_S = 600  # 10 分鐘上限，避免單請求 block worker 無限久


@router.post("/api/eval/batch")
async def batch_eval(req: BatchEvalRequest):
    """Run the editor graph once per selected golden_set case, harvest metrics
    from coverage/score_distribution/diversity/facts_coverage nodes, return
    per-case results plus aggregate (macro, per-category, worst-K).

    Requires the graph to contain an eval_case_loader — its case_id param is
    overridden per iteration. Other node params are preserved as-is.

    Bounds: graph ≤ 100 nodes, cases ≤ 50, worst_k ≤ 20, timeout 600s.
    """
    from core.eval_metrics import aggregate_batch

    nodes = [n.model_dump() for n in req.graph.nodes]
    edges = [e.model_dump() for e in req.graph.edges]
    if not nodes:
        raise HTTPException(status_code=400, detail="Graph has no nodes")

    loader_node = next((n for n in nodes if n.get("type") == "eval_case_loader"), None)
    if loader_node is None:
        raise HTTPException(
            status_code=400,
            detail="Graph must contain an eval_case_loader node",
        )

    selected = _select_cases(req.scope)
    if not selected:
        return {
            "per_case": [],
            "aggregate": aggregate_batch([], worst_k=req.worst_k),
            "skipped": [],
        }
    if len(selected) > 50:
        raise HTTPException(status_code=400, detail="too many cases selected (max 50)")

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(run_batch, nodes, edges, loader_node, selected, req.worst_k),
            timeout=_BATCH_EVAL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        raise HTTPException(
            status_code=504,
            detail=f"batch eval exceeded {_BATCH_EVAL_TIMEOUT_S}s timeout",
        )


@router.post("/api/eval/report")
def eval_report(req: EvalReportRequest):
    """Render a downloadable, human-readable report (HTML or Markdown) from an
    already-computed batch result posted back by the UI. Reuses the exact
    builders behind the `eval/report.py` CLI so the file matches that output.

    Returns the report as an attachment so the browser downloads it directly.
    """
    from eval.report import build_html, build_markdown

    graph = {
        "nodes": [n.model_dump() for n in req.graph.nodes],
        "edges": [e.model_dump() for e in req.graph.edges],
    }
    when = datetime.now().strftime("%Y-%m-%d %H:%M")
    stamp = datetime.now().strftime("%Y%m%d_%H%M")

    if req.fmt == "md":
        body = build_markdown(req.result, graph, req.graph_name, when, req.elapsed_s)
        media_type = "text/markdown; charset=utf-8"
        filename = f"eval_report_{stamp}.md"
    else:
        body = build_html(req.result, graph, req.graph_name, when, req.elapsed_s)
        media_type = "text/html; charset=utf-8"
        filename = f"eval_report_{stamp}.html"

    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


@router.get("/api/eval/cases")
def get_golden_set_cases():
    """List all golden_set cases (id + category) for the batch-scope UI."""
    cases = _load_golden_set_cases()
    return [
        {"id": c.get("id"), "category": c.get("category") or "uncategorized"}
        for c in cases
    ]
