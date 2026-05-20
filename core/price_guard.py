"""
Price guard — keyword-based short-circuit for pricing queries.

The knowledge base intentionally contains zero price data, but gemma3:4b
under direct "how much" pressure fabricates dollar amounts no matter how
strongly the persona forbids it (verified 2026-05-12). This guard sits at
the top of pipeline.query(), pattern-matches price intent, and returns a
canned refusal without ever calling the LLM — the same enforcement strategy
brand Guardrail and ScopeGate use, in spirit: when prompt-level rules
can't be trusted at small-model scale, push the policy into code.

The refusal speaks the visitor's language (English / Chinese CJK), since
canned text bypasses the model's per-turn language anchor.
"""

from __future__ import annotations

import re


# Detect "how much / price / cost / MSRP / discount" intent in EN + ZH.
# Each pattern is a single anchored fragment; combined with re.IGNORECASE.
_PRICE_PATTERNS = [
    r"\bprice[ds]?\b",
    r"\bpricing\b",
    r"\bcost(s|ing)?\b",
    r"\bmsrp\b",
    r"\bhow much\b",
    r"\bdiscount(s|ed|ing)?\b",
    r"\$\s?\d",       # explicit dollar amount mentioned in query
    r"售價",
    r"價格",
    r"價錢",
    r"定價",
    r"多少錢",
    r"幾錢",
    r"NT\$",
    r"折扣",
    r"優惠價",
    r"打折",
    r"特價",
]
_PRICE_RE = re.compile("|".join(_PRICE_PATTERNS), re.IGNORECASE)


class PriceGuardBlocked(Exception):
    """Raised when a query is blocked by the price guard.

    Mirrors GuardrailBlocked / ScopeBlocked so the node engine can treat all
    three with the same STATUS_BLOCKED short-circuit handler.
    """

    def __init__(self, reason: str, refusal_message: str):
        super().__init__(reason)
        self.reason = reason
        self.refusal_message = refusal_message
        # Engine's blocked-handler logs `matched_keyword`. Price detection is
        # regex-based with no single token, so surface a synthetic marker.
        self.matched_keyword = "price_intent"

_REFUSAL_EN = (
    "I don't have pricing info here at the booth — that's set by our retail "
    "partners, and the staff can pull up the exact numbers for you. In the "
    "meantime, want me to walk through what makes this product special?"
)

_REFUSAL_ZH = (
    "這邊沒有定價資訊，售價是由零售合作夥伴決定的，現場的工作人員可以幫您查到"
    "準確的價格。在那之前，想不想先聊聊這台筆電有什麼特別的地方？"
)


def is_price_query(query: str) -> bool:
    """True if the query is asking about price / cost / discount in EN or ZH."""
    return bool(_PRICE_RE.search(query or ""))


def refusal_message(query: str, format_hint=None) -> str:
    """Return a canned price refusal.

    For chatbot mode (format_hint is a dict / JSON Schema), wrap in the
    expected {reply, emotion} shape so the frontend renders cleanly.
    """
    is_chinese = bool(re.search(r"[一-鿿]", query or ""))
    text = _REFUSAL_ZH if is_chinese else _REFUSAL_EN

    if isinstance(format_hint, dict):
        import json
        return json.dumps({"reply": text, "emotion": "explaining"}, ensure_ascii=False)
    return text
