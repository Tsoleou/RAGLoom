#!/usr/bin/env bash
#
# One-shot smoke test for at-rest KB encryption + document injection.
#
# Runs entirely in a throwaway temp workspace via env overrides
# (RAG_KB_KEYSTORE / RAG_CHROMA_PERSIST_PATH / RAG_QUERY_LOG_DB), so it NEVER
# touches your real knowledge_base/, chroma_db/, data/, or config/ keystore.
# Everything is removed on exit.
#
# Offline checks always run (crypto, loader, disk-ciphertext proof, lock gating,
# query-log encryption, endpoint guards). The full ingest→query→inject→delete
# pass runs only if Ollama is reachable; otherwise it's skipped with a note.
#
# Usage:  bash tools/test_encryption.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."   # repo root

PY=venv/bin/python
[ -x "$PY" ] || PY=python3

WORK="$(mktemp -d "${TMPDIR:-/tmp}/ragloom-enc-smoke.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

export RAG_KB_KEYSTORE="$WORK/keystore.json"
export RAG_CHROMA_PERSIST_PATH="$WORK/chroma"
export RAG_QUERY_LOG_DB="$WORK/queries.db"

echo "=== RAGLoom encryption smoke test ==="
echo "    sandbox: $WORK"
echo "    (your real knowledge_base / chroma_db / data are untouched)"
echo

"$PY" - "$WORK" <<'PY'
import os
import sys

work = sys.argv[1]
PASS, FAIL = "  [PASS]", "  [FAIL]"
failures = 0

def check(label, cond):
    global failures
    print((PASS if cond else FAIL) + " " + label)
    if not cond:
        failures += 1

from core import kb_crypto

# 1) Disabled by default → transparent pass-through (backward compat)
print("1. Disabled-by-default pass-through")
check("no keystore yet → encryption disabled", not kb_crypto.is_enabled())
check("encrypt_text is a no-op when disabled", kb_crypto.encrypt_text("hi") == "hi")

# 2) Enable + on-disk ciphertext proof
print("2. Enable encryption + on-disk ciphertext")
kb_crypto.init_keystore("smoke-test-pass")
check("keystore created → encryption enabled", kb_crypto.is_enabled())
check("unlocked after init", kb_crypto.is_unlocked())

kb_dir = os.path.join(work, "kb")
os.makedirs(kb_dir, exist_ok=True)
secret = "SmokeWidget SX-42 weighs 0.42kg with a graphene chassis."
src = os.path.join(kb_dir, "product_smokewidget.txt")
with open(src, "wb") as f:
    f.write(kb_crypto.encrypt_bytes(secret.encode("utf-8")))
raw = open(src, "rb").read()
check("source file on disk starts with envelope magic", raw.startswith(b"RLENC1"))
check("source file on disk has NO plaintext", b"SX-42" not in raw and b"graphene" not in raw)

# 3) Loader transparently decrypts
print("3. Loader decrypt")
from core import loader
doc = loader.load_file(src)
check("loader returns decrypted content", "SX-42" in doc.content)
check("product_id derived from filename", doc.metadata.get("product_id") == "smokewidget")

# 4) Lock blocks decryption; unlock restores it
print("4. Lock / unlock")
kb_crypto.lock()
check("locked → is_unlocked() false", not kb_crypto.is_unlocked())
try:
    loader.load_file(src)
    check("locked loader raises KBLocked", False)
except kb_crypto.KBLocked:
    check("locked loader raises KBLocked", True)
check("wrong passphrase rejected", kb_crypto.unlock("nope") is False)
check("correct passphrase unlocks", kb_crypto.unlock("smoke-test-pass") is True)

# 4b) Passphrase rotation (two-tier key): data stays readable, old pass revoked
print("4b. Passphrase rotation")
probe = kb_crypto.encrypt_text("rotation-probe-token")
check("change_passphrase succeeds", kb_crypto.change_passphrase("smoke-test-pass", "smoke-test-pass-2"))
check("data readable after rotation", kb_crypto.decrypt_text(probe) == "rotation-probe-token")
kb_crypto.lock()
check("old passphrase revoked", kb_crypto.unlock("smoke-test-pass") is False)
check("new passphrase works", kb_crypto.unlock("smoke-test-pass-2") is True)
# restore the original passphrase so later steps read naturally
kb_crypto.change_passphrase("smoke-test-pass-2", "smoke-test-pass")

