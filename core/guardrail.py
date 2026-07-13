"""
Query safety guardrail.

Central rule: if a query mentions restricted keywords (e.g., competitor brands),
refuse to answer politely. Used by both the chat endpoint and the Guardrail
node — single source of truth, single refusal behavior.
"""

import json
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


def format_refusal(refusal_text: str, format_hint=None) -> str:
    """Wrap a refusal in chatbot JSON shape when format_hint requests it.

    Mirrors the pattern used by scope_gate / price_guard so all three gates
    produce consistent output when the downstream Generator expects JSON.
    """
    if isinstance(format_hint, dict) or format_hint == "json":
        return json.dumps({"reply": refusal_text, "emotion": "idle"}, ensure_ascii=False)
    return refusal_text


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
        # ASCII keywords (brand tokens like "hp"/"asus") use word boundaries so
        # they don't false-match inside longer ASCII words. CJK keywords have no
        # ASCII word boundaries — an all-Chinese query like "台積電股價多少" has
        # no \b anywhere under re.ASCII, so \b股價\b never fires. Match those as
        # plain substrings, which is the correct unit for non-space-delimited CJK.
        if kw_lower.isascii():
            hit = re.search(rf"\b{re.escape(kw_lower)}\b", lower, flags=re.ASCII)
        else:
            hit = kw_lower in lower
        if hit:
            return False, refusal_message, kw_lower

    return True, "", ""
