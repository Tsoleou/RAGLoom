"""
Output Critic 模組。

在 Generator 產出答案後，再做一次 LLM call 檢查答案是否違反負向規則。
可選擇只標記 (audit) 或自動改寫 (revise)。
"""

import json
import re
from dataclasses import dataclass
from typing import Optional

import requests


@dataclass
class CritiqueResult:
    passed: bool
    reason: str
    revised_text: Optional[str] = None


def _extract_json(text: str) -> Optional[dict]:
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def critique_answer(
    answer_text: str,
    criteria: str,
    model: str = "gemma3:4b",
    base_url: str = "http://localhost:11434",
    query: str = "",
    context: str = "",
    reference: str = "",
) -> CritiqueResult:
    """Run an LLM critique pass against the given answer.

    Two operating modes:
      - **Rules-only** (default): checks the answer against `criteria` only.
      - **Grounded**: when `query` and/or `context` are provided, also checks
        whether the answer (1) addresses the actual question and (2) only names
        products/models present in the sources. Catches off-target answers and
        hallucinated *products* that pure rule-checks miss. It deliberately does
        NOT police spec numbers/measurements — gemma3:4b is numerically blind and
        false-rejects grounded specs; numeric grounding is a code concern.

    Returns CritiqueResult with pass/fail + short reason.
    Network/parse failures degrade gracefully to "passed=True" so a flaky
    critic never blocks a correct answer reaching the user.
    """
    grounded = bool((query or "").strip() or (context or "").strip() or (reference or "").strip())

    if grounded:
        system = (
            "You are a strict quality reviewer. You will receive a user question, "
            "retrieved context, optional reference material, a candidate answer, "
            "and a list of negative rules.\n\n"
            "Decide if the answer:\n"
            "1. Actually addresses the user's question.\n"
            "2. Only names products or models that appear in the retrieved context "
            "OR the reference material — do not let it recommend or mention a "
            "product that appears in NEITHER source.\n"
            "3. Respects every negative rule below.\n\n"
            "Do NOT judge whether individual specs, numbers, measurements, or "
            "prices match the sources — that is checked separately. Treat the "
            "numeric details as correct.\n\n"
            "Output ONLY a valid JSON object with exactly two fields:\n"
            '- "pass": boolean — true only if ALL three conditions hold.\n'
            '- "reason": short string explaining the verdict (max 1 sentence).\n\n'
            'Example PASS: {"pass": true, "reason": "Grounded answer to the question."}\n'
            'Example FAIL: {"pass": false, "reason": "Recommended a product that appears in neither source."}'
        )
        user_parts = []
        if query.strip():
            user_parts.append(f"[User Question]\n{query.strip()}")
        if context.strip():
            user_parts.append(f"[Retrieved Context]\n{context.strip()}")
        if reference.strip():
            user_parts.append(f"[Reference Material]\n{reference.strip()}")
        user_parts.append(f"[Negative Rules]\n{criteria.strip()}")
        user_parts.append(f"[Candidate Answer]\n{answer_text.strip()}")
        user = "\n\n".join(user_parts) + "\n\nVerdict:"
    else:
        system = (
            "You are a strict quality reviewer. You will receive a candidate answer "
            "and a list of negative rules (things the answer must NOT do). "
            "Decide if the answer respects every rule.\n\n"
            "Output ONLY a valid JSON object with exactly two fields:\n"
            '- "pass": boolean — true if the answer respects all rules, false otherwise.\n'
            '- "reason": short string explaining the verdict (max 1 sentence).\n\n'
            'Example PASS: {"pass": true, "reason": "No rules violated."}\n'
            'Example FAIL: {"pass": false, "reason": "Mentions competitor brand Asus."}'
        )
        user = (
            f"[Negative Rules]\n{criteria.strip()}\n\n"
            f"[Candidate Answer]\n{answer_text.strip()}\n\n"
            "Verdict:"
        )
    full_prompt = f"{system}\n\n[User Request]: {user}"

    try:
        resp = requests.post(
            f"{base_url}/api/generate",
            json={
                "model": model,
                "prompt": full_prompt,
                "stream": False,
                "format": "json",
            },
            timeout=120,
        )
        resp.raise_for_status()
        raw = resp.json().get("response", "").strip()
    except (requests.ConnectionError, requests.HTTPError) as e:
        return CritiqueResult(passed=True, reason=f"Critic unavailable ({e}); defaulting to pass.")

    parsed = _extract_json(raw)
    if not parsed or "pass" not in parsed:
        return CritiqueResult(passed=True, reason="Critic returned unparseable output; defaulting to pass.")

    return CritiqueResult(
        passed=bool(parsed.get("pass")),
        reason=str(parsed.get("reason", "")).strip() or "(no reason provided)",
    )


def revise_answer(
    original_text: str,
    criteria: str,
    critique_reason: str,
    model: str = "gemma3:4b",
    base_url: str = "http://localhost:11434",
) -> str:
    """Ask the LLM to rewrite an answer to fix the issues identified by the critic."""
    system = (
        "You are an editor. The previous answer violated quality rules. "
        "Rewrite it so it follows every rule below. Keep the factual content and tone intact. "
        "Reply in the same language as the original answer. "
        "Output only the corrected answer in plain text — no preamble, no JSON, no markdown."
    )
    user = (
        f"[Rules to follow]\n{criteria.strip()}\n\n"
        f"[Original Answer]\n{original_text.strip()}\n\n"
        f"[Issues to fix]\n{critique_reason.strip()}\n\n"
        "Corrected answer:"
    )
    full_prompt = f"{system}\n\n[User Request]: {user}"

    try:
        resp = requests.post(
            f"{base_url}/api/generate",
            json={"model": model, "prompt": full_prompt, "stream": False},
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip() or original_text
    except (requests.ConnectionError, requests.HTTPError):
        return original_text
