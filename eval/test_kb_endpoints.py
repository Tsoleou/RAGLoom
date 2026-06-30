"""Offline tests for the KB lock/unlock + document-injection HTTP endpoints.

Covers the state machine and input guards (status, 423-while-locked, 401 on
wrong passphrase, 400 on bad filenames) — the parts that short-circuit before
any embed/ingest, so they need neither Ollama nor chroma."""

import importlib

import pytest


@pytest.fixture
def crypto(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_KB_KEYSTORE", str(tmp_path / "keystore.json"))
    from core import kb_crypto
    importlib.reload(kb_crypto)
    kb_crypto.lock()
    yield kb_crypto
    kb_crypto.lock()


def test_endpoints_lock_unlock_and_guards(crypto, tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_QUERY_LOG_DB", str(tmp_path / "q.db"))
    crypto.init_keystore("operator-pass")
    crypto.lock()  # boot state: enabled but locked

    # Reload the router so it binds to the reloaded crypto module.
    import api.routers.kb as kb_router
    importlib.reload(kb_router)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(kb_router.router)
    client = TestClient(app)

    # status reflects locked
    assert client.get("/api/kb/status").json() == {"enabled": True, "unlocked": False}

    # mutating endpoints are gated while locked (423), before any ingest runs
    r = client.post("/api/kb/documents", json={"filename": "a.txt", "content": "hi"})
    assert r.status_code == 423
    assert client.delete("/api/kb/documents/a.txt").status_code == 423

    # wrong passphrase rejected
    assert client.post("/api/kb/unlock", json={"passphrase": "nope"}).status_code == 401
    assert not crypto.is_unlocked()

    # bad filenames rejected with 400 (after unlock so we pass the lock gate)
    crypto.unlock("operator-pass")
    for bad in ("../escape.txt", "no_ext", "evil.exe"):
        r = client.post("/api/kb/documents", json={"filename": bad, "content": "x"})
        assert r.status_code == 400, bad

    # change-passphrase: wrong current → 401; correct → 200 and new one works
    assert client.post("/api/kb/change-passphrase", json={
        "old_passphrase": "WRONG", "new_passphrase": "brand-new-pass"}).status_code == 401
    assert client.post("/api/kb/change-passphrase", json={
        "old_passphrase": "operator-pass", "new_passphrase": "brand-new-pass"}).status_code == 200
    crypto.lock()
    assert crypto.unlock("operator-pass") is False
    assert crypto.unlock("brand-new-pass") is True


def test_encryption_gates_admin_surface_without_admin_password(crypto, monkeypatch):
    """S1: with encryption enabled but no RAG_ADMIN_PASSWORD set, the admin HTTP
    surface must NOT fall through to same-origin — an uncredentialed LAN request
    (TestClient sends no Origin, so is_same_origin() is True, exactly the LAN
    case) has to be rejected, while the unlock passphrase authenticates."""
    import base64

    crypto.init_keystore("operator-pass")
    crypto.lock()  # boot: enabled but locked

    import api.auth as auth
    import api.routers.kb as kb_router
    importlib.reload(kb_router)  # rebind router to the reloaded crypto module
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    saved = (auth._settings.api_admin_password, auth._settings.api_local_token)
    auth._settings.api_admin_password = ""   # the vulnerable config: no admin password
    auth._settings.api_local_token = ""
    try:
        app = FastAPI()
        app.add_middleware(auth.LocalTokenMiddleware)
        app.include_router(kb_router.router)
        client = TestClient(app)

        # kiosk endpoint stays open so the UnlockGate can boot
        assert client.get("/api/kb/status").status_code == 200

        # admin endpoint, uncredentialed same-origin → 401 (the S1 hole, closed).
        # Must be rejected at the auth layer, BEFORE the 423 lock gate.
        assert client.post("/api/kb/lock").status_code == 401

        # right passphrase but still locked → can't be verified yet → 401
        basic = base64.b64encode(b":operator-pass").decode()
        assert client.post("/api/kb/lock", headers={"Authorization": f"Basic {basic}"}).status_code == 401

        # once unlocked, the passphrase doubles as the admin Basic-Auth credential
        crypto.unlock("operator-pass")
        assert client.post("/api/kb/lock", headers={"Authorization": f"Basic {basic}"}).status_code == 200
        # wrong passphrase is still rejected
        crypto.unlock("operator-pass")
        bad = base64.b64encode(b":wrong-pass").decode()
        assert client.post("/api/kb/lock", headers={"Authorization": f"Basic {bad}"}).status_code == 401
    finally:
        auth._settings.api_admin_password, auth._settings.api_local_token = saved


def test_unlock_endpoint_rate_limits_brute_force(crypto):
    """S2: repeated wrong passphrases lock the unlock endpoint out (429) before
    running scrypt, and a correct passphrase is blocked while locked out."""
    crypto.init_keystore("operator-pass")
    crypto.lock()

    import api.routers.kb as kb_router
    importlib.reload(kb_router)  # fresh module-level limiter
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(kb_router.router)
    client = TestClient(app)

    # default threshold is 5: the first 5 wrong tries are 401, then locked out
    codes = [
        client.post("/api/kb/unlock", json={"passphrase": "wrong"}).status_code
        for _ in range(6)
    ]
    assert codes[:5] == [401] * 5
    assert codes[5] == 429

    # even the CORRECT passphrase is refused while the lockout window is open
    r = client.post("/api/kb/unlock", json={"passphrase": "operator-pass"})
    assert r.status_code == 429
    assert "Retry-After" in r.headers
    assert not crypto.is_unlocked()


def test_upload_size_cap_rejects_before_ingest(crypto):
    """S4: oversized uploads are rejected by the size cap BEFORE _write_and_ingest
    runs, and without buffering the whole body. Encryption stays disabled here so
    _require_unlocked passes; the 413/400 short-circuit needs neither Ollama nor
    chroma."""
    import api.routers.kb as kb_router
    importlib.reload(kb_router)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(kb_router.router)
    client = TestClient(app)

    over = kb_router._MAX_DOC_BYTES + 1

    # declared Content-Length over the cap → 413 (TestClient sets Content-Length)
    assert client.put("/api/kb/documents/big.txt", content=b"x" * over).status_code == 413

    # no/under-declared length (chunked stream) still capped by the running total
    def _chunked():
        for _ in range(over // 100_000 + 1):
            yield b"x" * 100_000
    assert client.put("/api/kb/documents/chunked.txt", content=_chunked()).status_code == 413

    # empty body → 400
    assert client.put("/api/kb/documents/empty.txt", content=b"").status_code == 400
