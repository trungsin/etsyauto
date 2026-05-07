"""One-shot cleanup: remove templates/designs created with placeholder URLs.

Smoke tests + early bootstrap rows referenced ``cdn.example.com`` URLs that don't
resolve. Those rows leak into the admin dropdowns and confuse new sellers.

Usage:
    cd backend && uv run python ../scripts/cleanup_placeholder_data.py
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from pathlib import Path


def _resolve_db_path() -> Path:
    """Pick the SQLite DB path from DATABASE_URL or the project default."""
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("sqlite:///"):
        path = Path(url[len("sqlite:///"):])
    else:
        # Default per backend/app/config.py: ./etsyauto.db relative to backend cwd
        path = Path("etsyauto.db")
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def main() -> int:
    db_path = _resolve_db_path()
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    cur.execute(
        "SELECT id, name, base_image_url FROM templates "
        "WHERE base_image_url LIKE '%cdn.example.com%'"
    )
    bad_templates = cur.fetchall()
    cur.execute(
        "SELECT id, name, file_url FROM designs "
        "WHERE file_url LIKE '%cdn.example.com%'"
    )
    bad_designs = cur.fetchall()

    print(f"Found {len(bad_templates)} placeholder template(s):")
    for tid, name, url in bad_templates:
        print(f"  #{tid} {name!r} → {url}")
    print(f"Found {len(bad_designs)} placeholder design(s):")
    for did, name, url in bad_designs:
        print(f"  #{did} {name!r} → {url}")

    if not (bad_templates or bad_designs):
        print("Nothing to clean up.")
        return 0

    if "--yes" not in sys.argv:
        confirm = input("Delete? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return 0

    cur.execute("DELETE FROM templates WHERE base_image_url LIKE '%cdn.example.com%'")
    cur.execute("DELETE FROM designs WHERE file_url LIKE '%cdn.example.com%'")
    conn.commit()
    print(f"Deleted {len(bad_templates)} templates, {len(bad_designs)} designs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
