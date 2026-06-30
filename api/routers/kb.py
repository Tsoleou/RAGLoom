"""Knowledge-base encryption + document-injection endpoints.

Two operator capabilities live here:

  - Lock state: GET /api/kb/status, POST /api/kb/unlock, POST /api/kb/lock.
    The unlock passphrase derives the at-rest encryption key (held in memory
    only) and, on success, brings the chat pipeline online.

  - Document injection: list / add / delete files under knowledge_base/, each
    written through the same encryption layer and re-ingested into the live
    collection so a freshly-added document is immediately answerable.

All mutating endpoints require the KB to be unlocked when encryption is enabled
— nothing can read or write encrypted content without the key. Path containment
reuses the existing path_guard so an injected filename can't escape the KB dir.
"""

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from api.rate_limit import FailedAttemptLimiter
from api.routers import chat as chat_router
from api.schemas import ChangePassphraseRequest, KBDocumentRequest, UnlockRequest
from core import kb_crypto
from core.loader import SUPPORTED_EXTENSIONS
from core.path_guard import PathNotAllowed, safe_path

router = APIRouter()

# Throttles brute-force / scrypt-CPU abuse of the unauthenticated unlock endpoint.
# Keyed by client IP; a correct passphrase clears the key.
_unlock_limiter = FailedAttemptLimiter()

# Documents are injected here only — a single fixed root, never client-chosen,
# so injection can't write into eval/ or chroma_db/ even though those are also
# allowed graph roots.
_KB_ROOT = "./knowledge_base"
# Conservative filename rule: a single path segment, known extension, no dotfiles
# or separators. Keeps the product_<id> convention loaders rely on intact.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
# Upload/paste size ceiling (mirrors the paste limit in api/schemas.py). Booth
# docs are small; the cap bounds the encrypt+chunk+embed memory per request.
_MAX_DOC_BYTES = 2_000_000


def _require_unlocked() -> None:
    if kb_crypto.is_enabled() and not kb_crypto.is_unlocked():
        raise HTTPException(status_code=423, detail="KB encryption locked — unlock first")


def _safe_kb_path(filename: str) -> Path:
    """Validate the filename and resolve it to a path confined under the KB root.

    Rejects path separators, dotfiles, unknown extensions, and anything that
    resolves outside knowledge_base/."""
    if not _SAFE_NAME.match(filename) or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported type {ext or '(none)'}; allowed: {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )
    try:
        return safe_path(
            f"{_KB_ROOT}/{filename}",
            allowed_roots=[_KB_ROOT],
            kind="kb document",
        )
    except PathNotAllowed as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Lock state ──────────────────────────────────────────────────────

@router.get("/api/kb/status")
def kb_status():
    """Whether encryption is configured and currently unlocked. Reachable while
    locked (it's how the UI decides to show the unlock prompt)."""
    return kb_crypto.status()


@router.post("/api/kb/unlock")
def kb_unlock(req: UnlockRequest, request: Request):
    """Derive the key from the passphrase and, on success, bring chat online."""
    if not kb_crypto.is_enabled():
        raise HTTPException(status_code=400, detail="KB encryption is not configured")
    # Throttle before the expensive scrypt: a locked-out client is rejected
    # without deriving a key, so brute-force and CPU-exhaustion both stall.
    client = request.client.host if request.client else "unknown"
    wait = _unlock_limiter.retry_after(client)
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail="Too many unlock attempts; try again later",
            headers={"Retry-After": str(int(wait) + 1)},
        )
    if not kb_crypto.unlock(req.passphrase):
        _unlock_limiter.record_failure(client)
        raise HTTPException(status_code=401, detail="Incorrect passphrase")
    _unlock_limiter.record_success(client)
    # Now that the key is loaded, build/attach the chat pipeline so the kiosk
    # works without a separate Load-KB step.
    try:
        count = chat_router.init_chat_pipe_if_needed()
    except Exception as e:  # noqa: BLE001 — unlock still succeeded; surface init issue
        return {"status": "ok", "unlocked": True, "chunks": -1, "warning": str(e)}
    return {"status": "ok", "unlocked": True, "chunks": count}


