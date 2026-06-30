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
