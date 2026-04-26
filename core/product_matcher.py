"""
Product name detection for hard-filter routing.

Maps a user query to a single product_id when the query unambiguously names
one product. Used by RAGPipeline.query() to bypass dense semantic retrieval
for "tell me about X" type queries — embedding similarity alone fails to
distinguish products in a homogeneous catalog (every product chunk looks
similar: specs / USP / target audience), so the right product can rank
below position 100 even when its name is in the query.

Behavior:
- 0 products mentioned                → None (fall through to dense retrieval)
- ≥2 distinct products mentioned      → None (comparison query, dense covers breadth)
- Comparison keyword (vs/比較/...)    → None (even if 1 product matched)
- Otherwise                           → the most specific matched product_id
"""

import re
from functools import lru_cache
from typing import Iterable, Optional


_COMPARISON_RE = re.compile(
    r"比較|差別|差在哪|哪個好|哪一台|哪一個|\bvs\b|\bversus\b|\bcompare\b",
    flags=re.IGNORECASE,
)


@lru_cache(maxsize=None)
def _build_pattern(product_id: str) -> re.Pattern:
    """Build a word-boundary regex for an id like 'visionbook_17'.

    Tokens may be separated by zero or more whitespace / underscore / hyphen
    so 'VisionBook 17', 'visionbook_17', 'visionbook17' all match. re.ASCII
    is required so a CJK char counts as a word boundary — otherwise queries
    like '我想看visionbook介紹' miss because Python's default \\b treats CJK
    as \\w (same root issue as guardrail's keyword matching).
    """
    parts = product_id.split("_")
    body = r"[\s_-]*".join(re.escape(p) for p in parts)
    return re.compile(rf"\b{body}\b", flags=re.IGNORECASE | re.ASCII)


def _drop_prefix_redundant(matches: list[str]) -> list[str]:
    """Drop shorter ids that are prefixes of longer matched ids.

    When 'visionbook' and 'visionbook_17' both match, the user almost
    certainly meant the more specific model — keep only the longer one.
    """
    sorted_desc = sorted(set(matches), key=len, reverse=True)
    keep: list[str] = []
    for m in sorted_desc:
        if any(longer.startswith(m + "_") for longer in keep):
            continue
        keep.append(m)
    return keep


def detect_product_filter(
    query: str,
    product_ids: Iterable[str],
) -> Optional[str]:
    """Return a single product_id if the query unambiguously names one product.

    See module docstring for full behavior.
    """
    if not query.strip():
        return None

    # Cheap check first: comparison queries skip the pattern loop entirely.
    if _COMPARISON_RE.search(query):
        return None

    matches = [pid for pid in product_ids if _build_pattern(pid).search(query)]
    if not matches:
        return None

    deduped = _drop_prefix_redundant(matches)
    if len(deduped) != 1:
        return None

    return deduped[0]
