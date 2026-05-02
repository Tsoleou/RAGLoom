"""
Scope gate — semantic-relevance check for off-topic queries.

Sister mechanism to `core/guardrail.py`: where Guardrail blocks by keyword
*before* retrieval, ScopeGate blocks queries that look off-topic *after*
retrieval. Two interchangeable detection modes:

  - "semantic" (default, recommended) — compare the query embedding against
    a small set of on-topic and off-topic ANCHOR phrases. Anchors live
    outside the KB, so they're immune to KB-keyword noise (e.g. "laptop"
    being so common in product chunks that it loses discrimination).
    Returns the margin `on_max - off_max`; positive ⇒ on-topic.

  - "retrieval" — threshold on the retriever's top score. Cheaper (reuses
    existing scores, no extra embed call) but less robust to bridge attacks
    like "is the dog like a laptop?" because dog/laptop both hit roughly
    similar retrieval scores against a laptop KB.

Greetings and very short utterances bypass either check so persona Rule 0
("respond warmly + invite a laptop question") still handles them.
"""

import json
import math
import re
from typing import Iterable, Optional, Sequence

from core.embedder import embed_query
from core.vector_store import RetrievalResult


DEFAULT_MIN_SCORE: float = 0.7
DEFAULT_MARGIN_THRESHOLD: float = 0.0


# Anchors live outside the KB. Embedded once per (tuple, model) and cached.
# Keep them concise and roughly parallel in length so cosine geometry stays
# comparable; mix English + Chinese so the gate works for both.
DEFAULT_ON_TOPIC_ANCHORS: list[str] = [
    "Questions about laptop computers, their specs, prices, or features",
    "Asking which laptop is best for gaming, work, school, or creative use",
    "Comparing laptop products across brands or models",
    "Questions about laptop hardware: CPU, GPU, RAM, screen, battery, or weight",
    "Questions about specific laptop models, brands, or product lines",
    "筆記型電腦的規格、價格、功能或推薦",
    "詢問筆電的處理器、顯示卡、記憶體、螢幕等硬體",
    "詢問哪一款筆電適合特定用途",
]

DEFAULT_OFF_TOPIC_ANCHORS: list[str] = [
    "Questions about pets, animals, or breeds",
    "Questions about food, cooking, or restaurants",
    "Questions about movies, music, sports, or entertainment",
    "Questions about weather, news, or current events",
    "關於寵物、動物、食物、天氣的問題",
    "與電腦科技無關的個人生活建議",
]

# A pure greeting / small-talk should NOT be blocked even when retrieval is
# low — those are handled by persona Rule 0. Match common openers in English
# and Chinese.
GREETING_RE = re.compile(
    r"^\s*(hi+|hello+|hey+|yo|嗨+|你好+|哈囉+|哈嘍+|"
    r"good\s+(morning|afternoon|evening)|早安|午安|晚安)\b",
    flags=re.IGNORECASE,
)

# Short queries are likely 1-2 word greetings/acks ("hi", "ok", "yes", "no",
# "好的"). Anything longer can be a meaningful CJK sentence (6 Chinese chars
# already form a question), so we keep the bypass tight.
SHORT_QUERY_LEN: int = 4


# Language-aware canned refusals. Returned by the gate when the query is
# clearly off-topic (low retrieval score) — keeps refusals consistent across
# turns and avoids the small-model failure modes (token loops, hallucinated
# off-topic catalogs) that we saw with prompt-only refusal.
_REFUSAL_EN: str = (
    "I'm only able to help with the laptops at this booth — "
    "but I'd love to talk about any of them! What kind of laptop are you looking for?"
)
_REFUSAL_ZH: str = (
    "我這邊只負責筆電的問題喔 — 不過我很樂意介紹任何一款！"
    "想了解哪一種類型的筆電呢？"
)


class ScopeBlocked(Exception):
    """Raised when retrieval score indicates an off-topic query.

    Mirrors GuardrailBlocked so the engine can treat both with the same
    short-circuit / STATUS_BLOCKED handling.
    """

    def __init__(self, reason: str, refusal_message: str, max_score: float):
        super().__init__(reason)
        self.reason = reason
        self.refusal_message = refusal_message
        self.max_score = max_score
        # Convenience for the engine's existing Guardrail-style handler that
        # logs `matched_keyword`. ScopeGate has no keyword, so we surface the
        # numeric reason here in the same field.
        self.matched_keyword = f"score={max_score:.2f}"


def _detect_language(text: str) -> str:
    """Return 'Chinese' if any CJK char present, else 'English'.

    Mirrors core.generator._detect_language to keep canned refusals language-
    aware without importing across module boundaries.
    """
    return "Chinese" if re.search(r"[一-鿿]", text) else "English"


