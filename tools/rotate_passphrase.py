#!/usr/bin/env python3
"""Change the knowledge-base encryption passphrase.

Thanks to two-tier keys (a random master key wrapped under the passphrase),
this only re-wraps the master key — it does NOT re-encrypt any data, so it's
instant and safe to run while the booth is offline. A legacy v1 keystore is
upgraded to v2 on the way through.

Run:  RAG_KB_OLD_PASSPHRASE=... RAG_KB_NEW_PASSPHRASE=... venv/bin/python -m tools.rotate_passphrase
      (or omit the env vars and you'll be prompted)
"""

import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import kb_crypto  # noqa: E402


def main() -> int:
    if not kb_crypto.is_enabled():
        ks = os.environ.get("RAG_KB_KEYSTORE", "./config/kb_keystore.json")
        print(f"✗ No keystore at {ks} — encryption isn't configured. Run `make kb-encrypt` first.")
        return 1

    old = os.environ.get("RAG_KB_OLD_PASSPHRASE", "")
    new = os.environ.get("RAG_KB_NEW_PASSPHRASE", "")
    if not old:
        old = getpass.getpass("Current passphrase: ")
    if not new:
        new = getpass.getpass("New passphrase: ")
        if new != getpass.getpass("Confirm new passphrase: "):
            print("✗ New passphrases do not match.")
            return 1
    if len(new) < 8:
        print("✗ New passphrase too short (min 8 chars).")
        return 1

    try:
        ok = kb_crypto.change_passphrase(old, new)
    except ValueError as e:
        print(f"✗ {e}")
        return 1
    if not ok:
        print("✗ Current passphrase is incorrect — nothing changed.")
        return 1

    print("✓ Passphrase changed. No data was re-encrypted (master key re-wrapped).")
    print("  Use the new passphrase to unlock from now on; the old one no longer works.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
