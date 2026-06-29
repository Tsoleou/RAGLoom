"""Knowledge-base at-rest encryption.

Encrypts document *bodies* only — the confidential payload — while leaving the
fields retrieval depends on (``product_id``, ``type``, ``filename``, the spec
table) in plaintext so metadata-filtered queries and the numeric constraint
filter keep working. Three surfaces share this one crypto boundary:

  - source files under ``knowledge_base/`` (decrypted by core/loader.py on read)
  - the chroma ``documents`` field — chunk full text (core/vector_store.py)
  - the query-log ``query`` / ``detail`` columns (api/query_log.py)

Key model — *hybrid operator-unlock* (chosen for disk-theft resistance):
  - A passphrase is entered at runtime via ``POST /api/unlock``; it is NEVER
    written to disk. The derived key lives in process memory until ``lock()``
    or shutdown.
  - The on-disk keystore holds only a KDF salt + parameters + a *verifier*
    token (a known plaintext encrypted under the key). It contains neither the
    passphrase nor the key, so copying the whole disk yields ciphertext with no
    way to read it.
  - Cost of this property: someone must unlock once per boot; the unattended
    kiosk auto-init is intentionally gated behind unlock.

Backward compatibility: when no keystore exists, encryption is DISABLED and
every function here is a transparent pass-through. Existing plaintext
deployments — and the entire offline test suite — are unaffected.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

# Envelope prefix marking an encrypted payload. Lets decrypt_* transparently
# pass through legacy plaintext (no prefix) during/after migration, and lets a
# half-migrated chroma collection be read without crashing.
_MAGIC_TEXT = "RLENC1:"
_MAGIC_BYTES = b"RLENC1\n"

# Constant the verifier round-trips through, to check an entered passphrase is
# the right one before trusting the derived key.
_VERIFIER_PLAINTEXT = b"RAGLOOM_KB_VERIFIER_v1"

# scrypt cost parameters. n=2**15 keeps a single derivation well under ~150ms on
# a laptop — fine for a once-per-boot unlock — while staying memory-hard.
_SCRYPT_N = 1 << 15
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32

_KEYSTORE_PATH = os.environ.get("RAG_KB_KEYSTORE", "./config/kb_keystore.json")


class KBLocked(Exception):
    """Raised when an encrypt/decrypt is attempted while encryption is enabled
    but the key has not been loaded (server booted, operator hasn't unlocked)."""


# ── In-memory key state (never persisted) ───────────────────────────
_key: bytes | None = None          # Fernet key (url-safe base64 of scrypt output)
_passphrase: str | None = None     # kept so admin Basic Auth can reuse one secret


def _keystore_file() -> Path:
    return Path(os.environ.get("RAG_KB_KEYSTORE", _KEYSTORE_PATH))


def _derive_key(passphrase: str, salt: bytes, *, n: int, r: int, p: int) -> bytes:
    """scrypt(passphrase) → 32 bytes → url-safe base64 (a Fernet key)."""
    # maxmem must exceed scrypt's working set (≈128*n*r bytes ≈ 33 MB at our
    # params); OpenSSL's default 32 MB cap would otherwise reject it.
    raw = hashlib.scrypt(
        passphrase.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=_KEY_LEN,
        maxmem=128 * n * r * 2,
    )
    return base64.urlsafe_b64encode(raw)


# ── Status ──────────────────────────────────────────────────────────

def is_enabled() -> bool:
    """True when a keystore exists — i.e. the KB is configured for encryption."""
    return _keystore_file().is_file()


def is_unlocked() -> bool:
    """True when the key is loaded in memory and crypto can run."""
    return _key is not None


def status() -> dict:
    """Compact state for the unlock UI: {enabled, unlocked}."""
    return {"enabled": is_enabled(), "unlocked": is_unlocked()}


# ── Keystore lifecycle ──────────────────────────────────────────────

def init_keystore(passphrase: str) -> None:
    """First-time setup: derive a key from ``passphrase`` and write a keystore
    holding only salt + params + verifier. Also loads the key into memory so the
    caller can immediately encrypt during migration.

    Raises FileExistsError if a keystore is already present (refuse to clobber —
    overwriting the salt would orphan every previously-encrypted byte)."""
    global _key, _passphrase
    path = _keystore_file()
    if path.is_file():
        raise FileExistsError(f"keystore already exists: {path}")
    if not passphrase:
        raise ValueError("passphrase must not be empty")

    salt = secrets.token_bytes(16)
    key = _derive_key(passphrase, salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    verifier = Fernet(key).encrypt(_VERIFIER_PLAINTEXT).decode("ascii")

    keystore = {
        "version": 1,
        "kdf": "scrypt",
        "salt": base64.b64encode(salt).decode("ascii"),
        "n": _SCRYPT_N,
        "r": _SCRYPT_R,
        "p": _SCRYPT_P,
        "verifier": verifier,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(keystore, indent=2), encoding="utf-8")
    _key = key
    _passphrase = passphrase
    print(f"[KBCrypto] Keystore initialized at {path}")


def unlock(passphrase: str) -> bool:
    """Derive the key from ``passphrase`` and load it into memory if it matches
    the keystore verifier. Returns True on success, False on wrong passphrase.

    Raises FileNotFoundError if encryption isn't configured (no keystore)."""
    global _key, _passphrase
    path = _keystore_file()
    if not path.is_file():
        raise FileNotFoundError(f"no keystore at {path}; encryption not configured")

    ks = json.loads(path.read_text(encoding="utf-8"))
    salt = base64.b64decode(ks["salt"])
    key = _derive_key(
        passphrase, salt,
        n=ks.get("n", _SCRYPT_N), r=ks.get("r", _SCRYPT_R), p=ks.get("p", _SCRYPT_P),
    )
    try:
        if Fernet(key).decrypt(ks["verifier"].encode("ascii")) != _VERIFIER_PLAINTEXT:
            return False
    except (InvalidToken, ValueError, KeyError):
        return False

    _key = key
    _passphrase = passphrase
    print("[KBCrypto] Unlocked")
    return True


def lock() -> None:
    """Drop the in-memory key/passphrase. Subsequent crypto raises KBLocked."""
    global _key, _passphrase
    _key = None
    _passphrase = None
    print("[KBCrypto] Locked")


def verify_passphrase(candidate: str) -> bool:
    """Cheap constant-time check used by admin Basic Auth so the operator unlock
    passphrase doubles as the admin password. Only valid once unlocked — we
    compare against the in-memory passphrase rather than re-running scrypt on
    every request."""
    if _passphrase is None or not candidate:
        return False
    return hmac.compare_digest(candidate, _passphrase)


# ── Text crypto (chunk documents, query-log fields) ─────────────────

def encrypt_text(plaintext: str) -> str:
    """Wrap ``plaintext`` into an enveloped token. Pass-through when encryption
    is disabled. Raises KBLocked when enabled but not unlocked."""
    if not is_enabled():
        return plaintext
    if _key is None:
        raise KBLocked("KB encryption is enabled but locked")
    token = Fernet(_key).encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _MAGIC_TEXT + token


def is_encrypted_text(value: str) -> bool:
    """True if ``value`` already carries the text envelope prefix."""
    return isinstance(value, str) and value.startswith(_MAGIC_TEXT)


def decrypt_text(value: str) -> str:
    """Inverse of :func:`encrypt_text`. Values without the envelope prefix are
    returned unchanged (legacy plaintext / mixed-state collections), so this is
    safe to call indiscriminately on read paths. Raises KBLocked only when an
    actually-encrypted value is encountered while locked."""
    if not isinstance(value, str) or not value.startswith(_MAGIC_TEXT):
        return value
    if _key is None:
        raise KBLocked("encountered encrypted data while locked")
    token = value[len(_MAGIC_TEXT):].encode("ascii")
    return Fernet(_key).decrypt(token).decode("utf-8")


# ── Bytes crypto (whole source files: txt/md/csv/pdf) ───────────────

def encrypt_bytes(data: bytes) -> bytes:
    if not is_enabled():
        return data
    if _key is None:
        raise KBLocked("KB encryption is enabled but locked")
    return _MAGIC_BYTES + Fernet(_key).encrypt(data)


def decrypt_bytes(data: bytes) -> bytes:
    if not isinstance(data, (bytes, bytearray)) or not data.startswith(_MAGIC_BYTES):
        return bytes(data)
    if _key is None:
        raise KBLocked("encountered encrypted data while locked")
    return Fernet(_key).decrypt(bytes(data[len(_MAGIC_BYTES):]))


def is_encrypted_bytes(data: bytes) -> bool:
    return isinstance(data, (bytes, bytearray)) and data.startswith(_MAGIC_BYTES)
