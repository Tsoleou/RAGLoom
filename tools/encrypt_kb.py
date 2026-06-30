#!/usr/bin/env python3
"""One-time migration: turn a plaintext knowledge base into an encrypted one.

What it does, in order:
  1. Create the keystore from a passphrase (salt + verifier only; no secret on
     disk). Refuses to run if a keystore already exists.
  2. Encrypt every source file under knowledge_base/ in place (atomic replace),
     skipping product_images/ (served publicly) and anything already encrypted.
  3. Rebuild the chroma vector store from the now-encrypted sources so the
     stored chunk text is ciphertext too — then wipe the old plaintext chroma_db
     so the leak you're closing isn't left sitting on disk.

Run:  RAG_KB_PASSPHRASE='your-pass' venv/bin/python -m tools.encrypt_kb
      (or omit the env var and you'll be prompted)

Flags:
  --no-reingest   encrypt source files only; skip rebuilding/wiping chroma_db.
                  ⚠ the existing chroma_db still holds PLAINTEXT chunks until you
                  re-ingest, so this leaves the main leak open — use only if
                  Ollama is unavailable and you'll re-ingest later.
"""

import argparse
import getpass
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Allow running as `python -m tools.encrypt_kb` or `python tools/encrypt_kb.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import kb_crypto  # noqa: E402
from core.loader import SUPPORTED_EXTENSIONS  # noqa: E402

_KB_ROOT = Path("./knowledge_base")
_SKIP_DIRS = {"product_images"}  # public images stay plaintext (served at /product_images)


def _source_files() -> list[Path]:
    """All encryptable source files under knowledge_base/ (recursive), excluding
    public/skip dirs, dotfiles, and unsupported types."""
    out: list[Path] = []
    for f in sorted(_KB_ROOT.rglob("*")):
        if not f.is_file() or f.name.startswith("."):
            continue
        if any(part in _SKIP_DIRS for part in f.relative_to(_KB_ROOT).parts):
            continue
        if f.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        out.append(f)
    return out


def _encrypt_in_place(path: Path) -> bool:
    """Encrypt one file atomically. Returns False if already encrypted."""
    raw = path.read_bytes()
    if kb_crypto.is_encrypted_bytes(raw):
        return False
    enc = kb_crypto.encrypt_bytes(raw)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".enc-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(enc)
        os.replace(tmp, path)  # atomic on POSIX
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return True


def _encrypt_query_log() -> None:
    """Encrypt the free-text columns of any EXISTING query-log rows in place.

    log_query encrypts new rows going forward, but the accumulated visitor
    question history on disk is plaintext until this runs — and for a privacy
    product that history is itself sensitive. Idempotent: rows already carrying
    the envelope prefix are skipped, so re-running is safe."""
    import sqlite3

    db = os.environ.get("RAG_QUERY_LOG_DB", "./data/queries.db")
    if not Path(db).is_file():
        print("→ No query log found — skipping.")
        return
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT id, query, detail FROM queries").fetchall()
        n = 0
        for rid, query, detail in rows:
            # Skip rows already enveloped so re-running never double-encrypts.
            q_done = query is None or kb_crypto.is_encrypted_text(query)
            d_done = detail is None or kb_crypto.is_encrypted_text(detail)
            if q_done and d_done:
                continue
            new_q = query if q_done else kb_crypto.encrypt_text(query)
            new_d = detail if d_done else kb_crypto.encrypt_text(detail)
            conn.execute(
                "UPDATE queries SET query = ?, detail = ? WHERE id = ?",
                (new_q, new_d, rid),
            )
            n += 1
        conn.commit()
        print(f"→ Encrypted {n} existing query-log row(s) in {db}.")
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Encrypt the RAGLoom knowledge base.")
    ap.add_argument("--no-reingest", action="store_true",
                    help="encrypt source files only; do not rebuild/wipe chroma_db")
    args = ap.parse_args()

    if kb_crypto.is_enabled():
        print(f"✗ A keystore already exists at {os.environ.get('RAG_KB_KEYSTORE', './config/kb_keystore.json')}.")
        print("  The KB is already configured for encryption — refusing to re-init.")
        return 1

    if not _KB_ROOT.is_dir():
        print(f"✗ {_KB_ROOT} not found — run from the repo root.")
        return 1

    passphrase = os.environ.get("RAG_KB_PASSPHRASE", "")
    if not passphrase:
        passphrase = getpass.getpass("Set KB encryption passphrase: ")
        if passphrase != getpass.getpass("Confirm passphrase: "):
            print("✗ Passphrases do not match.")
            return 1
    if len(passphrase) < 8:
        print("✗ Passphrase too short (min 8 chars).")
        return 1

    files = _source_files()
    print(f"→ Will encrypt {len(files)} source file(s) under {_KB_ROOT} "
          f"(skipping {', '.join(sorted(_SKIP_DIRS))}), plus existing query-log rows.")
    if not args.no_reingest:
        print("→ Will then rebuild chroma_db from the encrypted sources and "
              "WIPE the existing plaintext chroma_db.")
    print("  This passphrase is NOT stored. If you lose it, the KB is unrecoverable.")
    if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Aborted.")
        return 1

    # 1. Keystore (also loads the key into memory for the encrypt pass).
    kb_crypto.init_keystore(passphrase)

    # 2. Encrypt source files.
    enc, skipped = 0, 0
    for f in files:
        if _encrypt_in_place(f):
            enc += 1
            print(f"  ✓ encrypted {f.relative_to(_KB_ROOT.parent)}")
        else:
            skipped += 1
    print(f"→ Encrypted {enc} file(s), skipped {skipped} already-encrypted.")

    # 2b. Encrypt the accumulated query-log history (the user explicitly opted
    # queries.db into the encryption scope; new rows are encrypted by log_query).
    _encrypt_query_log()

    # 3. Rebuild vector store from encrypted sources, then wipe old plaintext DB.
    if args.no_reingest:
        print("⚠ Skipped chroma rebuild (--no-reingest). The existing chroma_db "
              "still contains PLAINTEXT chunks. Re-ingest before relying on this.")
        return 0

    from config.settings import Settings
    from core.pipeline import RAGPipeline

    chroma_path = os.environ.get("RAG_CHROMA_PERSIST_PATH", "./chroma_db")
    print(f"→ Rebuilding vector store at {chroma_path} from encrypted sources…")
    # Fresh DB in a temp dir so a mid-rebuild crash never deletes the old data
    # before the new store is fully built.
    new_path = chroma_path.rstrip("/") + ".enc-new"
    if Path(new_path).exists():
        shutil.rmtree(new_path)
    pipe = RAGPipeline(Settings(score_threshold=0.0, chroma_persist_path=new_path))
    count = pipe.ingest(str(_KB_ROOT))
    del pipe  # release chroma handle before swapping dirs
    print(f"→ Built {count} encrypted chunks.")

    old_path = chroma_path.rstrip("/")
    backup = old_path + ".plaintext-bak"
    if Path(old_path).exists():
        if Path(backup).exists():
            shutil.rmtree(backup)
        os.replace(old_path, backup)
    os.replace(new_path, old_path)
    # Securely remove the plaintext backup (it held readable chunks).
    if Path(backup).exists():
        shutil.rmtree(backup)
        print("→ Wiped old plaintext chroma_db.")

    print("\n✓ Knowledge base is now encrypted.")
    print("  Start the server and unlock via /admin (or POST /api/kb/unlock).")
    print("  Tip: set RAG_ADMIN_PASSWORD to the same passphrase so one secret "
          "covers both admin login and KB unlock.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
