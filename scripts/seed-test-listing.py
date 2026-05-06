"""seed-test-listing.py — Insert one fake listing into SQLite for manual debugging.

Usage:
    cd backend && uv run python ../scripts/seed-test-listing.py
    # or from repo root:
    python scripts/seed-test-listing.py  (requires backend/ on sys.path)
"""
import json
import sys
from pathlib import Path

# Allow running from repo root by adding backend/ to sys.path
backend_dir = Path(__file__).parent.parent / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

# Change working directory to backend so SQLite path resolves correctly
import os
os.chdir(backend_dir)

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import settings
from app.database import Base
from app.models.listing import Listing

FAKE_LISTING = {
    "etsy_listing_id": "999999",
    "original_title": "Test Listing — Handmade Cotton Tote Bag Natural Organic Market Bag",
    "original_desc": "A beautiful handcrafted tote bag perfect for everyday use.",
    "original_tags": json.dumps(["tote bag", "cotton", "handmade", "natural", "organic", "market bag"]),
    "original_images": json.dumps(["https://via.placeholder.com/600.png"]),
    "status": "new",
}


def main() -> None:
    engine = create_engine(settings.database_url, echo=False)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        existing = (
            session.query(Listing)
            .filter(Listing.etsy_listing_id == FAKE_LISTING["etsy_listing_id"])
            .first()
        )
        if existing:
            print(f"[seed] Listing {FAKE_LISTING['etsy_listing_id']} already exists (id={existing.id}, status={existing.status})")
            print("[seed] Resetting status to 'new' for fresh test run.")
            existing.status = "new"
            existing.push_attempts = 0
            existing.last_push_error = None
            existing.notion_page_id = None
            existing.pushed_at = None
            session.commit()
            print(f"[seed] Updated: id={existing.id}")
            return

        row = Listing(**FAKE_LISTING)
        session.add(row)
        session.commit()
        session.refresh(row)
        print(f"[seed] Inserted test listing:")
        print(f"       id={row.id}")
        print(f"       etsy_listing_id={row.etsy_listing_id}")
        print(f"       status={row.status}")
        print(f"       title={row.original_title}")
        print(f"[seed] Done. To verify:")
        print(f"       sqlite3 backend/etsyauto.db \"SELECT id, etsy_listing_id, status, original_title FROM listings WHERE etsy_listing_id='999999';\"")


if __name__ == "__main__":
    main()
