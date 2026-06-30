"""Offline tests for at-rest KB encryption: crypto core, loader decrypt,
query-log encryption, and the lock/unlock + injection endpoints.

No Ollama / no chroma — covers the crypto boundary and HTTP state machine. The
actual embed+ingest on document upload is exercised separately (needs Ollama).
"""

import importlib

import pytest


@pytest.fixture
def crypto(tmp_path, monkeypatch):
    """Point the keystore at a temp file and hand back a freshly-imported,
    locked kb_crypto module. Always locks on teardown so global key state never
    leaks into other tests (which run with encryption disabled)."""
    monkeypatch.setenv("RAG_KB_KEYSTORE", str(tmp_path / "keystore.json"))
    from core import kb_crypto
    importlib.reload(kb_crypto)
    kb_crypto.lock()
    yield kb_crypto
    kb_crypto.lock()


# ── Crypto core ─────────────────────────────────────────────────────

def test_disabled_is_passthrough(crypto):
    assert not crypto.is_enabled()
    assert crypto.encrypt_text("hi") == "hi"
    assert crypto.decrypt_text("hi") == "hi"
    assert crypto.encrypt_bytes(b"x") == b"x"
    assert crypto.decrypt_bytes(b"x") == b"x"


def test_text_round_trip_and_envelope(crypto):
    crypto.init_keystore("correct horse battery")
    assert crypto.is_enabled() and crypto.is_unlocked()
    token = crypto.encrypt_text("機密：型號 SW-9000")
    assert token.startswith("RLENC1:")
    assert "SW-9000" not in token            # ciphertext, not plaintext
    assert crypto.decrypt_text(token) == "機密：型號 SW-9000"


def test_cipher_rebuilt_on_reunlock_decrypts_old_ciphertext(crypto):
    # The Fernet cipher is cached per unlock; locking clears it and a re-unlock
    # rebuilds it from the same master key, so ciphertext from before the lock
    # must still decrypt (guards the cached-cipher optimization).
    crypto.init_keystore("correct horse battery")
    token = crypto.encrypt_text("機密：型號 SW-9000")
    crypto.lock()
    assert crypto.unlock("correct horse battery") is True
    assert crypto.decrypt_text(token) == "機密：型號 SW-9000"


def test_legacy_plaintext_passes_through_decrypt(crypto):
    crypto.init_keystore("pw-pw-pw-pw")
    # A value without the envelope prefix (pre-encryption row) is returned as-is.
    assert crypto.decrypt_text("just plain text") == "just plain text"


def test_bytes_round_trip(crypto):
    crypto.init_keystore("pw-pw-pw-pw")
    enc = crypto.encrypt_bytes(b"%PDF-1.4 secret")
    assert crypto.is_encrypted_bytes(enc)
    assert crypto.decrypt_bytes(enc) == b"%PDF-1.4 secret"


def test_wrong_passphrase_rejected(crypto):
    crypto.init_keystore("right-pass")
    crypto.lock()
    assert crypto.unlock("wrong-pass") is False
    assert not crypto.is_unlocked()
    assert crypto.unlock("right-pass") is True


def test_locked_decrypt_raises_but_plaintext_ok(crypto):
    crypto.init_keystore("pw-pw-pw-pw")
    token = crypto.encrypt_text("secret")
    crypto.lock()
    with pytest.raises(crypto.KBLocked):
        crypto.decrypt_text(token)
    # non-enveloped values never need the key, so they still pass through
    assert crypto.decrypt_text("plain") == "plain"


def test_init_refuses_to_clobber(crypto):
    crypto.init_keystore("pw-pw-pw-pw")
    with pytest.raises(FileExistsError):
        crypto.init_keystore("another")


def test_verify_passphrase_only_when_unlocked(crypto):
    crypto.init_keystore("operator-pass")
    assert crypto.verify_passphrase("operator-pass")
    assert not crypto.verify_passphrase("nope")
    crypto.lock()
    assert not crypto.verify_passphrase("operator-pass")  # no in-memory secret


# ── Loader transparent decrypt ──────────────────────────────────────

def test_loader_decrypts_encrypted_file(crypto, tmp_path):
    crypto.init_keystore("pw-pw-pw-pw")
    from core import loader
    p = tmp_path / "product_widget.txt"
    p.write_bytes(crypto.encrypt_bytes("Widget weighs 0.8kg".encode("utf-8")))
    assert b"0.8kg" not in p.read_bytes()        # on-disk ciphertext
    doc = loader.load_file(str(p))
    assert doc.content == "Widget weighs 0.8kg"   # transparently decrypted
    assert doc.metadata["product_id"] == "widget"


def test_loader_raises_when_locked(crypto, tmp_path):
    crypto.init_keystore("pw-pw-pw-pw")
    from core import loader
    p = tmp_path / "secret.txt"
    p.write_bytes(crypto.encrypt_bytes(b"confidential"))
    crypto.lock()
    with pytest.raises(crypto.KBLocked):
        loader.load_file(str(p))


# ── Query-log encryption round-trip ─────────────────────────────────

def test_query_log_encrypts_then_decrypts(crypto, tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_QUERY_LOG_DB", str(tmp_path / "q.db"))
    crypto.init_keystore("pw-pw-pw-pw")
    from api import query_log
    importlib.reload(query_log)

    query_log.log_query(
        query="這台多重？", response={"retrieval": [], "guards": []},
        profile="default", model="gemma3:4b", latency_ms=10, status="ok",
    )
    # Raw column is ciphertext on disk…
    import sqlite3
    raw = sqlite3.connect(str(tmp_path / "q.db")).execute(
        "SELECT query FROM queries"
    ).fetchone()[0]
    assert raw.startswith("RLENC1:") and "多重" not in raw
    # …but the read path returns plaintext.
    recent = query_log.fetch_recent(limit=5)
    assert recent and recent[0]["query"] == "這台多重？"


# Migration (tools/) and HTTP-endpoint behavior are covered in
# eval/test_kb_migration.py and eval/test_kb_endpoints.py respectively, so each
# lands in the same commit as the code it exercises.