# 5) Query-log encryption round-trip
print("5. Query-log encryption")
from api import query_log
query_log.log_query(query="這台多重？", response={"retrieval": [], "guards": []},
                    profile="default", model="m", latency_ms=5, status="ok")
import sqlite3
row = sqlite3.connect(os.environ["RAG_QUERY_LOG_DB"]).execute("SELECT query FROM queries").fetchone()[0]
check("query column on disk is ciphertext", row.startswith("RLENC1:") and "多重" not in row)
check("read path decrypts query", query_log.fetch_recent(limit=1)[0]["query"] == "這台多重？")

# 6) HTTP endpoint guards (offline — short-circuit before any ingest)
print("6. Endpoint lock/validation guards")
import importlib
import api.routers.kb as kb_router
importlib.reload(kb_router)
from fastapi import FastAPI
from fastapi.testclient import TestClient
app = FastAPI(); app.include_router(kb_router.router)
client = TestClient(app)
kb_crypto.lock()
check("status reports locked", client.get("/api/kb/status").json() == {"enabled": True, "unlocked": False})
check("inject while locked → 423",
      client.post("/api/kb/documents", json={"filename": "a.txt", "content": "x"}).status_code == 423)
check("wrong passphrase via API → 401",
      client.post("/api/kb/unlock", json={"passphrase": "wrong"}).status_code == 401)
kb_crypto.unlock("smoke-test-pass")
check("path-escape filename → 400",
      client.post("/api/kb/documents", json={"filename": "../escape.txt", "content": "x"}).status_code == 400)

# 7) Full pipeline (needs Ollama): ingest → chroma ciphertext → query → inject → delete
print("7. End-to-end ingest/query/inject (Ollama)")
import requests
base = os.environ.get("RAG_OLLAMA_BASE_URL", "http://localhost:11434")
try:
    requests.get(base + "/api/tags", timeout=2).raise_for_status()
    ollama_up = True
except Exception:
    ollama_up = False

if not ollama_up:
    print("  [SKIP] Ollama not reachable at %s — skipping live ingest/query." % base)
else:
    import glob
    import api.routers.chat as chat
    from config.settings import Settings
    chat.chat_pipe = chat.RAGPipeline(Settings(score_threshold=0.0))
    n = chat.chat_pipe.ingest(kb_dir)
    check("ingest produced chunks", n > 0)
    leak = any(
        (b"SX-42" in open(p, "rb").read() or b"graphene" in open(p, "rb").read())
        for p in glob.glob(os.environ["RAG_CHROMA_PERSIST_PATH"] + "/**/*.sqlite3", recursive=True)
    )
    check("chroma sqlite has NO plaintext chunk text", not leak)
    chat.chat_pipe.query("tell me about SmokeWidget")
    hit = " ".join(str(r.chunk.text) for r in chat.chat_pipe._last_retrieval)
    check("query retrieves DECRYPTED chunk text", "SX-42" in hit or "graphene" in hit)

    # inject a second doc via the live re-ingest path, then remove it
    src2 = os.path.join(kb_dir, "product_extrawidget.txt")
    with open(src2, "wb") as f:
        f.write(kb_crypto.encrypt_bytes("ExtraWidget EX-7 is a 1.1kg titanium tablet.".encode()))
    chat.reingest_file(src2)
    chat.chat_pipe.query("tell me about ExtraWidget")
    hit2 = " ".join(str(r.chunk.text) for r in chat.chat_pipe._last_retrieval)
    check("injected doc is immediately answerable", "EX-7" in hit2 or "titanium" in hit2)
    chat.remove_file_chunks("product_extrawidget.txt")
    left = [m for m in chat.chat_pipe.collection.get(include=["metadatas"])["metadatas"]
            if m.get("filename") == "product_extrawidget.txt"]
    check("delete removes the injected chunks", not left)

print()
if failures:
    print("=== RESULT: %d check(s) FAILED ===" % failures)
    sys.exit(1)
print("=== RESULT: all checks passed ===")
PY

echo
echo "Sandbox cleaned up. Real data was never touched."
