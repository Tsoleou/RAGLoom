"""
RAGLoom eval runner — load golden_set.json, run pipeline, score, report.

Usage:
    python -m eval.runner                              # all cases
    python -m eval.runner --category language_test     # filter by category
    python -m eval.runner --case starforge_x1_gpu_en   # single case (debug)
    python -m eval.runner --skip-ingest                # reuse existing chroma_db (faster reruns)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from core.guardrail import check_query as guardrail_check
from core.pipeline import RAGPipeline

from eval.scorer import CaseResult, aggregate, score_case


REPO_ROOT = Path(__file__).resolve().parent.parent
GOLDEN_SET_PATH = REPO_ROOT / "eval" / "golden_set.json"
KB_PATH = REPO_ROOT / "knowledge_base"
RESULTS_DIR = REPO_ROOT / "eval_results"


def load_golden_set(path: Path = GOLDEN_SET_PATH) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["cases"]


def filter_cases(cases: list[dict], category: str | None, case_id: str | None) -> list[dict]:
    if case_id:
        return [c for c in cases if c["id"] == case_id]
    if category:
        return [c for c in cases if c.get("category") == category]
    return cases


def run_case(pipeline: RAGPipeline, case: dict) -> CaseResult:
    pipeline.reset_conversation()
    question = case["question"]

    # 1. Guardrail check (mirrors api/server.py:148-160)
    allowed, refusal_msg, matched_kw = guardrail_check(question)
    if not allowed:
        return score_case(
            case=case,
            answer=refusal_msg,
            retrieved_product_ids=[],
            blocked=True,
        )

    # 2. Run pipeline
    try:
        result = pipeline.query(question, mode="professional")
        answer = result.text
    except Exception as e:
        answer = f"[ERROR] {type(e).__name__}: {e}"

    # 3. Collect retrieval product_ids
    retrieved_product_ids: list[str] = []
    for r in pipeline._last_retrieval or []:
        pid = r.chunk.metadata.get("product_id")
        if pid and pid not in retrieved_product_ids:
            retrieved_product_ids.append(pid)

    return score_case(
        case=case,
        answer=answer,
        retrieved_product_ids=retrieved_product_ids,
        blocked=False,
    )


def print_report(results: list[CaseResult], summary: dict, run_meta: dict) -> None:
    bar = "=" * 60
    print(f"\n{bar}")
    print(f"  RAGLoom Eval Report")
    print(f"  Run: {run_meta['timestamp']}  |  Model: {run_meta['llm_model']}")
    print(bar)
    print(f"  Cases: {summary['total']}  |  Passed: {summary['passed']} ({int(summary['pass_rate']*100)}%)\n")

    print("  Per-Dimension (macro-avg):")
    for dim, val in summary["per_dimension"].items():
        if val is None:
            print(f"    {dim:<14}: n/a")
        else:
            print(f"    {dim:<14}: {val:.2f}")
    print()

    print("  Per-Category:")
    for cat, stats in summary["per_category"].items():
        check = "✓" if stats["passed"] == stats["total"] else "⚠"
        print(f"    {cat:<22}: {stats['passed']}/{stats['total']}  {check}")
    print()

    failed = [r for r in results if not r.passed]
    if failed:
        print("  Failed Cases:")
        for r in failed:
            scores_str = " ".join(
                f"{k}={'-' if v is None else f'{v:.2f}'}"
                for k, v in r.scores.items()
            )
            print(f"    ✗ {r.case_id}")
            print(f"      {scores_str}")
            for note in r.notes:
                print(f"      · {note}")
    else:
        print("  All cases passed.")
    print(bar)


def save_json_report(results: list[CaseResult], summary: dict, run_meta: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts_slug = run_meta["timestamp"].replace(":", "").replace("-", "").replace(" ", "T")
    out_path = out_dir / f"eval_{ts_slug}.json"
    payload = {
        "run": run_meta,
        "summary": summary,
        "results": [r.to_dict() for r in results],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def main():
    parser = argparse.ArgumentParser(description="RAGLoom eval runner")
    parser.add_argument("--category", help="Run only cases in this category")
    parser.add_argument("--case", help="Run only this case_id")
    parser.add_argument("--skip-ingest", action="store_true", help="Reuse existing chroma_db without re-ingesting KB")
    parser.add_argument("--output-dir", default=str(RESULTS_DIR), help="Where to save the JSON report")
    args = parser.parse_args()

    cases = filter_cases(load_golden_set(), args.category, args.case)
    if not cases:
        print(f"[Runner] No cases match (category={args.category}, case={args.case})", file=sys.stderr)
        sys.exit(1)

    print(f"[Runner] Initializing pipeline...")
    pipeline = RAGPipeline()

    if not args.skip_ingest:
        print(f"[Runner] Re-ingesting KB at {KB_PATH}...")
        pipeline.reset_collection()
        pipeline.ingest(str(KB_PATH))
    else:
        print(f"[Runner] Skipping ingest (reusing existing collection)")

    run_meta = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "llm_model": pipeline.config.llm_model,
        "embedding_model": pipeline.config.embedding_model,
        "total_cases": len(cases),
    }

    print(f"[Runner] Running {len(cases)} case(s)...\n")
    results: list[CaseResult] = []
    t0 = time.time()
    for i, case in enumerate(cases, 1):
        print(f"  [{i}/{len(cases)}] {case['id']}...", end=" ", flush=True)
        case_t0 = time.time()
        r = run_case(pipeline, case)
        elapsed = time.time() - case_t0
        status = "✓" if r.passed else "✗"
        print(f"{status} ({elapsed:.1f}s)")
        results.append(r)

    total_elapsed = time.time() - t0
    run_meta["elapsed_seconds"] = round(total_elapsed, 1)

    summary = aggregate(results)
    print_report(results, summary, run_meta)

    out_path = save_json_report(results, summary, run_meta, Path(args.output_dir))
    print(f"\n  Detailed JSON: {out_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
