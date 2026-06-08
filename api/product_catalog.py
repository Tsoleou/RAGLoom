"""Product display-name lookup for the analytics dashboard.

Marketing reads product *names*, not source filenames or snake_case ids. The
knowledge base ships a catalog CSV (`_reference/product_comparison.csv`) keyed
by `product_id` with a human 型號 (model) column — we load it once and expose a
`product_id -> display name` map.

Kept separate from query_log so the write path (logging) stays dependency-free
and only the read path (stats/recent) pays the CSV load.
"""

import csv
import os

_CSV_PATH = os.environ.get(
    "RAG_PRODUCT_CATALOG_CSV",
    "./knowledge_base/_reference/product_comparison.csv",
)

_cache: dict[str, str] | None = None


def _prettify(product_id: str) -> str:
    """Fallback name when a product_id isn't in the CSV: snake_case → Title."""
    return product_id.replace("_", " ").title()


def name_map() -> dict[str, str]:
    """Return (and cache) the product_id → display-name map from the CSV.

    Display name prefers the 型號 (model) column, then 系列 (series). Missing or
    unreadable CSV degrades to an empty map; callers fall back to _prettify.
    """
    global _cache
    if _cache is not None:
        return _cache
    out: dict[str, str] = {}
    try:
        with open(_CSV_PATH, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                pid = (row.get("product_id") or "").strip()
                if not pid:
                    continue
                name = (row.get("型號") or row.get("系列") or "").strip()
                out[pid] = name or _prettify(pid)
    except (OSError, csv.Error) as e:
        print(f"[ProductCatalog] WARNING: could not load {_CSV_PATH}: {e}")
    _cache = out
    return out


def display_name(product_id: str | None) -> str | None:
    """Map one product_id to its display name, prettifying unknown ids."""
    if not product_id:
        return None
    return name_map().get(product_id) or _prettify(product_id)
