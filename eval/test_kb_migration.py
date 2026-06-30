"""Offline test for the one-time encryption migration (tools/encrypt_kb.py):
existing plaintext query-log rows get encrypted in place, idempotently."""

import importlib
import sqlite3

import pytest


@pytest.fixture
def crypto(tmp_path, monkeypatch):
    """Temp keystore + a freshly-imported, locked kb_crypto. Locks on teardown
    so global key state never leaks into other (encryption-disabled) tests."""
    monkeypatch.setenv("RAG_KB_KEYSTORE", str(tmp_path / "keystore.json"))
    from core import kb_crypto
    importlib.reload(kb_crypto)
    kb_crypto.lock()
    yield kb_crypto
    kb_crypto.lock()


def test_migration_encrypts_existing_query_rows(crypto, tmp_path, monkeypatch):
    db = tmp_path / "q.db"
    monkeypatch.setenv("RAG_QUERY_LOG_DB", str(db))
    # Seed a PLAINTEXT row while encryption is still disabled.
    from api import query_log
    importlib.reload(query_log)
    query_log.log_query(
        query="舊問題", response={"retrieval": [], "guards": []},
        profile="default", model="m", latency_ms=5, status="ok",
    )
    raw = sqlite3.connect(str(db)).execute("SELECT query FROM queries").fetchone()[0]
    assert raw == "舊問題"  # confirmed plaintext on disk

    # Enable encryption, then run the migration's query-log encryptor.
    crypto.init_keystore("pw-pw-pw-pw")
    import tools.encrypt_kb as mig
    mig._encrypt_query_log()
    raw2 = sqlite3.connect(str(db)).execute("SELECT query FROM queries").fetchone()[0]
    assert raw2.startswith("RLENC1:") and "舊問題" not in raw2  # now ciphertext

    importlib.reload(query_log)
    assert query_log.fetch_recent(limit=1)[0]["query"] == "舊問題"  # decrypts on read

    # Idempotent: a second pass must not double-encrypt.
    mig._encrypt_query_log()
    assert query_log.fetch_recent(limit=1)[0]["query"] == "舊問題"