@router.post("/api/kb/lock")
def kb_lock():
    """Drop the in-memory key. Chat goes offline until the next unlock."""
    kb_crypto.lock()
    chat_router.chat_pipe = None
    return {"status": "ok", "unlocked": False}


@router.post("/api/kb/change-passphrase")
def kb_change_passphrase(req: ChangePassphraseRequest):
    """Rotate the encryption passphrase (re-wraps the master key; no data is
    re-encrypted). Admin-gated like other mutating endpoints; the caller proves
    authorization by supplying the current passphrase."""
    if not kb_crypto.is_enabled():
        raise HTTPException(status_code=400, detail="KB encryption is not configured")
    try:
        ok = kb_crypto.change_passphrase(req.old_passphrase, req.new_passphrase)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=401, detail="Current passphrase is incorrect")
    return {"status": "ok"}


# ── Document injection ──────────────────────────────────────────────

def _list_documents() -> list[dict]:
    root = Path(_KB_ROOT)
    out: list[dict] = []
    if not root.is_dir():
        return out
    for f in sorted(root.iterdir()):
        if not f.is_file() or f.name.startswith("."):
            continue
        if f.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        raw = f.read_bytes()
        out.append({
            "filename": f.name,
            "type": f.suffix.lstrip(".").lower(),
            "bytes": len(raw),
            "encrypted": kb_crypto.is_encrypted_bytes(raw),
        })
    return out


@router.get("/api/kb/documents")
def list_documents():
    """List the source documents currently in the knowledge base."""
    return {"documents": _list_documents()}


def _write_and_ingest(filename: str, data: bytes) -> dict:
    """Encrypt+write a document and re-ingest it. Shared by paste + upload."""
    path = _safe_kb_path(filename)
    # PDFs/CSVs may not be valid UTF-8 — but text we accept as a string must be.
    path.write_bytes(kb_crypto.encrypt_bytes(data))
    try:
        chunks = chat_router.reingest_file(str(path))
    except Exception as e:  # noqa: BLE001 — file is saved; report ingest failure
        raise HTTPException(status_code=500, detail=f"Saved but ingest failed: {e}")
    return {"status": "ok", "filename": filename, "chunks": chunks}


@router.post("/api/kb/documents")
def add_document_text(req: KBDocumentRequest):
    """Inject a document from pasted text (txt/md/csv)."""
    _require_unlocked()
    ext = Path(req.filename).suffix.lower()
    if ext == ".pdf":
        raise HTTPException(status_code=400, detail="Use file upload (PUT) for PDF")
    return _write_and_ingest(req.filename, req.content.encode("utf-8"))


@router.put("/api/kb/documents/{filename}")
async def upload_document(filename: str, request: Request):
    """Inject a document from a raw file upload (any supported type, incl. PDF).

    The file bytes are the request body — avoids a multipart dependency. The
    frontend sends the File object directly as the body."""
    _require_unlocked()
    # Enforce the size ceiling WITHOUT buffering an unbounded body first: reject
    # early on a declared Content-Length, then read the stream with a running cap
    # so a missing or under-declared length (chunked / spoofed) still can't
    # balloon memory before the check fires.
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            declared_len = int(declared)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if declared_len > _MAX_DOC_BYTES:
            raise HTTPException(status_code=413, detail="File too large (max 2 MB)")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > _MAX_DOC_BYTES:
            raise HTTPException(status_code=413, detail="File too large (max 2 MB)")
    if not buf:
        raise HTTPException(status_code=400, detail="Empty upload")
    return _write_and_ingest(filename, bytes(buf))


@router.delete("/api/kb/documents/{filename}")
def delete_document(filename: str):
    """Remove a document from the KB and drop its chunks from the collection."""
    _require_unlocked()
    path = _safe_kb_path(filename)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"No such document: {filename}")
    path.unlink()
    chat_router.remove_file_chunks(filename)
    return {"status": "ok", "filename": filename}
