"""Offline tests for passphrase rotation (two-tier key wrapping).

Rotating must keep all existing ciphertext readable (the master key is
unchanged — only its wrapper changes), reject a wrong current passphrase
without touching the keystore, and upgrade a legacy v1 keystore to v2."""

import base64
import importlib
import json
import secrets

import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def crypto(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_KB_KEYSTORE", str(tmp_path / "keystore.json"))
    from core import kb_crypto
    importlib.reload(kb_crypto)
    kb_crypto.lock()
    yield kb_crypto
    kb_crypto.lock()


def test_init_creates_v2_wrapped_keystore(crypto):
    crypto.init_keystore("oldpass123")
    ks = json.loads(crypto._keystore_file().read_text())
    assert ks["version"] == 2 and "wrapped_key" in ks and "verifier" not in ks


def test_rotate_preserves_data_and_revokes_old(crypto):
    crypto.init_keystore("oldpass123")
    token = crypto.encrypt_text("機密 SX-42")
    crypto.lock()

    assert crypto.change_passphrase("oldpass123", "newpass456") is True
    # data still readable — master key unchanged, only re-wrapped
    assert crypto.decrypt_text(token) == "機密 SX-42"

    crypto.lock()
    assert crypto.unlock("oldpass123") is False   # old passphrase revoked
    assert crypto.unlock("newpass456") is True
    assert crypto.decrypt_text(token) == "機密 SX-42"


def test_rotate_wrong_old_is_noop(crypto):
    crypto.init_keystore("oldpass123")
    before = crypto._keystore_file().read_text()
    assert crypto.change_passphrase("WRONG", "newpass456") is False
    assert crypto._keystore_file().read_text() == before  # untouched


def test_rotate_rejects_weak_new(crypto):
    crypto.init_keystore("oldpass123")
    with pytest.raises(ValueError):
        crypto.change_passphrase("oldpass123", "short")


def test_v1_keystore_upgrades_to_v2_on_rotate(crypto):
    # Hand-craft a legacy v1 keystore (passphrase-derived key used directly).
    salt = secrets.token_bytes(16)
    kek = crypto._derive_key("v1pass789", salt, n=crypto._SCRYPT_N,
                             r=crypto._SCRYPT_R, p=crypto._SCRYPT_P)
    v1 = {
        "version": 1, "kdf": "scrypt",
        "salt": base64.b64encode(salt).decode(),
        "n": crypto._SCRYPT_N, "r": crypto._SCRYPT_R, "p": crypto._SCRYPT_P,
        "verifier": Fernet(kek).encrypt(crypto._VERIFIER_PLAINTEXT).decode(),
    }
    crypto._keystore_file().write_text(json.dumps(v1))

    assert crypto.unlock("v1pass789") is True   # v1 still readable
    token = crypto.encrypt_text("v1 機密")
    crypto.lock()

    assert crypto.change_passphrase("v1pass789", "v2pass123") is True
    ks = json.loads(crypto._keystore_file().read_text())
    assert ks["version"] == 2 and "wrapped_key" in ks and "verifier" not in ks
    assert crypto.decrypt_text(token) == "v1 機密"  # legacy data preserved

    crypto.lock()
    assert crypto.unlock("v1pass789") is False
    assert crypto.unlock("v2pass123") is True
    assert crypto.decrypt_text(token) == "v1 機密"
