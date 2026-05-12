"""
Eval LLM-as-judge — secondary LLM pass to audit faithfulness, relevance, and hallucinations.

For each case the runner sends the original question, the retrieved chunks the
generator saw, and the generator's answer. The judge returns per-dimension
scores plus a list of factual claims that aren't supported by the retrieved
context (used by the 1b' hallucination gate in runner.py).

Schema-constrained via Ollama structured output — the same grammar mechanism
that enforces the CHATBOT persona's JSON shape (see core/personas.py).
"""

from __future__ import annotations

import json
import re
from typing import Optional

import requests


JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "faithfulness": {
            "type": "object",
            "properties": {
                "score": {"type": "number"},
                "supported_claims": {"type": "array", "items": {"type": "string"}},
                "hallucinated_claims": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["score", "supported_claims", "hallucinated_claims"],
        },
        "relevance": {
            "type": "object",
            "properties": {
                "score": {"type": "number"},
                "reason": {"type": "string"},
            },
            "required": ["score", "reason"],
        },
    },
    "required": ["faithfulness", "relevance"],
}


JUDGE_SYSTEM_PROMPT = (
    "You are a strict RAG quality auditor. You receive a user question, the context the assistant "
    "had access to (retrieved chunks + always-available reference data), and the assistant's answer. "
    "Judge two dimensions.\n\n"
    "FAITHFULNESS — is every factual claim in the answer supported by the provided context?\n"
    "  - A 'claim' is a SPECIFIC factual assertion: model names (e.g. 'VisionBook Studio 16'), "
    "numeric specs (e.g. '16GB', '240Hz', '1.95 kg'), feature names (e.g. 'OLED', 'Wi-Fi 7', 'ECC memory'), "
    "port counts, weights.\n"
    "  - Marketing language ('great for gaming', 'perfect for creators', 'powerful') is NOT a claim — ignore it.\n"
    "  - A claim is SUPPORTED if EITHER the retrieved chunks OR the reference data asserts it. "
    "Check both before deciding.\n"
    "  - A claim is HALLUCINATED only if it cannot be verified from either source. Silence in both = hallucinated.\n"
    "  - 'supported_claims' / 'hallucinated_claims' must quote the specific phrase from the answer.\n"
    "  - score = (#supported) / (#supported + #hallucinated). If the answer contains no factual claims, score = 1.0.\n\n"
    "RELEVANCE — does the answer address the question?\n"
    "  - 1.0 = directly answers; 0.5 = partially answers or drifts off-topic; 0.0 = off-topic or refuses.\n"
    "  - A faithful answer can still be irrelevant. Judge independently of faithfulness.\n\n"
    "Output ONLY a JSON object matching the schema. Be conservative — if a claim cannot be verified from "
    "either context source, treat it as hallucinated."
)


def _extract_json(text: str) -> Optional[dict]:
    """Pull the first balanced JSON object out of a string.

    Defensive — schema-constrained output should already be a clean JSON document,
    but parse failures must not crash the eval run.
    """
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _empty_judge(model: str, error: str) -> dict:
    """Fail-open judge result for unreachable/unparseable cases.

    hallucinated_claims=[] ensures the 1b' gate does NOT flip passed=False when
    the judge itself is broken — a flaky judge must never punish a correct answer.
    """
    return {
        "faithfulness": {"score": None, "supported_claims": [], "hallucinated_claims": []},
        "relevance": {"score": None, "reason": "judge unavailable"},
        "judge_model": model,
        "error": error,
    }


def run_judge(
    question: str,
    retrieved_chunks: list[str],
    answer: str,
    reference_data: str = "",
    model: str = "gemma3:4b",
    base_url: str = "http://localhost:11434",
) -> dict:
    """Run a second-pass LLM audit on a generated answer.

    The judge must see EVERY context source the assistant saw, otherwise
    legitimate claims from the always-on reference (product comparison
    CSV, etc.) look like fabrication when only the top-N retrieved chunks
    are visible. Pass reference_data separately so the schema-side scoring
    rule can name both sources explicitly.

    Returns a dict matching JUDGE_SCHEMA plus `judge_model` and `error`
    (None on success). Network/parse failures degrade gracefully via
    _empty_judge.
    """
    if not retrieved_chunks:
        ctx_block = "(no chunks retrieved)"
    else:
        ctx_block = "\n\n".join(
            f"[Chunk {i + 1}]\n{c.strip()}" for i, c in enumerate(retrieved_chunks)
        )

    user_parts = [
        f"[Question]\n{question.strip()}",
        f"[Retrieved Context]\n{ctx_block}",
    ]
    if reference_data and reference_data.strip():
        user_parts.append(
            f"[Reference Data — always available to the assistant]\n{reference_data.strip()}"
        )
    user_parts.append(f"[Answer]\n{answer.strip()}")
    user_parts.append("Output JSON:")
    user_content = "\n\n".join(user_parts)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
        "format": JUDGE_SCHEMA,
    }

    try:
        resp = requests.post(f"{base_url}/api/chat", json=payload, timeout=120)
        resp.raise_for_status()
        raw = resp.json().get("message", {}).get("content", "").strip()
    except (requests.ConnectionError, requests.HTTPError, requests.Timeout) as e:
        return _empty_judge(model, f"network: {type(e).__name__}: {e}")

    parsed = _extract_json(raw)
    if not parsed:
        return _empty_judge(model, f"unparseable output: {raw[:120]!r}")

    faith = parsed.get("faithfulness") or {}
    rel = parsed.get("relevance") or {}
    if "hallucinated_claims" not in faith or "supported_claims" not in faith:
        return _empty_judge(model, "schema mismatch (missing claim lists)")

    return {
        "faithfulness": {
            "score": faith.get("score"),
            "supported_claims": list(faith.get("supported_claims") or []),
            "hallucinated_claims": list(faith.get("hallucinated_claims") or []),
        },
        "relevance": {
            "score": rel.get("score"),
            "reason": str(rel.get("reason") or "").strip() or "(no reason)",
        },
        "judge_model": model,
        "error": None,
    }
