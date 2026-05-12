"""
Eval scorer — turns a (case, answer, retrieval) tuple into per-dimension scores.

Four rule-based dimensions:
  - language      : detected answer language matches expected
  - retrieval     : retrieved chunks contain expected product_id (skipped if expected_product is null)
  - faithfulness  : keyword recall over expected_facts (mode="all" or "any")
  - relevance     : MVP — pass if faithfulness >= 0.5

Guardrail cases short-circuit: if expected_blocked == actual_blocked, all dimensions = 1.0.

Optional LLM-as-judge layer (Phase 2): the runner can attach a `llm_judge` dict
to each CaseResult. aggregate() then surfaces `per_dimension_llm` and
`judge_failures` blocks. The judge itself lives in eval/judge.py; the 1b'
hallucination gate (passed=False when hallucinated_claims is non-empty) is
applied in the runner, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional

from core.generator import _detect_language


PASS_THRESHOLD = 0.5


@dataclass
class CaseResult:
    case_id: str
    category: str
    question: str
    expected_language: str
    detected_language: str
    expected_product: str | None
    retrieved_product_ids: list[str]
    answer: str
    blocked: bool
    expected_blocked: bool
    matched_facts: list[str]
    missing_facts: list[str]
    scores: dict[str, float | None]  # None = N/A (skipped)
    passed: bool
    notes: list[str] = field(default_factory=list)
    llm_judge: Optional[dict] = None  # populated by runner when --llm-judge is set

    def to_dict(self) -> dict:
        return asdict(self)


def _score_faithfulness(answer: str, expected_facts: list[str], match_mode: str) -> tuple[float, list[str], list[str]]:
    """Return (score, matched, missing). Case-insensitive substring match."""
    if not expected_facts:
        return 1.0, [], []

    haystack = answer.lower()
    matched = [f for f in expected_facts if f.lower() in haystack]
    missing = [f for f in expected_facts if f not in matched]

    if match_mode == "any":
        score = 1.0 if matched else 0.0
    else:  # "all"
        score = len(matched) / len(expected_facts)

    return score, matched, missing


def score_case(
    case: dict,
    answer: str,
    retrieved_product_ids: list[str],
    blocked: bool,
) -> CaseResult:
    """Score one case across all dimensions."""
    expected_blocked = bool(case.get("expected_blocked", False))
    expected_lang = case["expected_language"]
    expected_product = case.get("expected_product")
    expected_facts = case.get("expected_facts", [])
    match_mode = case.get("match_mode", "all")

    detected_lang = _detect_language(answer) if answer else expected_lang
    notes: list[str] = []

    # Guardrail short-circuit: blocked-state correctness is the whole test
    if expected_blocked or blocked:
        if expected_blocked == blocked:
            scores = {"language": 1.0, "retrieval": None, "faithfulness": 1.0, "relevance": 1.0}
            notes.append(f"guardrail: expected_blocked={expected_blocked}, actual={blocked} ✓")
            return CaseResult(
                case_id=case["id"],
                category=case.get("category", "uncategorized"),
                question=case["question"],
                expected_language=expected_lang,
                detected_language=detected_lang,
                expected_product=expected_product,
                retrieved_product_ids=retrieved_product_ids,
                answer=answer,
                blocked=blocked,
                expected_blocked=expected_blocked,
                matched_facts=[],
                missing_facts=[],
                scores=scores,
                passed=True,
                notes=notes,
            )
        else:
            notes.append(f"guardrail: expected_blocked={expected_blocked}, actual={blocked} ✗")
            scores = {"language": 0.0, "retrieval": None, "faithfulness": 0.0, "relevance": 0.0}
            return CaseResult(
                case_id=case["id"],
                category=case.get("category", "uncategorized"),
                question=case["question"],
                expected_language=expected_lang,
                detected_language=detected_lang,
                expected_product=expected_product,
                retrieved_product_ids=retrieved_product_ids,
                answer=answer,
                blocked=blocked,
                expected_blocked=expected_blocked,
                matched_facts=[],
                missing_facts=expected_facts,
                scores=scores,
                passed=False,
                notes=notes,
            )

    # 1. Language
    lang_score = 1.0 if detected_lang == expected_lang else 0.0
    if lang_score == 0.0:
        notes.append(f"language: expected {expected_lang}, got {detected_lang}")

    # 2. Retrieval (None = skipped, not counted in avg)
    if expected_product is None:
        retrieval_score: float | None = None
    elif expected_product in retrieved_product_ids:
        retrieval_score = 1.0
    else:
        retrieval_score = 0.0
        notes.append(f"retrieval: expected '{expected_product}' not in {retrieved_product_ids[:5]}")

    # 3. Faithfulness
    faith_score, matched, missing = _score_faithfulness(answer, expected_facts, match_mode)
    if missing and match_mode == "all":
        notes.append(f"faithfulness ({match_mode}): missed {missing}")
    elif faith_score == 0.0 and expected_facts:
        notes.append(f"faithfulness ({match_mode}): none of {expected_facts} matched")

    # 4. Relevance (MVP heuristic)
    relevance_score = 1.0 if faith_score >= PASS_THRESHOLD else 0.0

    scores = {
        "language": lang_score,
        "retrieval": retrieval_score,
        "faithfulness": faith_score,
        "relevance": relevance_score,
    }

    # Pass criterion: every applicable dimension >= threshold
    applicable = [v for v in scores.values() if v is not None]
    passed = all(v >= PASS_THRESHOLD for v in applicable)

    return CaseResult(
        case_id=case["id"],
        category=case.get("category", "uncategorized"),
        question=case["question"],
        expected_language=expected_lang,
        detected_language=detected_lang,
        expected_product=expected_product,
        retrieved_product_ids=retrieved_product_ids,
        answer=answer,
        blocked=blocked,
        expected_blocked=expected_blocked,
        matched_facts=matched,
        missing_facts=missing,
        scores=scores,
        passed=passed,
        notes=notes,
    )


def aggregate(results: list[CaseResult]) -> dict[str, Any]:
    """Compute macro-averages, pass rate, and per-category breakdown."""
    if not results:
        return {"total": 0, "passed": 0, "pass_rate": 0.0, "per_dimension": {}, "per_category": {}}

    dim_keys = ["language", "retrieval", "faithfulness", "relevance"]
    dim_sums: dict[str, list[float]] = {k: [] for k in dim_keys}
    for r in results:
        for k in dim_keys:
            v = r.scores.get(k)
            if v is not None:
                dim_sums[k].append(v)

    per_dimension = {
        k: round(sum(vs) / len(vs), 3) if vs else None
        for k, vs in dim_sums.items()
    }

    # Per-category pass rate
    cat_buckets: dict[str, list[CaseResult]] = {}
    for r in results:
        cat_buckets.setdefault(r.category, []).append(r)
    per_category = {
        cat: {"passed": sum(1 for r in rs if r.passed), "total": len(rs)}
        for cat, rs in cat_buckets.items()
    }

    passed_count = sum(1 for r in results if r.passed)
    summary: dict[str, Any] = {
        "total": len(results),
        "passed": passed_count,
        "pass_rate": round(passed_count / len(results), 3),
        "per_dimension": per_dimension,
        "per_category": per_category,
    }

    # LLM judge aggregates — only present if at least one case ran the judge
    judged = [r for r in results if r.llm_judge is not None]
    if judged:
        faith_scores = [
            r.llm_judge["faithfulness"].get("score")
            for r in judged
            if r.llm_judge.get("error") is None
            and r.llm_judge.get("faithfulness", {}).get("score") is not None
        ]
        rel_scores = [
            r.llm_judge["relevance"].get("score")
            for r in judged
            if r.llm_judge.get("error") is None
            and r.llm_judge.get("relevance", {}).get("score") is not None
        ]
        judge_errors = sum(1 for r in judged if r.llm_judge.get("error") is not None)

        judge_failures = [
            {
                "case_id": r.case_id,
                "hallucinated_claims": r.llm_judge["faithfulness"]["hallucinated_claims"],
            }
            for r in judged
            if r.llm_judge.get("error") is None
            and r.llm_judge["faithfulness"]["hallucinated_claims"]
        ]

        summary["per_dimension_llm"] = {
            "faithfulness": round(sum(faith_scores) / len(faith_scores), 3) if faith_scores else None,
            "relevance": round(sum(rel_scores) / len(rel_scores), 3) if rel_scores else None,
            "hallucination_rate": f"{len(judge_failures)}/{len(judged)}",
            "judge_errors": judge_errors,
        }
        summary["judge_failures"] = judge_failures

    return summary
