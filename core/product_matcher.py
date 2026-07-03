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
from typing import Iterable, List, Mapping, Optional


_COMPARISON_RE = re.compile(
    r"比較|差別|差在哪|哪個好|哪一台|哪一個|\bvs\b|\bversus\b|\bcompare\b",
    flags=re.IGNORECASE,
)


# Maps a canonical English brand stem (must match the leading token of a
# product_id) to Chinese aliases users may type. The canonical key is what
# replaces the alias in the query string before pattern matching, so
# 'starforge' must be the prefix of product_ids like 'starforge_x1'.
# Both Traditional and Simplified variants are listed — the persona mirrors the
# visitor's variant, so generated transliterations come in both.
# '流明' is deliberately NOT a luminos alias: it is the Chinese word for the
# lumens unit, so spec sentences like '300流明' would false-match the brand.
DEFAULT_BRAND_ALIASES: dict[str, list[str]] = {
    "starforge":  ["星鋒", "星峰", "星锋"],
    "visionbook": ["維森書", "視覺書", "维森书", "视觉书"],
    "novapad":    ["諾瓦", "諾瓦帕", "诺瓦", "诺瓦帕"],
    "titanbook":  ["泰坦書", "鈦書", "泰坦书", "钛书"],
    "luminos":    ["璐米諾", "璐米诺"],
}


# Canonical display casing per brand stem, for restoring transliterated names
# in generated replies. Keys mirror DEFAULT_BRAND_ALIASES; a stem missing here
# falls back to str.capitalize().
BRAND_DISPLAY: dict[str, str] = {
    "starforge":  "StarForge",
    "visionbook": "VisionBook",
    "novapad":    "NovaPad",
    "titanbook":  "TitanBook",
    "luminos":    "Luminos",
}


def _normalize_aliases(query: str, aliases: Mapping[str, list[str]]) -> str:
    """Replace each alias in the query with its canonical English stem.

    Longest alias first, so a shorter alias that is a prefix of a longer one
    ('諾瓦' vs '諾瓦帕') doesn't clobber the longer match mid-string and leave a
    stray char that breaks the product pattern.
    """
    for canonical, alts in aliases.items():
        for alt in sorted(alts, key=len, reverse=True):
            if alt and alt in query:
                query = query.replace(alt, canonical)
    return query


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
    aliases: Optional[Mapping[str, list[str]]] = None,
) -> Optional[str]:
    """Return a single product_id if the query unambiguously names one product.

    See module docstring for full behavior.
    """
    if not query.strip():
        return None

    # Cheap check first: comparison queries skip the pattern loop entirely.
    # Run before alias normalization so Chinese comparison keywords still match.
    if _COMPARISON_RE.search(query):
        return None

    query = _normalize_aliases(query, aliases if aliases is not None else DEFAULT_BRAND_ALIASES)

    matches = [pid for pid in product_ids if _build_pattern(pid).search(query)]
    if not matches:
        return None

    deduped = _drop_prefix_redundant(matches)
    if len(deduped) != 1:
        return None

    return deduped[0]


def restore_english_names(
    text: str,
    aliases: Optional[Mapping[str, list[str]]] = None,
    display: Optional[Mapping[str, str]] = None,
) -> str:
    """Replace Chinese product-name transliterations with the English original.

    The reverse of _normalize_aliases, applied to *generated* text: the 4B
    generator under the persona's "never switch languages mid-reply" rule
    tends to transliterate product names in Chinese answers (StarForge →
    星鋒). Product names are proper nouns and must stay English; a prompt
    rule alone is not reliable at this model size, so the final reply is
    normalized back in code.

    Longest alias first across ALL brands, same rationale as
    _normalize_aliases. A space is inserted where the restored name would
    fuse with adjacent ASCII alphanumerics (星鋒X1 → 'StarForge X1', not
    'StarForgeX1'); against CJK no space is added, matching how English
    names sit in Chinese prose.
    """
    if not text:
        return text
    alias_map = aliases if aliases is not None else DEFAULT_BRAND_ALIASES
    display_map = display if display is not None else BRAND_DISPLAY

    pairs = [
        (alt, canonical)
        for canonical, alts in alias_map.items()
        for alt in alts
        if alt
    ]
    pairs.sort(key=lambda p: len(p[0]), reverse=True)

    for alt, canonical in pairs:
        if alt not in text:
            continue
        name = display_map.get(canonical) or canonical.capitalize()

        def _sub(m: re.Match, _name: str = name) -> str:
            s = m.string
            before = s[m.start() - 1] if m.start() > 0 else ""
            after = s[m.end()] if m.end() < len(s) else ""
            out = _name
            if before.isascii() and before.isalnum():
                out = " " + out
            if after.isascii() and after.isalnum():
                out = out + " "
            return out

        text = re.sub(re.escape(alt), _sub, text)
    return text


