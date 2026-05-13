"""Seed a real Comfort Tee template (#1) for dev: uploads base PNGs to R2 and upserts the row.

Idempotent — safe to re-run. Re-uploads the PNGs (overwrite is harmless), then
ensures template#1 has the expected fields and exactly 6 variations
(S/M/L × White/Black).

Run from backend/ with the project venv:

    uv run python scripts/seed-dev-template.py

Env it reads (from backend/.env via app.config.settings):
    R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY,
    R2_BUCKET_NAME, R2_PUBLIC_URL, DATABASE_URL.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running directly via `uv run python scripts/seed-dev-template.py`
# from the backend/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3  # noqa: E402

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models.template import Template  # noqa: E402
from app.models.template_variation import TemplateVariation  # noqa: E402

ASSET_DIR = Path(__file__).resolve().parent / "seed-data" / "templates"
WHITE_KEY = "templates/comfort-tee-white.png"
BLACK_KEY = "templates/comfort-tee-black.png"

# Composite anchor — chest area (relative 0–1)
ANCHOR = {"x": 0.32, "y": 0.32, "w": 0.36, "h": 0.40}

VARIATIONS = [
    # (size, color, price_cents, sku)
    ("S", "White", 1900, "CT-S-W"),
    ("S", "Black", 1900, "CT-S-B"),
    ("M", "White", 1900, "CT-M-W"),
    ("M", "Black", 1900, "CT-M-B"),
    ("L", "White", 2100, "CT-L-W"),
    ("L", "Black", 2100, "CT-L-B"),
]


def _r2_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )


def upload_bases() -> tuple[str, str]:
    """Push both PNGs to R2; return their public URLs."""
    s3 = _r2_client()
    bucket = settings.r2_bucket_name
    public = settings.r2_public_url.rstrip("/")

    for local_name, key in [("comfort-tee-white.png", WHITE_KEY),
                            ("comfort-tee-black.png", BLACK_KEY)]:
        path = ASSET_DIR / local_name
        if not path.exists():
            raise SystemExit(f"missing asset: {path}")
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=path.read_bytes(),
            ContentType="image/png",
            CacheControl="public, max-age=31536000",
        )
        print(f"  R2 put {key} ({path.stat().st_size}B)")

    return f"{public}/{WHITE_KEY}", f"{public}/{BLACK_KEY}"


def upsert_template(white_url: str, black_url: str) -> None:
    """Create or update template#1 + replace its variations."""
    session = SessionLocal()
    try:
        tmpl = session.get(Template, 1)
        if tmpl is None:
            tmpl = Template(id=1)
            session.add(tmpl)
            print("  template#1 not found — creating")
        else:
            print(f"  template#1 found ({tmpl.name!r}) — updating")

        tmpl.name = "Comfort Tee"
        tmpl.category = "apparel"
        tmpl.base_image_url = white_url
        tmpl.composite_anchor_json = json.dumps(ANCHOR)
        tmpl.default_price_cents = 1900
        tmpl.variation_options_json = json.dumps({
            "sizes": [
                {"name": "S", "price_cents": 1900},
                {"name": "M", "price_cents": 1900},
                {"name": "L", "price_cents": 2100},
            ],
            "colors": ["White", "Black"],
            "primary_color": "White",
            # NOTE: placeholder Etsy taxonomy; pick a real one before publishing.
            "etsy_taxonomy_id": 1209,
        })
        tmpl.color_base_images_json = json.dumps({
            "White": white_url,
            "Black": black_url,
        })

        # Replace variations wholesale — keeps the table clean on re-runs.
        session.query(TemplateVariation).filter_by(template_id=1).delete()
        session.flush()
        session.add_all([
            TemplateVariation(
                template_id=1,
                size=size,
                color=color,
                price_cents=price,
                sku=sku,
            )
            for size, color, price, sku in VARIATIONS
        ])

        session.commit()
        session.refresh(tmpl)
        print(f"  variations: {len(tmpl.variations)} "
              f"({', '.join(f'{v.size}/{v.color}' for v in tmpl.variations)})")
    finally:
        session.close()


def main() -> None:
    print("Seeding dev template (Comfort Tee, #1)...")
    print("[1/2] uploading base PNGs to R2")
    white_url, black_url = upload_bases()
    print("[2/2] upserting template row + variations")
    upsert_template(white_url, black_url)
    print("done.")
    print(f"  base (White): {white_url}")
    print(f"  base (Black): {black_url}")


if __name__ == "__main__":
    main()
