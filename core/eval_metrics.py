"""
Retrieval quality metrics — pure compute helpers for the Editor eval nodes.

Mirrors the algorithms used in `eval/scorer.py` so node output matches CLI
eval (`python -m eval.runner`). Each function takes a list of
`RetrievalResult` from `core.vector_store` and returns a metric dict with
shape:

    {
        "name": <str>,
        "score": <float | None>,   # 0.0–1.0 normalized; None when N/A
        "details": <dict>,         # metric-specific detail
    }

`facts_coverage` reuses the substring + match_mode algorithm from
`eval/scorer.py::_score_faithfulness`; the formula is duplicated here (not
imported) to keep `core/` free of `eval/` dependencies.
"""

from __future__ import annotations

import math
from collections import Counter

from core.vector_store import RetrievalResult


def _product_ids(results: list[RetrievalResult], top_k: int) -> list[str]:
    return [
        (r.chunk.metadata.get("product_id") or "")
        for r in results[:top_k]
    ]


def compute_coverage(
    results: list[RetrievalResult],
    expected_product: str,
    top_k: int,
) -> dict:
    """Hit@K — did expected_product appear in top_k, and at which rank?

    Mirrors `eval/scorer.py::score_case` Retrieval dimension: 1.0 if
    expected_product appears in retrieved product_ids, else 0.0. `rank` is
    the 1-indexed position of the first match (None if missed). When
    expected_product is empty, treat as N/A (score=None).
    """
    expected = (expected_product or "").strip()
    retrieved = _product_ids(results, top_k)

    if not expected:
        return {
            "name": "coverage",
            "score": None,
            "details": {
                "expected_product": "",
                "retrieved_products": retrieved,
                "top_k": top_k,
                "note": "expected_product not provided — coverage N/A",
            },
        }

    hit = expected in retrieved
    rank = (retrieved.index(expected) + 1) if hit else None

    return {
        "name": "coverage",
        "score": 1.0 if hit else 0.0,
        "details": {
            "expected_product": expected,
            "hit": hit,
            "rank": rank,
            "top_k": top_k,
            "retrieved_products": retrieved,
        },
    }


def compute_score_distribution(
    results: list[RetrievalResult],
    top_k: int,
) -> dict:
    """Top-K retrieval score statistics. No ground truth needed.

    `score` is None — this metric is descriptive, not pass/fail. Use
    `details.top1` and `details.gap_top1_topk` to diagnose "all noise"
    retrieval where every chunk scores low.
    """
    scores = [float(r.score) for r in results[:top_k]]

    if not scores:
        return {
            "name": "score_distribution",
            "score": None,
            "details": {"count": 0, "note": "no results"},
        }

    n = len(scores)
    mean = sum(scores) / n
    var = sum((s - mean) ** 2 for s in scores) / n
    std = math.sqrt(var)
    top1 = scores[0]
    topk = scores[-1]

    return {
        "name": "score_distribution",
        "score": None,
        "details": {
            "count": n,
            "min": min(scores),
            "max": max(scores),
            "mean": mean,
            "std": std,
            "top1": top1,
            "topk": topk,
            "gap_top1_topk": top1 - topk,
            "scores": [round(s, 4) for s in scores],
        },
    }


def compute_diversity(
    results: list[RetrievalResult],
    top_k: int,
) -> dict:
    """How many distinct product_ids appear in top-K, and Shannon entropy.

    Useful for comparison queries: if entropy is near zero, retriever is
    dominated by one product even though the question covers many.
    `score` is normalized entropy / log2(top_k) ∈ [0, 1].
    """
    pids = _product_ids(results, top_k)
    valid = [p for p in pids if p]

    if not valid:
        return {
            "name": "diversity",
            "score": None,
            "details": {
                "count": 0,
                "note": "no retrieved chunks have product_id metadata",
                "distribution": {},
            },
        }

    counts = Counter(valid)
    total = sum(counts.values())
    probs = [c / total for c in counts.values()]
    entropy = -sum(p * math.log2(p) for p in probs if p > 0)

    # Normalize against the max possible entropy for this top_k (uniform).
    max_entropy = math.log2(top_k) if top_k > 1 else 1.0
    normalized = entropy / max_entropy if max_entropy > 0 else 0.0

    dominant_pid, dominant_count = counts.most_common(1)[0]

    return {
        "name": "diversity",
        "score": min(normalized, 1.0),
        "details": {
            "unique_products": len(counts),
            "distribution": dict(counts),
            "entropy": entropy,
            "entropy_normalized": normalized,
            "dominant_pid": dominant_pid,
            "dominant_share": dominant_count / total,
            "top_k": top_k,
        },
    }