# The CJK char right before a model token being one of these almost always
# means ordinary prose ('這台X1', '哪款Pro'), not a transliterated brand name.
_MENTION_STOPCHARS = frozenset("台款的這那这部機机型號号是有跟和與与比薦荐")


def find_untranslated_mentions(text: str, product_ids: Iterable[str]) -> List[str]:
    """Snippets that look like an *unknown* transliteration of a product name.

    Run AFTER restore_english_names has replaced every known alias: CJK chars
    still fused directly onto a catalog model token (the letter-bearing part
    of a product_id, e.g. 'x1' in starforge_x1) usually mean the generator
    invented a transliteration the alias table doesn't know ('星輝X1').
    Heuristic, log-only — snippets exist so new variants get added to
    DEFAULT_BRAND_ALIASES, never to mutate the reply.
    """
    if not text or not text.strip():
        return []
    tokens: set[str] = set()
    for pid in product_ids:
        for part in str(pid).split("_")[1:]:
            # Letter-bearing only: bare numbers ('17', '9000') sit next to CJK
            # in perfectly normal spec prose (續航17小時).
            if len(part) >= 2 and any(c.isalpha() for c in part):
                tokens.add(part)
    if not tokens:
        return []
    body = "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True))
    pattern = re.compile(
        rf"([一-鿿]{{2,6}})({body})\b", flags=re.IGNORECASE | re.ASCII
    )
    return [
        m.group(0)
        for m in pattern.finditer(text)
        if m.group(1)[-1] not in _MENTION_STOPCHARS
    ]


def find_products_in_text(
    text: str,
    product_ids: Iterable[str],
    aliases: Optional[Mapping[str, list[str]]] = None,
) -> List[str]:
    """Return every product_id whose name appears in `text`.

    Unlike detect_product_filter (which routes a *query* to a single product
    and bails on comparison phrasing), this scans an arbitrary text — typically
    a generated reply — and returns *all* products named in it, so the caller
    can attach one image per product an answer actually talks about. Reuses the
    same alias normalization + word-boundary patterns, so Chinese aliases and
    'VisionBook 17' spacing variants match identically.

    Prefix dedup is *span-aware* here, not length-based like detect_product_filter:
    a bare stem ('visionbook', itself a real product) is dropped only when every
    one of its match spans sits inside a longer model's span — i.e. it only ever
    appeared as the prefix of 'visionbook 17', never on its own. A base product
    genuinely named alongside its submodel is kept; a submodel name is never
    mis-attributed to the base stem.

    Result order follows the given product_ids for determinism.
    """
    if not text or not text.strip():
        return []

    product_ids = list(product_ids)  # consumed twice below; never trust an iterator
    text = _normalize_aliases(text, aliases if aliases is not None else DEFAULT_BRAND_ALIASES)

    # All match spans per id (finditer, so a standalone bare-stem mention is
    # distinguishable from one that is only the prefix of a longer model name).
    spans: dict[str, list[tuple[int, int]]] = {}
    for pid in product_ids:
        found = [m.span() for m in _build_pattern(pid).finditer(text)]
        if found:
            spans[pid] = found
    if not spans:
        return []

    keep: set[str] = set()
    for pid, my_spans in spans.items():
        # Keep pid if any of its spans is NOT contained in a strictly-longer
        # matched id's span (unrelated stems can't overlap, so containment alone
        # is the precise "this was just a prefix" test — no startswith needed).
        for s, e in my_spans:
            covered = any(
                ls <= s and e <= le
                for longer, l_spans in spans.items()
                if len(longer) > len(pid)
                for ls, le in l_spans
            )
            if not covered:
                keep.add(pid)
                break

    seen: set[str] = set()
    ordered: List[str] = []
    for pid in product_ids:
        if pid in keep and pid not in seen:
            seen.add(pid)
            ordered.append(pid)
    return ordered
