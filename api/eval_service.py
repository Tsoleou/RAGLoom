"""
Batch-eval helpers: golden-set loading, case selection, metric harvesting, and
the per-case graph-run core. The async wrapper (timeout / to_thread) stays in
the router; this module holds the pure logic.

`_GOLDEN_SET_PATH_DEFAULT` is CWD-relative (repo root), same as before the split.
"""

import json
from pathlib import Path

from fastapi import HTTPException

from api.engine import execute_graph
from api.schemas import BatchEvalScope

_GOLDEN_SET_PATH_DEFAULT = Path("eval/golden_set.json")


def _load_golden_set_cases() -> list[dict]:
    if not _GOLDEN_SET_PATH_DEFAULT.exists():
        return []
    try:
        data = json.loads(_GOLDEN_SET_PATH_DEFAULT.read_text())
    except Exception as e:
        print(f"[BatchEval] Failed to load golden set: {e}")
        return []
    return data.get("cases") or []


def _select_cases(scope: BatchEvalScope) -> list[dict]:
    cases = _load_golden_set_cases()
    mode = (scope.mode or "all").lower()
    if mode == "all":
        return cases
    if mode == "category":
        cat = scope.category or ""
        return [c for c in cases if (c.get("category") or "") == cat]
    if mode == "ids":
        wanted = set(scope.case_ids or [])
        return [c for c in cases if c.get("id") in wanted]
    raise HTTPException(status_code=400, detail=f"Unknown scope mode: {scope.mode}")


_METRIC_NODE_TYPES = {
    "coverage_metric": "coverage",
    "score_distribution_metric": "score_distribution",
    "diversity_metric": "diversity",
    "facts_coverage_metric": "facts_coverage",
}


def _harvest_metrics(nodes: list[dict], outputs: dict) -> dict:
    """For each metric node type present in the graph, pull its first occurrence's
    `metric` output. Metric nodes the user didn't include are simply absent."""
    harvested: dict = {key: None for key in _METRIC_NODE_TYPES.values()}
    for n in nodes:
        ntype = n.get("type")
        key = _METRIC_NODE_TYPES.get(ntype)
        if key is None or harvested.get(key) is not None:
            continue
        node_out = outputs.get(n["id"]) or {}
        metric = node_out.get("metric")
        if isinstance(metric, dict):
            harvested[key] = metric
    return harvested


def run_batch(
    nodes: list[dict],
    edges: list[dict],
    loader_node: dict,
    selected: list[dict],
    worst_k: int,
) -> dict:
    """Run the editor graph once per selected case, harvest metrics, aggregate.

    Synchronous core — the router runs this in a thread under a timeout.
    """
    from copy import deepcopy
    from core.eval_metrics import aggregate_batch

    per_case = []
    skipped = []
    loader_id = loader_node["id"]
    for case in selected:
        case_id = case.get("id")
        case_nodes = deepcopy(nodes)
        for n in case_nodes:
            if n.get("id") == loader_id:
                params = dict(n.get("params") or {})
                params["case_id"] = case_id
                n["params"] = params
                break

        try:
            _node_results, outputs = execute_graph(
                case_nodes, edges, return_outputs=True
            )
        except Exception as e:
            skipped.append({"case_id": case_id, "reason": f"graph error: {e}"})
            continue

        metrics = _harvest_metrics(case_nodes, outputs)
        per_case.append({
            "case_id": case_id,
            "category": case.get("category") or "uncategorized",
            "metrics": metrics,
        })

    return {
        "per_case": per_case,
        "aggregate": aggregate_batch(per_case, worst_k=worst_k),
        "skipped": skipped,
    }
