"""Path guard：擋下 graph 節點上的任意路徑讀寫。

Graph executor 的 `params` 來自前端 JSON，不可信。任何指向檔案系統的
欄位（`source_path` / `persist_path` / `golden_set_path` …）都要先過
`safe_path()`，把路徑鎖在 Settings 宣告的 allowed roots 之內。

Kind 對應一組白名單根目錄；目前只用 "data" 一種，但保留 kind 參數讓
未來可以再切分（例如 eval-only / write-only）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


class PathNotAllowed(ValueError):
    """Raised when a requested path falls outside the allowed roots."""


def _resolved_roots(roots: Iterable[str]) -> list[Path]:
    out: list[Path] = []
    for r in roots:
        r = (r or "").strip()
        if not r:
            continue
        try:
            out.append(Path(r).resolve())
        except OSError:
            continue
    return out


def safe_path(raw: str, *, allowed_roots: Iterable[str], kind: str = "data") -> Path:
    """Resolve `raw` and ensure it lives under one of `allowed_roots`.

    `.resolve()` collapses `..` and symlinks before the containment check,
    so `./knowledge_base/../../etc` can't sneak past.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise PathNotAllowed(f"{kind} path is empty")

    try:
        resolved = Path(raw).resolve()
    except OSError as e:
        raise PathNotAllowed(f"{kind} path could not be resolved: {raw!r} ({e})")

    roots = _resolved_roots(allowed_roots)
    if not roots:
        raise PathNotAllowed(
            f"no allowed roots configured for {kind}; set RAG_ALLOWED_DATA_ROOTS"
        )

    for root in roots:
        try:
            if resolved == root or resolved.is_relative_to(root):
                return resolved
        except ValueError:
            continue

    raise PathNotAllowed(
        f"{kind} path {resolved} is outside allowed roots: "
        + ", ".join(str(r) for r in roots)
    )