def aggregate_batch(per_case: list[dict], worst_k: int = 3) -> dict:
    """Aggregate per-case metric dicts into macro / per-category / worst-K.

    Input shape (one entry per case):
        {
            "case_id": str,
            "category": str,
            "metrics": {
                "coverage": <metric dict | None>,
                "score_distribution": <metric dict | None>,
                "diversity": <metric dict | None>,
                "facts_coverage": <metric dict | None>,
            }
        }

    Returns:
        {
            "macro": {<metric_name>: {"mean": float, "n": int}},   # None scores skipped
            "per_category": {<category>: {<metric_name>: {"mean", "n"}}},
            "worst_k": [
                {"case_id", "category", "composite_score": float, "missing_metrics": list}
            ],
            "total_cases": int,
        }

    Composite score (for worst-K ranking) is the mean of non-None metric scores
    on that case. Cases with no scoring metrics get composite=None and sort last.
    """
    metric_names = ["coverage", "score_distribution", "diversity", "facts_coverage"]

    def _macro(cases: list[dict]) -> dict:
        out = {}
        for name in metric_names:
            scores = []
            for c in cases:
                m = (c.get("metrics") or {}).get(name) or {}
                s = m.get("score")
                if s is not None:
                    scores.append(s)
            if scores:
                out[name] = {"mean": sum(scores) / len(scores), "n": len(scores)}
            else:
                out[name] = {"mean": None, "n": 0}
        return out

    macro = _macro(per_case)

    by_cat: dict[str, list[dict]] = {}
    for c in per_case:
        cat = c.get("category") or "uncategorized"
        by_cat.setdefault(cat, []).append(c)
    per_category = {cat: _macro(cases) for cat, cases in by_cat.items()}

    composite_rows = []
    for c in per_case:
        metrics = c.get("metrics") or {}
        scores = [(metrics.get(n) or {}).get("score") for n in metric_names]
        non_null = [s for s in scores if s is not None]
        missing = [
            n for n in metric_names
            if (metrics.get(n) or {}).get("score") is None
        ]
        composite_rows.append({
            "case_id": c.get("case_id"),
            "category": c.get("category"),
            "composite_score": (sum(non_null) / len(non_null)) if non_null else None,
            "missing_metrics": missing,
        })

    # Sort ascending by composite (worst first); None composites sink to end.
    composite_rows.sort(
        key=lambda r: (r["composite_score"] is None, r["composite_score"] or 0.0)
    )
    worst = composite_rows[:worst_k]

    return {
        "macro": macro,
        "per_category": per_category,
        "worst_k": worst,
        "total_cases": len(per_case),
    }


def compute_facts_coverage(
    results: list[RetrievalResult],
    expected_facts: list[str],
    match_mode: str,
) -> dict:
    """Keyword recall of expected_facts against concatenated retrieved text.

    Algorithm mirrors `eval/scorer.py::_score_faithfulness`:
      - case-insensitive substring match
      - match_mode == "any" → 1.0 if any fact matches, else 0.0
      - match_mode == "all" → matched / total

    The retrieval-level analogue: do the answer-supporting facts even
    APPEAR in what we retrieved? If not, the LLM has to hallucinate or fail.
    """
    facts = [f.strip() for f in (expected_facts or []) if f and f.strip()]

    if not facts:
        return {
            "name": "facts_coverage",
            "score": None,
            "details": {
                "mode": match_mode,
                "matched": [],
                "missing": [],
                "note": "no expected_facts provided",
            },
        }

    haystack = "\n".join(r.chunk.text for r in results).lower()

    matched = [f for f in facts if f.lower() in haystack]
    missing = [f for f in facts if f not in matched]

    if match_mode == "any":
        score = 1.0 if matched else 0.0
    else:  # "all"
        score = len(matched) / len(facts)

    return {
        "name": "facts_coverage",
        "score": score,
        "details": {
            "mode": match_mode,
            "matched": matched,
            "missing": missing,
            "total_facts": len(facts),
            "matched_count": len(matched),
        },
    }
