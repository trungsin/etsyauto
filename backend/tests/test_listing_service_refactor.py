"""Tests for v0.10 listing_creator_service refactor (save_draft + upload_to_etsy + CAS lock)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.design import Design
from app.models.listing import Listing
from app.models.template import Template
from app.services import listing_creator_service
from app.services.listing_creator_service import ConflictError

_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)
    yield


@pytest.fixture
def db():
    s = _TestingSessionLocal()
    yield s
    s.close()


def _seed_template(db, *, sizes=None, colors=None) -> Template:
    sizes = sizes or [{"name": "S", "price_cents": 1900}, {"name": "M", "price_cents": 1900}]
    colors = colors or ["White", "Black"]
    t = Template(
        name="T-Refactor",
        category="apparel",
        base_image_url="https://cdn.example.com/base.png",
        default_price_cents=1900,
        variation_options_json=json.dumps({
            "sizes": sizes,
            "colors": colors,
            "primary_color": colors[0],
            "etsy_taxonomy_id": 1209,
            "shipping_profile_id": 123,
            "readiness_state_id": 456,
        }),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


def _seed_design(db) -> Design:
    d = Design(
        name="logo",
        source_type="upload",
        file_url="https://cdn.example.com/design.png",
        width=1000,
        height=1000,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def _stub_render(*_args, **_kwargs):
    """Stub render_all_for_listing — returns one image entry."""
    return [{
        "id": 1, "template_id": 1, "image_url": "https://cdn.example.com/img.png",
        "color": "White", "rank": 1, "role": "mockup", "is_virtual": False,
        "url": "https://cdn.example.com/composite/1.png", "cached": True, "error": None,
    }]


# ---------------------------------------------------------------------------
# save_draft
# ---------------------------------------------------------------------------


def test_save_draft_persists_local_no_etsy_call(db):
    """save_draft creates Listing row with status='new', etsy_listing_id=None,
    local_payload_json populated. NO EtsyApiClient instantiation."""
    tmpl = _seed_template(db)
    design = _seed_design(db)
    combos = [{"size": "S", "color": "White", "enabled": True}]

    with patch.object(listing_creator_service.composite_service, "render_all_for_listing", side_effect=_stub_render), \
         patch("app.services.listing_creator_service.EtsyApiClient") as MockClient:
        result = listing_creator_service.save_draft(
            session=db,
            template_id=tmpl.id,
            design_id=design.id,
            title="Draft Tee",
            description="desc",
            tags=["custom"],
            enabled_combos=combos,
        )

    assert result["status"] == "new"
    assert result["idempotent"] is False
    MockClient.assert_not_called()

    row = db.get(Listing, result["listing_id"])
    assert row is not None
    assert row.status == "new"
    assert row.etsy_listing_id is None
    payload = json.loads(row.local_payload_json)
    assert payload["title"] == "Draft Tee"
    assert payload["tags"] == ["custom"]
    assert payload["enabled_combos"] == [{"size": "S", "color": "White"}]
    assert payload["gallery_snapshot"]  # render snapshot persisted


def test_save_draft_idempotent_on_template_design(db):
    """Calling save_draft twice with same (template_id, design_id) updates the
    same row — no duplicates."""
    tmpl = _seed_template(db)
    design = _seed_design(db)
    combos = [{"size": "S", "color": "White", "enabled": True}]

    with patch.object(listing_creator_service.composite_service, "render_all_for_listing", side_effect=_stub_render):
        r1 = listing_creator_service.save_draft(
            session=db, template_id=tmpl.id, design_id=design.id,
            title="A", description="d1", tags=["x"], enabled_combos=combos,
        )
        r2 = listing_creator_service.save_draft(
            session=db, template_id=tmpl.id, design_id=design.id,
            title="B", description="d2", tags=["y"], enabled_combos=combos,
        )

    assert r1["listing_id"] == r2["listing_id"]
    rows = list(db.scalars(select(Listing)))
    assert len(rows) == 1
    # Latest call wins for editable fields
    assert rows[0].original_title == "B"
    assert json.loads(rows[0].original_tags) == ["y"]


def test_save_draft_idempotent_returns_existing_live_listing(db):
    """If a listing already has etsy_listing_id, save_draft returns idempotent
    without re-rendering or modifying the row."""
    tmpl = _seed_template(db)
    design = _seed_design(db)

    existing = Listing(
        etsy_listing_id="9999",
        original_title="Live",
        original_desc="d",
        original_tags="[]",
        original_images="[]",
        status="created",
        template_id=tmpl.id,
        design_id=design.id,
    )
    db.add(existing)
    db.commit()
    db.refresh(existing)

    with patch.object(listing_creator_service.composite_service, "render_all_for_listing") as mock_render:
        result = listing_creator_service.save_draft(
            session=db, template_id=tmpl.id, design_id=design.id,
            title="Ignored", description="ignored", tags=[],
            enabled_combos=[{"size": "S", "color": "White", "enabled": True}],
        )

    assert result["idempotent"] is True
    assert result["listing_id"] == existing.id
    mock_render.assert_not_called()
    db.refresh(existing)
    assert existing.original_title == "Live"  # untouched


# ---------------------------------------------------------------------------
# upload_to_etsy — CAS lock + failure paths
# ---------------------------------------------------------------------------


def test_upload_to_etsy_cas_lock_blocks_concurrent(db):
    """If status is already 'uploading', upload_to_etsy raises ConflictError without
    invoking Etsy."""
    tmpl = _seed_template(db)
    design = _seed_design(db)
    row = Listing(
        etsy_listing_id=None,
        original_title="Locked",
        original_desc="d",
        original_tags="[]",
        original_images="[]",
        status="uploading",  # already locked
        template_id=tmpl.id,
        design_id=design.id,
        local_payload_json=json.dumps({"title": "Locked", "enabled_combos": [], "tags": []}),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    with patch("app.services.listing_creator_service.EtsyApiClient") as MockClient:
        with pytest.raises(ConflictError):
            listing_creator_service.upload_to_etsy(db, row.id, shop_id=123)
    MockClient.assert_not_called()


def test_upload_to_etsy_failure_rolls_back_to_failed(db):
    """If Etsy create_draft_listing raises, status is rolled back to 'failed' and
    last_push_error captured."""
    tmpl = _seed_template(db)
    design = _seed_design(db)
    snapshot = [{"rank": 1, "color": "White", "url": "https://cdn.example.com/c.png", "id": 1}]
    payload = {
        "title": "T", "description": "d", "tags": [],
        "enabled_combos": [{"size": "S", "color": "White"}],
        "zone_designs": {}, "gallery_snapshot": snapshot,
    }
    row = Listing(
        etsy_listing_id=None,
        original_title="T",
        original_desc="d",
        original_tags="[]",
        original_images="[]",
        status="new",
        template_id=tmpl.id,
        design_id=design.id,
        local_payload_json=json.dumps(payload),
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    etsy_mock = MagicMock()
    etsy_mock.__enter__ = MagicMock(return_value=etsy_mock)
    etsy_mock.__exit__ = MagicMock(return_value=False)
    etsy_mock.get_taxonomy_property_values.return_value = {
        "results": [{"value_id": 1, "name": "White"}, {"value_id": 100, "name": "S"}]
    }
    etsy_mock.create_draft_listing.side_effect = RuntimeError("Etsy 500 boom")

    with patch("app.services.listing_creator_service.EtsyApiClient", return_value=etsy_mock):
        with pytest.raises(RuntimeError):
            listing_creator_service.upload_to_etsy(db, row.id, shop_id=123)

    db.refresh(row)
    assert row.status == "failed"
    assert row.last_push_error is not None
    assert "Etsy 500 boom" in row.last_push_error
    assert row.push_attempts == 1
    assert row.etsy_listing_id is None


def test_upload_to_etsy_idempotent_when_already_uploaded(db):
    """Listing already has etsy_listing_id → return idempotent without re-upload."""
    tmpl = _seed_template(db)
    design = _seed_design(db)
    row = Listing(
        etsy_listing_id="55555",
        original_title="Live",
        original_desc="d",
        original_tags="[]",
        original_images="[]",
        status="created",
        template_id=tmpl.id,
        design_id=design.id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    with patch("app.services.listing_creator_service.EtsyApiClient") as MockClient:
        result = listing_creator_service.upload_to_etsy(db, row.id, shop_id=123)

    MockClient.assert_not_called()
    assert result["idempotent"] is True
    assert result["etsy_listing_id"] == "55555"


# ---------------------------------------------------------------------------
# Back-compat: create_from_template still works
# ---------------------------------------------------------------------------


def test_create_from_template_backcompat_shim_still_works(db):
    """The back-compat shim must still produce a draft + upload in one call.
    Verifies that legacy callers (/listings/from-template route) keep working."""
    tmpl = _seed_template(db)
    design = _seed_design(db)
    combos = [{"size": "S", "color": "White", "enabled": True}]

    etsy_mock = MagicMock()
    etsy_mock.__enter__ = MagicMock(return_value=etsy_mock)
    etsy_mock.__exit__ = MagicMock(return_value=False)
    etsy_mock.get_taxonomy_property_values.side_effect = lambda tid, pid: {
        "results": [
            {"value_id": 1, "name": "White"},
            {"value_id": 100, "name": "S"},
        ]
    }
    etsy_mock.create_draft_listing.return_value = {"listing_id": "7777"}
    etsy_mock.update_listing_inventory.return_value = {}
    etsy_mock.upload_listing_image_bytes.return_value = {"listing_image_id": 1}

    with patch.object(listing_creator_service.composite_service, "render_all_for_listing", side_effect=_stub_render), \
         patch("app.services.listing_creator_service.EtsyApiClient", return_value=etsy_mock), \
         patch("app.services.listing_creator_service.httpx.Client") as MockHttp, \
         patch("app.services.listing_creator_service.time.sleep"):
        MockHttp.return_value.__enter__.return_value.get.return_value.content = b"\x89PNG fake"
        result = listing_creator_service.create_from_template(
            session=db,
            template_id=tmpl.id, design_id=design.id,
            title="T", description="d", tags=[],
            enabled_combos=combos, shop_id="shop1",
        )

    assert result["etsy_listing_id"] == "7777"
    assert result["idempotent"] is False
    row = db.get(Listing, result["listing_id"])
    assert row.status == "created"
    assert row.etsy_listing_id == "7777"
