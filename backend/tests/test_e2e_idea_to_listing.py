"""E2E happy-path test — keyword → miner → idea → wizard submit → drafted listing.

Single end-to-end test that exercises the full v0.8 pipeline with the Etsy public
client in dry-run mode and listing_creator_service mocked at the boundary.

Verifies (per phase-07 success criteria):
  1. miner upserts ≥1 idea from a seeded keyword
  2. wizard step1/step2/step3 GET/POST cycle returns 200
  3. submit creates IdeaToListing row, flips idea.status='drafted',
     and surfaces the etsy_listing_id from the dry-run fixture
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.clients.etsy_public_client import EtsyPublicClient
from app.config import settings
from app.database import Base, get_db
from app.main import app
from app.models.design import Design
from app.models.idea import Idea
from app.models.idea_signal import IdeaSignal  # noqa: F401 — register table
from app.models.idea_to_listing import IdeaToListing
from app.models.keyword import Keyword  # noqa: F401
from app.models.listing import Listing
from app.models.reference import Reference  # noqa: F401
from app.models.template import Template
from app.models.template_variation import TemplateVariation  # noqa: F401
from app.services import idea_miner_service, idea_service, keyword_service

# ---------------------------------------------------------------------------
# In-memory DB shared across this module
# ---------------------------------------------------------------------------

_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "e2e-token"
VALID_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}


def _override_get_db():
    db = _TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)
    yield


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", ADMIN_TOKEN)
    from app import config
    config.settings.admin_token = ADMIN_TOKEN
    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def db():
    s = _TestingSessionLocal()
    yield s
    s.close()


@pytest.fixture
def dry_run(monkeypatch):
    monkeypatch.setattr(settings, "etsy_dry_run", True)
    monkeypatch.setattr(settings, "etsy_dry_run_scenario", "happy")
    yield


# ---------------------------------------------------------------------------
# E2E happy path
# ---------------------------------------------------------------------------

_MOCK_LCS_RESULT = {
    "listing_id": 1,
    "etsy_listing_id": "9001",
    "draft_url": "https://www.etsy.com/your/shops/me/listings/draft/9001",
    "composite_urls": [],
    "idempotent": False,
}


def test_e2e_keyword_to_drafted_listing(client, db, dry_run):
    # ── Seed: keyword + template + design ────────────────────────────────
    kw = keyword_service.create_keyword(db, "botanical print")

    tmpl = Template(
        name="E2E T-Shirt",
        category="apparel",
        base_image_url="https://example.com/base.png",
        default_price_cents=1900,
        variation_options_json='{"sizes":[{"name":"M","price_cents":1900}],"colors":["White"]}',
    )
    db.add(tmpl)
    db.commit()
    db.refresh(tmpl)

    design = Design(
        name="E2E Design",
        source_type="upload",
        file_url="https://example.com/design.png",
        width=1000,
        height=1000,
    )
    db.add(design)
    db.commit()
    db.refresh(design)

    # Pre-create the Listing row that the mock returns (FK target for IdeaToListing)
    listing = Listing(
        id=1,
        etsy_listing_id="9001",
        original_title="Mocked",
        original_desc="d",
        original_tags="[]",
        original_images="[]",
        status="created",
        template_id=tmpl.id,
        design_id=design.id,
    )
    db.add(listing)
    db.commit()

    # ── Step A: run miner against dry-run Etsy client ────────────────────
    with patch("app.clients.etsy_public_client.time.sleep"), \
         patch("app.services.idea_miner_service.time.sleep"):
        miner_client = EtsyPublicClient(api_key="test")
        try:
            result = idea_miner_service.run_for_keyword(db, kw.id, client=miner_client)
        finally:
            miner_client.close()

    assert result["ideas_upserted"] == 5
    assert result["errors"] == 0

    ideas = idea_service.list_ideas(db, keyword_id=kw.id)
    assert len(ideas) == 5
    idea = ideas[0]
    assert idea.status == "new"

    # ── Step B: wizard step1 GET ─────────────────────────────────────────
    r = client.get(f"/admin/ideas/{idea.id}/create-listing", headers=VALID_HEADERS)
    assert r.status_code == 200
    assert "Step 1" in r.text

    # ── Step C: wizard step2 POST ────────────────────────────────────────
    r = client.post(
        f"/admin/ideas/{idea.id}/create-listing/step2",
        headers=VALID_HEADERS,
        data={"prefill_title": "on", "prefill_tags": "on"},
    )
    assert r.status_code == 200
    assert "Step 2" in r.text

    # ── Step D: wizard step3 POST (review form) ──────────────────────────
    r = client.post(
        f"/admin/ideas/{idea.id}/create-listing/step3",
        headers=VALID_HEADERS,
        data={
            "template_id": str(tmpl.id),
            "design_id": str(design.id),
            "title": idea.title or "E2E Tee",
            "tags_csv": "botanical, wall art",
            "description": idea.description or "E2E description",
            "price": "25.00",
            "who_made": "i_did",
            "when_made": "made_to_order",
        },
    )
    assert r.status_code == 200
    assert "Step 3" in r.text

    # ── Step E: submit (mock listing_creator_service) ────────────────────
    with patch(
        "app.routes.idea_wizard.listing_creator_service.create_from_template",
        return_value=_MOCK_LCS_RESULT,
    ):
        r = client.post(
            f"/admin/ideas/{idea.id}/create-listing/submit",
            headers=VALID_HEADERS,
            data={
                "template_id": str(tmpl.id),
                "design_id": str(design.id),
                "title": "E2E Drafted Tee",
                "description": "Created by E2E test",
                "tags_csv": "botanical, e2e",
                "shop_id": "12345678",
            },
        )

    assert r.status_code == 200
    assert "9001" in r.text  # etsy_listing_id surfaced on success page

    # ── Assertions: idea linked + drafted ────────────────────────────────
    db.refresh(idea)
    assert idea.status == "drafted"

    link = db.get(IdeaToListing, (idea.id, listing.id))
    assert link is not None, "IdeaToListing row must exist after submit"
