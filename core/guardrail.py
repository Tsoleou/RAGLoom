"""
Query safety guardrail.

Central rule: if a query mentions restricted keywords (e.g., competitor brands),
refuse to answer politely. Used by both the chat endpoint and the Guardrail
node — single source of truth, single refusal behavior.
"""

import re


DEFAULT_BLOCKED_BRANDS: list[str] = ["asus", "acer", "msi", "hp", "dell", "apple"]

DEFAULT_REFUSAL_MESSAGE: str = (
    "I'm sorry, but I can only answer questions about our own products. "
    "For information about other brands, please visit their official channels."
)


class GuardrailBlocked(Exception):
    """Raised when a query is blocked by the guardrail.

    Carries the refusal message so downstream handlers (engine, chat endpoint)
    can surface it to the user without re-deriving it.
    """

    def __init__(self, reason: str, refusal_message: str, matched_keyword: str):
        super().__init__(reason)
        self.reason = reason
        self.refusal_message = refusal_message
        self.matched_keyword = matched_keyword


def parse_keywords(raw: str) -> list[str]:
    """Parse a comma-separated keyword string into a clean list."""
    return [kw.strip() for kw in raw.split(",") if kw.strip()]


def check_query(
    query: str,
    blocked_keywords: list[str] | None = None,
    refusal_message: str | None = None,
) -> tuple[bool, str, str]:
    """Check whether a user query should be blocked.

    Matching uses ASCII word boundaries so short keywords like "hp" don't
    false-match inside ASCII words, while CJK characters (which are \w under
    Unicode) still count as boundaries — otherwise "asus筆電" wouldn't match.

    Args:
        query: The user's question.
        blocked_keywords: Keywords to block. Defaults to DEFAULT_BLOCKED_BRANDS.
        refusal_message: Message returned when blocked. Defaults to DEFAULT_REFUSAL_MESSAGE.

    Returns:
        (allowed, message, matched_keyword).
        - allowed=True: message="", matched_keyword=""
        - allowed=False: message=refusal text, matched_keyword=the triggering word
    """
    if blocked_keywords is None:
        blocked_keywords = DEFAULT_BLOCKED_BRANDS
    if refusal_message is None or not refusal_message.strip():
        refusal_message = DEFAULT_REFUSAL_MESSAGE

    lower = query.lower()
    for kw in blocked_keywords:
        kw_lower = kw.strip().lower()
        if not kw_lower:
            continue
        if re.search(rf"\b{re.escape(kw_lower)}\b", lower, flags=re.ASCII):
            return False, refusal_message, kw_lower

    return True, "", ""
