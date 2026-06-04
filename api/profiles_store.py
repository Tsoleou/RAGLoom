"""
Profile storage (persistence layer).

Layout (per-file):
    config/profiles/<name>.json   ← user-created profiles, content = {nodes, edges}
    config/profiles/_active.txt   ← active profile name (1 line)

The 'default' profile lives in code (_default_chat_graph) — no file.
Legacy single-file config/chat_profiles.json is migrated on first load.

Paths are CWD-relative (resolved against the process working directory, i.e.
the repo root where uvicorn launches) — same as before the split.
"""

import json
import os
import re as _re
import secrets
from pathlib import Path

from api.default_graph import _default_chat_graph, _ensure_graph

_PROFILES_DIR = Path("config/profiles")
_ACTIVE_PATH = _PROFILES_DIR / "_active.txt"
_LEGACY_PROFILES_PATH = Path("config/chat_profiles.json")
_DEFAULT_NAME = "default"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 帶 random suffix 避免並發寫互砍同一個 .tmp
    tmp = path.with_suffix(path.suffix + f".{secrets.token_hex(8)}.tmp")
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise


_PROFILE_NAME_RE = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _is_safe_profile_name(name: str) -> bool:
    """嚴格白名單：開頭英數，後續英數/底線/連字號，長度 1–64。
    擋掉 newline、null byte、前後空白、過長檔名、reserved 名字等病態輸入。"""
    return isinstance(name, str) and bool(_PROFILE_NAME_RE.fullmatch(name))


def _profile_path(name: str) -> Path:
    return _PROFILES_DIR / f"{name}.json"


def _list_user_profile_names() -> list[str]:
    if not _PROFILES_DIR.exists():
        return []
    names = []
    for p in sorted(_PROFILES_DIR.glob("*.json")):
        stem = p.stem
        if stem.startswith("_") or stem.startswith("."):
            continue
        names.append(stem)
    return names


def _read_active_name() -> str:
    if _ACTIVE_PATH.exists():
        name = _ACTIVE_PATH.read_text().strip()
        if name:
            return name
    return _DEFAULT_NAME


def _write_active_name(name: str) -> None:
    _atomic_write_text(_ACTIVE_PATH, name + "\n")


def _read_user_profile_graph(name: str) -> dict | None:
    path = _profile_path(name)
    if not path.exists():
        return None
    try:
        graph = json.loads(path.read_text())
    except Exception as e:
        print(f"[Server] Skipping malformed profile {path.name}: {e}")
        return None
    return graph if isinstance(graph, dict) and graph.get("nodes") else None


def _write_user_profile_graph(name: str, graph: dict) -> None:
    _atomic_write_text(_profile_path(name), json.dumps(graph, ensure_ascii=False, indent=2))


def _delete_user_profile_file(name: str) -> bool:
    path = _profile_path(name)
    if not path.exists():
        return False
    path.unlink()
    return True


def _migrate_legacy_profiles_if_needed() -> None:
    """One-shot: split old config/chat_profiles.json into per-file layout.
    Idempotent — bails out if profiles/ already exists or legacy file is gone."""
    if _PROFILES_DIR.exists():
        return
    if not _LEGACY_PROFILES_PATH.exists():
        return
    try:
        data = json.loads(_LEGACY_PROFILES_PATH.read_text())
    except Exception as e:
        print(f"[Server] Legacy profile migration aborted ({e}); keeping {_LEGACY_PROFILES_PATH}")
        return

    _PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    profiles = data.get("profiles") or {}
    migrated = 0
    for name, profile in profiles.items():
        if name == _DEFAULT_NAME:
            continue  # default lives in code now
        if not _is_safe_profile_name(name):
            print(f"[Server] Skipping unsafe legacy profile name: {name!r}")
            continue
        patched = _ensure_graph(profile)
        graph = patched.get("graph")
        if not isinstance(graph, dict) or not graph.get("nodes"):
            continue
        _write_user_profile_graph(name, graph)
        migrated += 1

    active = data.get("active") or _DEFAULT_NAME
    _write_active_name(active)

    backup = _LEGACY_PROFILES_PATH.with_suffix(_LEGACY_PROFILES_PATH.suffix + ".bak")
    os.replace(_LEGACY_PROFILES_PATH, backup)
    print(f"[Server] Migrated {migrated} profile(s) to {_PROFILES_DIR}/; legacy file backed up at {backup}")


def _load_profiles() -> dict:
    """Assemble {active, profiles:{name:{graph}}} from per-file storage.
    'default' is synthesized from _default_chat_graph().
    Migration is handled at lifespan startup, not here."""
    profiles = {_DEFAULT_NAME: {"graph": _default_chat_graph()}}
    for name in _list_user_profile_names():
        graph = _read_user_profile_graph(name)
        if graph is not None:
            profiles[name] = {"graph": graph}
    active = _read_active_name()
    if active not in profiles:
        active = _DEFAULT_NAME
    return {"active": active, "profiles": profiles}
