"""Tests for the Idea → Listing 3-step wizard routes.

Coverage:
- Step 1 GET 200 with idea data; IP-warning present for extension_passive source
- Step 1 GET 200 — IP-warning present when reference_image_url is set
- Step 1 GET 200 — no IP-warning for plain etsy_api idea without reference image
- Step 1 GET 404 for missing idea
- Step 1 GET 401 without auth token
- Step 2 POST 200 — transitions, hidden state carried forward
- Step 2 POST 200 — prefill fields appear in step2 HTML
- Step 3 POST 200 — full review form rendered with template + design info
- Step 3 POST 422 — no template_id returns error template
- Submit POST — happy path: mocked service, idea_to_listing row, status=drafted
- Submit POST — service raises ValueError → error template rendered (no crash)
- Submit POST 401 — auth required
- Submit POST 422 — validation: empty title
- Submit POST — idempotent result handled gracefully
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.design import Design
from app.models.idea import Idea
from app.models.idea_signal import IdeaSignal  # noqa: F401 — ensures table created
from app.models.idea_to_listing import IdeaToListing
from app.models.keyword import Keyword  # noqa: F401
from app.models.listing import Listing
from app.models.reference import Reference  # noqa: F401
from app.models.template import Template
from app.models.template_variation import TemplateVariation  # noqa: F401
from app.services import idea_service

# ---------------------------------------------------------------------------
# DB + client fixtures
# ---------------------------------------------------------------------------

_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "test-wizard-token"
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


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def _seed_idea(
    db_session,
    *,
    source: str = "etsy_api",
    slid: str = "WIZ001",
    title: str = "Wizard Test Idea",
    reference_image_url: str | None = None,
    tags_json: str | None = '["custom","gift"]',
    description: str | None = "A test description",
    price_cents: int | None = 2500,
) -> Idea:
    idea, _ = idea_service.upsert_idea(
        db_session,
        source=source,
        source_listing_id=slid,
        title=title,
        reference_image_url=reference_image_url,
        tags_json=tags_json,
        description=description,
        price_cents=price_cents,
    )
    return idea


def _seed_template(db_session) -> Template:
    tmpl = Template(
        name="Test T-Shirt",
        category="apparel",
        base_image_url="https://example.com/base.png",
        default_price_cents=1900,
        variation_options_json='{"sizes":[{"name":"M","price_cents":1900}],"colors":["White","Black"]}',
    )
    db_session.add(tmpl)
    db_session.commit()
    db_session.refresh(tmpl)
    return tmpl


def _seed_design(db_session) -> Design:
    design = Design(
        name="Wizard Design",
        source_type="upload",
        file_url="https://example.com/design.png",
        width=1000,
        height=1000,
    )
    db_session.add(design)
    db_session.commit()
    db_session.refresh(design)
    return design


# ---------------------------------------------------------------------------
# Step 1 — GET /admin/ideas/{id}/create-listing
# ---------------------------------------------------------------------------

def test_step1_requires_auth(client, db):
    idea = _seed_idea(db)
    resp = client.get(f"/admin/ideas/{idea.id}/create-listing")
    assert resp.status_code == 401


def test_step1_returns_200_with_idea_data(client, db):
    idea = _seed_idea(db, title="Awesome Tee")
    resp = client.get(f"/admin/ideas/{idea.id}/create-listing", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert "Awesome Tee" in resp.text
    # Step indicator present
    assert "Step 1" in resp.text


def test_step1_shows_ip_warning_for_extension_passive(client, db):
    idea = _seed_idea(db, source="extension_passive", slid="EXT001")
    resp = client.get(f"/admin/ideas/{idea.id}/create-listing", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert "Reference image is for inspiration only" in resp.text or "copyright risk" in resp.text


def test_step1_shows_ip_warning_when_reference_image_url_present(client, db):
    idea = _seed_idea(
        db,
        slid="REF002",
        reference_image_url="https://example.com/ref.jpg",
    )
    resp = client.get(f"/admin/ideas/{idea.id}/create-listing", headers=VALID_HEADERS)
    assert resp.status_code == 200
    assert "Reference image is for inspiration only" in resp.text or "copyright risk" in resp.text


def test_step1_no_ip_warning_for_plain_etsy_api_idea(client, db):
    idea = _seed_idea(db, source="etsy_api", slid="PLAIN003", reference_image_url=None)
    resp = client.get(f"/admin/ideas/{idea.id}/create-listing", headers=VALID_HEADERS)
    assert resp.status_code == 200
    # The "banner-warn" class is reused by the DRY-RUN banner; assert on IP banner copy
    assert "Reference image is for inspiration only" not in resp.text
    assert "copyright risk" not in resp.text


def test_step1_404_on_missing_idea(client):
    resp = client.get("/admin/ideas/99999/create-listing", headers=VALID_HEADERS)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Step 2 — POST /admin/ideas/{id}/create-listing/step2
# ---------------------------------------------------------------------------

def test_step2_returns_200_and_carries_prefill(client, db):
    idea = _seed_idea(db, title="Test Idea", slid="ST2A")
    tmpl = _seed_template(db)
    _seed_design(db)

    resp = client.post(
        f"/admin/ideas/{idea.id}/create-listing/step2",
        headers=VALID_HEADERS,
        data={
            "prefill_title": "on",
            "prefill_tags": "on",
            "prefill_description": "on",
            "prefill_materials": "",
            "prefill_taxonomy": "",
            "prefill_who_made": "",
            "prefill_when_made": "",
            "prefill_price": "on",
        },
    )
    assert resp.status_code == 200
    # Step 2 indicator
    assert "Step 2" in resp.text
    # Template and design lists rendered
    assert str(tmpl.id) in resp.text
    # Hidden title should be passed forward
    assert "Test Idea" in resp.text


def test_step2_shows_design_picker(client, db):
    idea = _seed_idea(db, slid="ST2B")
    _seed_template(db)
    design = _seed_design(db)

    resp = client.post(
        f"/admin/ideas/{idea.id}/create-listing/step2",
        headers=VALID_HEADERS,
        data={},
    )
    assert resp.status_code == 200
    assert design.name in resp.text


def test_step2_requires_auth(client, db):
    idea = _seed_idea(db, slid="ST2AUTH")
    resp = client.post(f"/admin/ideas/{idea.id}/create-listing/step2", data={})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Step 3 — POST /admin/ideas/{id}/create-listing/step3
# ---------------------------------------------------------------------------

def test_step3_returns_200_with_review_form(client, db):
    idea = _seed_idea(db, title="Review Tee", slid="ST3A")
    tmpl = _seed_template(db)
    design = _seed_design(db)

    resp = client.post(
        f"/admin/ideas/{idea.id}/create-listing/step3",
        headers=VALID_HEADERS,
        data={
            "template_id": str(tmpl.id),
            "design_id": str(design.id),
            "title": "Review Tee",
            "tags_csv": "custom, gift",
            "description": "A great description",
            "materials_csv": "",
            "taxonomy_id": "",
            "who_made": "i_did",
            "when_made": "made_to_order",
            "price": "25.00",
        },
    )
    assert resp.status_code == 200
    assert "Step 3" in resp.text
    assert "Review Tee" in resp.text
    # Template + design referenced
    assert tmpl.name in resp.text
    assert design.name in resp.text


def test_step3_returns_error_when_no_template_id(client, db):
    idea = _seed_idea(db, slid="ST3NOTEMPL")
    resp = client.post(
        f"/admin/ideas/{idea.id}/create-listing/step3",
        headers=VALID_HEADERS,
        data={"design_id": "1"},
    )
    assert resp.status_code == 422
    assert "template" in resp.text.lower()


# ---------------------------------------------------------------------------
# Submit — POST /admin/ideas/{id}/create-listing/submit
# ---------------------------------------------------------------------------

_MOCK_RESULT = {
    "listing_id": 42,
    "etsy_listing_id": "9001",
    "draft_url": "https://www.etsy.com/your/shops/me/listings/draft/9001",
    "composite_urls": [],
    "idempotent": False,
}


def test_submit_requires_auth(client, db):
    idea = _seed_idea(db, slid="SUBAUTH")
    resp = client.post(f"/admin/ideas/{idea.id}/create-listing/submit", data={})
    assert resp.status_code == 401


def test_submit_happy_path_creates_listing_and_marks_drafted(client, db):
    idea = _seed_idea(db, title="Happy Tee", slid="SUBHAPPY")
    tmpl = _seed_template(db)
    design = _seed_design(db)

    # Pre-create the Listing row that the mock will return (listing_id=42 must exist
    # for idea_service.link_to_listing to create an IdeaToListing row with valid FK)
    listing = Listing(
        id=42,
        etsy_listing_id="9001",
        original_title="Happy Tee",
        original_desc="desc",
        original_tags="[]",
        original_images="[]",
        status="created",
        template_id=tmpl.id,
        design_id=design.id,
    )
    db.add(listing)
    db.commit()

    with patch(
        "app.routes.idea_wizard.listing_creator_service.create_from_template",
        return_value=_MOCK_RESULT,
    ):
        resp = client.post(
            f"/admin/ideas/{idea.id}/create-listing/submit",
            headers=VALID_HEADERS,
            data={
                "template_id": str(tmpl.id),
                "design_id": str(design.id),
                "title": "Happy Tee",
                "description": "A happy description",
                "tags_csv": "custom, tee",
                "shop_id": "12345678",
            },
        )

    assert resp.status_code == 200
    assert "9001" in resp.text  # etsy_listing_id in success page

    # idea_to_listing row created
    link = db.get(IdeaToListing, (idea.id, 42))
    assert link is not None

    # idea status flipped to drafted
    db.refresh(idea)
    assert idea.status == "drafted"


def test_submit_service_error_renders_error_template(client, db):
    idea = _seed_idea(db, slid="SUBERR")
    tmpl = _seed_template(db)
    design = _seed_design(db)

    with patch(
        "app.routes.idea_wizard.listing_creator_service.create_from_template",
        side_effect=ValueError("Template not found"),
    ):
        resp = client.post(
            f"/admin/ideas/{idea.id}/create-listing/submit",
            headers=VALID_HEADERS,
            data={
                "template_id": str(tmpl.id),
                "design_id": str(design.id),
                "title": "Error Tee",
                "description": "desc",
                "tags_csv": "",
                "shop_id": "12345678",
            },
        )

    # Must NOT crash — renders error template
    assert resp.status_code in (422, 500)
    assert "Template not found" in resp.text


def test_submit_validation_empty_title(client, db):
    idea = _seed_idea(db, slid="SUBVAL")
    tmpl = _seed_template(db)
    design = _seed_design(db)

    resp = client.post(
        f"/admin/ideas/{idea.id}/create-listing/submit",
        headers=VALID_HEADERS,
        data={
            "template_id": str(tmpl.id),
            "design_id": str(design.id),
            "title": "",
            "description": "desc",
            "tags_csv": "",
            "shop_id": "12345678",
        },
    )
    assert resp.status_code == 422
    assert "Title" in resp.text


def test_submit_idempotent_result_rendered_as_success(client, db):
    """When service returns idempotent=True, success page shows the warning banner."""
    idea = _seed_idea(db, slid="SUBIDEMP")
    tmpl = _seed_template(db)
    design = _seed_design(db)

    # Pre-create listing row for FK integrity
    listing = Listing(
        id=99,
        etsy_listing_id="8888",
        original_title="Existing",
        original_desc="desc",
        original_tags="[]",
        original_images="[]",
        status="created",
        template_id=tmpl.id,
        design_id=design.id,
    )
    db.add(listing)
    db.commit()

    idempotent_result = {**_MOCK_RESULT, "listing_id": 99, "etsy_listing_id": "8888", "idempotent": True}

    with patch(
        "app.routes.idea_wizard.listing_creator_service.create_from_template",
        return_value=idempotent_result,
    ):
        resp = client.post(
            f"/admin/ideas/{idea.id}/create-listing/submit",
            headers=VALID_HEADERS,
            data={
                "template_id": str(tmpl.id),
                "design_id": str(design.id),
                "title": "Existing Tee",
                "description": "desc",
                "tags_csv": "",
                "shop_id": "12345678",
            },
        )

    assert resp.status_code == 200
    # Idempotent warning shown
    assert "already existed" in resp.text or "existing" in resp.text.lower()
    assert "8888" in resp.text