def refusal_message(query: str, format_hint=None) -> str:
    """Build a canned refusal in the visitor's language.

    For chatbot mode (JSON Schema or "json" format_hint), wrap as a valid
    JSON object matching the {reply, emotion} schema so downstream parsers
    don't break.
    """
    text = _REFUSAL_ZH if _detect_language(query) == "Chinese" else _REFUSAL_EN

    if isinstance(format_hint, dict) or format_hint == "json":
        return json.dumps({"reply": text, "emotion": "idle"}, ensure_ascii=False)
    return text


def check_scope(
    query: str,
    results: Iterable[RetrievalResult],
    min_score: float = DEFAULT_MIN_SCORE,
    format_hint=None,
) -> tuple[bool, float]:
    """Retrieval-score variant: in-scope if top retrieval score >= min_score.

    Returns:
        (allowed, max_score)
        - allowed=True  when greeting/short-query bypass OR max_score >= min_score
        - allowed=False when off-topic; caller should raise ScopeBlocked
          (or short-circuit directly in chat path).

    Note: this function does NOT raise — the caller decides whether to raise
    the exception (node graph) or build a refusal response (chat path).
    """
    if GREETING_RE.match(query) or len(query.strip()) < SHORT_QUERY_LEN:
        return True, 0.0

    results_list = list(results)
    max_score = max((r.score for r in results_list), default=0.0)

    return max_score >= min_score, max_score


# Anchor embeddings are pure functions of (anchor list, model) so we cache by
# a hashable key. Keys: (tuple(anchors), model). Refreshed only when the user
# edits the anchors via the node param.
_anchor_cache: dict[tuple, list[list[float]]] = {}


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity between two equal-length vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _get_anchor_embeddings(
    anchors: Sequence[str],
    model: str,
    base_url: str,
) -> list[list[float]]:
    """Embed anchors lazily and cache. Empty / whitespace-only anchors skipped."""
    cleaned = tuple(a.strip() for a in anchors if a and a.strip())
    if not cleaned:
        return []
    key = (cleaned, model)
    cached = _anchor_cache.get(key)
    if cached is not None:
        return cached
    embeds = [embed_query(a, model=model, base_url=base_url) for a in cleaned]
    _anchor_cache[key] = embeds
    return embeds


def check_scope_semantic(
    query: str,
    on_topic_anchors: Optional[Sequence[str]] = None,
    off_topic_anchors: Optional[Sequence[str]] = None,
    margin_threshold: float = DEFAULT_MARGIN_THRESHOLD,
    embedding_model: str = "nomic-embed-text",
    base_url: str = "http://localhost:11434",
    query_embedding: Optional[list[float]] = None,
) -> tuple[bool, float]:
    """Anchor-based semantic in-scope check.

    Compares the query embedding against on-topic and off-topic anchor sets;
    margin = max(on_sim) - max(off_sim). In-scope when margin > threshold.

    Why this beats retrieval-score for bridge attacks: bridge phrasing like
    "is the dog like a laptop?" pulls similarity to BOTH anchor sets, but
    off-topic anchors usually win because "dog" carries more semantic weight
    than the generic "laptop" token in most embedding spaces.

    Args:
        query: User question.
        on_topic_anchors: Phrases representing the desired domain. Defaults
            to DEFAULT_ON_TOPIC_ANCHORS.
        off_topic_anchors: Phrases representing what to refuse. Defaults
            to DEFAULT_OFF_TOPIC_ANCHORS.
        margin_threshold: Minimum on - off margin to count as in-scope.
            0.0 means "more on than off"; raise to be stricter.
        query_embedding: Optional precomputed query vector to skip an extra
            embedder API call (e.g., when the retriever already embedded).

    Returns:
        (allowed, margin). Greetings / short queries bypass with margin=0.0.
    """
    if GREETING_RE.match(query) or len(query.strip()) < SHORT_QUERY_LEN:
        return True, 0.0

    on_anchors = list(on_topic_anchors) if on_topic_anchors is not None else DEFAULT_ON_TOPIC_ANCHORS
    off_anchors = list(off_topic_anchors) if off_topic_anchors is not None else DEFAULT_OFF_TOPIC_ANCHORS

    on_embeds = _get_anchor_embeddings(on_anchors, embedding_model, base_url)
    off_embeds = _get_anchor_embeddings(off_anchors, embedding_model, base_url)

    # If both sides are empty, treat as pass — we have no signal.
    if not on_embeds and not off_embeds:
        return True, 0.0

    if query_embedding is None:
        query_embedding = embed_query(query, model=embedding_model, base_url=base_url)

    on_max = max((_cosine(query_embedding, a) for a in on_embeds), default=0.0)
    off_max = max((_cosine(query_embedding, a) for a in off_embeds), default=0.0)
    margin = on_max - off_max

    return margin > margin_threshold, margin
