"""
RAGLoom eval runner — load golden_set.json, run pipeline, score, report.

Usage:
    python -m eval.runner                              # all cases
    python -m eval.runner --category language_test     # filter by category
    python -m eval.runner --case starforge_x1_gpu_en   # single case (debug)
    python -m eval.runner --skip-ingest                # reuse existing chroma_db (faster reruns)
    python -m eval.runner --llm-judge                  # run secondary LLM audit pass
    python -m eval.runner --llm-judge --judge-model qwen2.5:7b
    python -m eval.runner --llm-judge --no-hallucination-gate    # judge reports only, no veto
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

from eval.judge import run_judge
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


def run_case(pipeline: RAGPipeline, case: dict, args: argparse.Namespace) -> CaseResult:
    pipeline.reset_conversation()
    question = case["question"]

    # 1. Guardrail check (mirrors api/server.py:148-160)
    allowed, refusal_msg, matched_kw = guardrail_check(question)
    if not allowed:
        # Guardrail-blocked cases never reach the judge: there is no
        # generated answer to audit, only a canned refusal.
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

    # 3. Collect retrieval product_ids + chunk texts (chunks fed to the judge)
    retrieved_product_ids: list[str] = []
    retrieved_chunks: list[str] = []
    for r in pipeline._last_retrieval or []:
        pid = r.chunk.metadata.get("product_id")
        if pid and pid not in retrieved_product_ids:
            retrieved_product_ids.append(pid)
        retrieved_chunks.append(r.chunk.text)

    # Inner-guard attribution: PriceGuard / ScopeGate short-circuit inside
    # pipeline.query() and write to pipeline._last_guards. Without reading
    # this, scorer thinks blocked=False even when the answer is a canned
    # refusal — guardrail-expected cases then fail spuriously.
    inner_blocked = any(
        g.get("status") == "block" for g in (pipeline._last_guards or [])
    )

    case_result = score_case(
        case=case,
        answer=answer,
        retrieved_product_ids=retrieved_product_ids,
        blocked=inner_blocked,
    )

    # 4. Optional LLM-as-judge pass — skip when pipeline short-circuited
    # (price guard / scope gate). Those return canned refusals before
    # retrieval runs, so there is no context to verify the answer against
    # and the judge would falsely flag the refusal text as unsupported.
    if args.llm_judge and retrieved_chunks:
        case_result.llm_judge = run_judge(
            question=question,
            retrieved_chunks=retrieved_chunks[:3],
            answer=answer,
            reference_data=pipeline._reference_data,
            model=args.judge_model,
        )

        # 1b' hallucination gate — only the binary list signal can veto,
        # never the noisy continuous scores. Fail-open when the judge itself
        # errored (error != None) so a flaky judge doesn't punish good answers.
        if (
            case_result.llm_judge
            and not args.no_hallucination_gate
            and case_result.llm_judge.get("error") is None
        ):
            hallucinated = case_result.llm_judge["faithfulness"]["hallucinated_claims"]
            if hallucinated:
                case_result.passed = False
                case_result.notes.append(
                    f"LLM judge: hallucinations flagged — {hallucinated}"
                )

    return case_result


def print_report(results: list[CaseResult], summary: dict, run_meta: dict) -> None:
    bar = "=" * 60
    print(f"\n{bar}")
    print(f"  RAGLoom Eval Report")
    print(f"  Run: {run_meta['timestamp']}  |  Model: {run_meta['llm_model']}")
    if run_meta.get("judge_model"):
        gate_state = "ON" if run_meta.get("hallucination_gate") else "OFF (observer)"
        print(f"  Judge: {run_meta['judge_model']}  |  Hallucination gate: {gate_state}")
    print(bar)
    print(f"  Cases: {summary['total']}  |  Passed: {summary['passed']} ({int(summary['pass_rate']*100)}%)\n")

    print("  Per-Dimension (macro-avg, rule-based):")
    for dim, val in summary["per_dimension"].items():
        if val is None:
            print(f"    {dim:<14}: n/a")
        else:
            print(f"    {dim:<14}: {val:.2f}")
    print()

    if "per_dimension_llm" in summary:
        llm = summary["per_dimension_llm"]
        print("  Per-Dimension (LLM judge):")
        for dim in ("faithfulness", "relevance"):
            val = llm.get(dim)
            print(f"    {dim:<14}: {'n/a' if val is None else f'{val:.2f}'}")
        print(f"    {'hallucination':<14}: {llm['hallucination_rate']} cases flagged")
        if llm["judge_errors"]:
            print(f"    {'judge_errors':<14}: {llm['judge_errors']}")
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

    if summary.get("judge_failures"):
        print("\n  ⚠ Judge-flagged hallucinations:")
        for jf in summary["judge_failures"]:
            print(f"    - {jf['case_id']}: {jf['hallucinated_claims']}")
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
    parser.add_argument("--llm-judge", action="store_true", help="Run a secondary LLM audit pass for hallucination + relevance")
    parser.add_argument("--judge-model", default="gemma3:4b", help="Ollama model used as judge (only effective with --llm-judge)")
    parser.add_argument("--no-hallucination-gate", action="store_true", help="Judge reports only; hallucinated_claims do not flip passed=False (calibration mode)")
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
        "judge_model": args.judge_model if args.llm_judge else None,
        "hallucination_gate": bool(args.llm_judge and not args.no_hallucination_gate),
    }

    print(f"[Runner] Running {len(cases)} case(s)...\n")
    results: list[CaseResult] = []
    t0 = time.time()
    for i, case in enumerate(cases, 1):
        print(f"  [{i}/{len(cases)}] {case['id']}...", end=" ", flush=True)
        case_t0 = time.time()
        r = run_case(pipeline, case, args)
        elapsed = time.time() - case_t0
        status = "✓" if r.passed else "✗"
        # Inline judge summary for quick scanning when --llm-judge is on
        judge_inline = ""
        if r.llm_judge and r.llm_judge.get("error") is None:
            h = r.llm_judge["faithfulness"]["hallucinated_claims"]
            judge_inline = f"  [judge: {len(h)} hallucinated]" if h else "  [judge: clean]"
        elif r.llm_judge:
            judge_inline = "  [judge: error]"
        print(f"{status} ({elapsed:.1f}s){judge_inline}")
        results.append(r)

    total_elapsed = time.time() - t0
    run_meta["elapsed_seconds"] = round(total_elapsed, 1)

    summary = aggregate(results)
    print_report(results, summary, run_meta)

    out_path = save_json_report(results, summary, run_meta, Path(args.output_dir))
    print(f"\n  Detailed JSON: {out_path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
