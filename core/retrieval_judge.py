"""
Retrieval Judge.

After the dense retriever picks top-K candidate chunks, a small LLM reads
the query + every candidate together and decides which ones actually answer
the question vs. which only share surface keywords. Catches the failure
modes pure cosine retrieval can't:

  - **Polarity/negation flips** — a chunk like "本機不適合高效能需求"
    embeds close to "推薦效能強的筆電" because the surface tokens overlap,
    but the LLM reads the negation and drops it.
  - **Keyword squatting** — generic terms like "筆電" hit nearly every
    chunk; the judge can tell whether a chunk genuinely addresses *this*
    question.

One LLM call covers all K candidates — output is a JSON verdict list.
On any LLM / parse error the function degrades to "keep everything" so a
flaky judge never hides correct chunks.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Iterable

import requests

from core.vector_store import RetrievalResult


# Single call, batched. Truncate each chunk to keep prompt small.
_DEFAULT_PREVIEW_CHARS = 300

_SYSTEM = (
    "You are a strict retrieval reviewer. You will receive a user question "
    "and a numbered list of retrieved text chunks. Decide for each chunk "
    "whether it actually helps answer the question.\n\n"
    "Pay attention to POLARITY: a chunk that contains the keywords but in a "
    "negative or warning context (e.g., 'NOT suitable for high-performance') "
    "must be marked keep=false.\n\n"
    "Output ONLY a valid JSON object with this exact shape:\n"
    '{"verdicts": [{"i": 0, "keep": true|false, "reason": "short"}, ...]}\n'
    "- One entry per chunk, in the same order.\n"
    "- `reason` is one short clause (max 12 words).\n"
    "- No prose outside the JSON object."
)


@dataclass
class JudgeVerdict:
    index: int
    keep: bool
    reason: str
    source: str
    score: float


def _extract_json(text: str) -> dict | None:
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def judge_retrieval(
    query: str,
    results: Iterable[RetrievalResult],
    model: str = "gemma3:4b",
    base_url: str = "http://localhost:11434",
    max_preview_chars: int = _DEFAULT_PREVIEW_CHARS,
) -> tuple[list[RetrievalResult], list[JudgeVerdict]]:
    """Filter retrieval results via a single batched LLM relevance judge.

    Returns (kept_results, verdicts). `verdicts` carries one entry per input
    chunk (kept or dropped) so the UI can show the rerank decision.

    Degrades to "keep everything" on LLM error / parse failure — never silently
    drops chunks just because the judge misfired.
    """
    candidates = list(results)
    if not candidates or not (query and query.strip()):
        return candidates, []

    chunks_block = "\n\n".join(
        f"[{i}] (source={r.chunk.metadata.get('filename', '?')}, score={r.score:.2f})\n"
        f"{r.chunk.text[:max_preview_chars]}"
        for i, r in enumerate(candidates)
    )
    user = (
        f"[Question]\n{query.strip()}\n\n"
        f"[Chunks]\n{chunks_block}\n\n"
        "Verdicts:"
    )
    prompt = f"{_SYSTEM}\n\n{user}"

    try:
        resp = requests.post(
            f"{base_url}/api/generate",
            json={"model": model, "prompt": prompt, "stream": False, "format": "json"},
            timeout=120,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
    except (requests.ConnectionError, requests.HTTPError, requests.Timeout) as e:
        print(f"[RetrievalJudge] LLM call failed ({e}); keeping all chunks.")
        return candidates, [
            JudgeVerdict(
                index=i,
                keep=True,
                reason="judge unavailable",
                source=r.chunk.metadata.get("filename", "?"),
                score=r.score,
            )
            for i, r in enumerate(candidates)
        ]

    parsed = _extract_json(raw)
    if not parsed or "verdicts" not in parsed:
        print(f"[RetrievalJudge] Unparseable verdict (raw={raw[:80]!r}); keeping all chunks.")
        return candidates, [
            JudgeVerdict(
                index=i,
                keep=True,
                reason="judge output unparseable",
                source=r.chunk.metadata.get("filename", "?"),
                score=r.score,
            )
            for i, r in enumerate(candidates)
        ]

    # Index judgments by `i`; tolerate out-of-order or missing entries.
    by_idx: dict[int, dict] = {}
    for v in parsed.get("verdicts", []):
        if isinstance(v, dict) and isinstance(v.get("i"), int):
            by_idx[v["i"]] = v

    verdicts: list[JudgeVerdict] = []
    kept: list[RetrievalResult] = []
    for i, r in enumerate(candidates):
        v = by_idx.get(i)
        keep = bool(v.get("keep", True)) if v else True
        reason = str(v.get("reason", "")) if v else "missing verdict — kept by default"
        verdicts.append(
            JudgeVerdict(
                index=i,
                keep=keep,
                reason=reason,
                source=r.chunk.metadata.get("filename", "?"),
                score=r.score,
            )
        )
        if keep:
            kept.append(r)

    dropped = len(candidates) - len(kept)
    print(f"[RetrievalJudge] kept {len(kept)}/{len(candidates)} chunks ({dropped} dropped)")
    return kept, verdicts
