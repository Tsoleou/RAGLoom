"""Profile endpoints: list, save, activate, delete chat profiles."""

from fastapi import APIRouter, HTTPException

from api.profiles_store import (
    _DEFAULT_NAME,
    _delete_user_profile_file,
    _is_safe_profile_name,
    _load_profiles,
    _read_active_name,
    _write_active_name,
    _write_user_profile_graph,
)
from api.schemas import ActivateProfileRequest, ChatProfileRequest

router = APIRouter()


@router.get("/api/profiles")
def get_profiles():
    """Return all profiles and the active profile name."""
    return _load_profiles()


@router.post("/api/profiles")
def save_profile(req: ChatProfileRequest):
    """Create or overwrite a named user profile with its full chat graph."""
    if req.name == _DEFAULT_NAME:
        raise HTTPException(status_code=400, detail="'default' is reserved — choose another name.")
    if not _is_safe_profile_name(req.name):
        raise HTTPException(
            status_code=400,
            detail="Profile name must start with a letter/digit and contain only [A-Za-z0-9_-], length 1–64.",
        )
    if not isinstance(req.graph, dict) or not req.graph.get("nodes"):
        raise HTTPException(status_code=400, detail="Profile graph must include nodes.")
    _write_user_profile_graph(req.name, req.graph)
    return {"status": "ok", "name": req.name}


@router.post("/api/profiles/activate")
def activate_profile(req: ActivateProfileRequest):
    """Set the active profile."""
    available = _load_profiles()["profiles"]
    if req.name not in available:
        raise HTTPException(status_code=404, detail=f"Profile '{req.name}' not found")
    _write_active_name(req.name)
    return {"status": "ok", "active": req.name}


@router.delete("/api/profiles/{name}")
def delete_profile(name: str):
    """Delete a user profile (cannot delete 'default')."""
    if name == _DEFAULT_NAME:
        raise HTTPException(status_code=400, detail="Cannot delete the default profile")
    if not _is_safe_profile_name(name):
        raise HTTPException(status_code=400, detail="Invalid profile name")
    if not _delete_user_profile_file(name):
        raise HTTPException(status_code=404, detail=f"Profile '{name}' not found")
    if _read_active_name() == name:
        _write_active_name(_DEFAULT_NAME)
    return {"status": "ok"}
