"""Tests for /references/{id}/suggest-title and /references/{id}/cutout endpoints."""
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.design import Design  # noqa: F401
from app.models.reference import Reference  # noqa: F401
from app.models.template import Template  # noqa: F401
from app.models.template_variation import TemplateVariation  # noqa: F401

_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_Session = sessionmaker(autocommit=False, autoflush=False, bind=_engine)

ADMIN_TOKEN = "test-admin-token-ai"
VALID_HEADERS = {"X-Admin-Token": ADMIN_TOKEN}

_SCRAPE_PAYLOAD = {
    "source_url": "https://www.etsy.com/listing/11111/sample",
    "source_listing_id": "11111",
    "original_title": "Hand Painted Mug",
    "original_description": "A beautiful hand painted ceramic mug.",
    "original_images": [
        "https://img.example.com/a.jpg",
        "https://img.example.com/b.jpg",
    ],
}

_FAKE_VARIANTS = [
    {"text": "Ceramic Mug Handmade Gift", "char_count": 24, "rationale": "r1", "target_keywords": ["ceramic"]},
    {"text": "Hand Painted Coffee Cup", "char_count": 22, "rationale": "r2", "target_keywords": ["painted"]},
    {"text": "Custom Artisan Mug", "char_count": 18, "rationale": "r3", "target_keywords": ["artisan"]},
]

# Minimal 8x8 transparent PNG bytes (valid PNG with alpha channel)
import io
from PIL import Image as _PILImage
_buf = io.BytesIO()
_img = _PILImage.new("RGBA", (8, 8), (255, 0, 0, 128))
_img.save(_buf, format="PNG")
_FAKE_PNG_BYTES = _buf.getvalue()


def _override_get_db():
    db = _Session()
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


def _create_ref(client) -> int:
    resp = client.post("/references/scrape", headers=VALID_HEADERS, json=_SCRAPE_PAYLOAD)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# suggest-title — auth guard
# ---------------------------------------------------------------------------

def test_suggest_title_requires_token(client):
    ref_id = _create_ref(client)
    resp = client.post(f"/references/{ref_id}/suggest-title")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# suggest-title — 404 on missing reference
# ---------------------------------------------------------------------------

def test_suggest_title_not_found(client):
    with patch("app.services.reference_service.GeminiTextClient") as mock_cls:
        mock_cls.return_value.generate_title_variants.return_value = _FAKE_VARIANTS
        resp = client.post("/references/99999/suggest-title", headers=VALID_HEADERS)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# suggest-title — happy path: returns 3 variants, persisted in DB
# ---------------------------------------------------------------------------

def test_suggest_title_returns_variants(client):
    ref_id = _create_ref(client)
    with patch("app.services.reference_service.GeminiTextClient") as mock_cls:
        mock_cls.return_value.generate_title_variants.return_value = _FAKE_VARIANTS
        resp = client.post(f"/references/{ref_id}/suggest-title", headers=VALID_HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "variants" in body
    assert len(body["variants"]) == 3
    assert body["variants"][0]["text"] == "Ceramic Mug Handmade Gift"


def test_suggest_title_persisted_and_status_enriched(client):
    ref_id = _create_ref(client)
    with patch("app.services.reference_service.GeminiTextClient") as mock_cls:
        mock_cls.return_value.generate_title_variants.return_value = _FAKE_VARIANTS
        client.post(f"/references/{ref_id}/suggest-title", headers=VALID_HEADERS)

    ref_resp = client.get(f"/references/{ref_id}", headers=VALID_HEADERS)
    assert ref_resp.status_code == 200
    body = ref_resp.json()
    assert body["status"] == "enriched"
    assert body["ai_title_variants"] is not None
    assert len(body["ai_title_variants"]) == 3


# ---------------------------------------------------------------------------
# suggest-title — idempotent: calling twice replaces, not duplicates
# ---------------------------------------------------------------------------

def test_suggest_title_idempotent_replaces(client):
    ref_id = _create_ref(client)
    second_variants = [
        {"text": "New Variant One", "char_count": 15, "rationale": "r", "target_keywords": []},
        {"text": "New Variant Two", "char_count": 15, "rationale": "r", "target_keywords": []},
        {"text": "New Variant Three", "char_count": 17, "rationale": "r", "target_keywords": []},
    ]
    with patch("app.services.reference_service.GeminiTextClient") as mock_cls:
        mock_cls.return_value.generate_title_variants.return_value = _FAKE_VARIANTS
        client.post(f"/references/{ref_id}/suggest-title", headers=VALID_HEADERS)

    with patch("app.services.reference_service.GeminiTextClient") as mock_cls:
        mock_cls.return_value.generate_title_variants.return_value = second_variants
        resp = client.post(f"/references/{ref_id}/suggest-title", headers=VALID_HEADERS)

    assert resp.json()["variants"][0]["text"] == "New Variant One"

    ref_resp = client.get(f"/references/{ref_id}", headers=VALID_HEADERS)
    stored = ref_resp.json()["ai_title_variants"]
    assert len(stored) == 3
    assert stored[0]["text"] == "New Variant One"


# ---------------------------------------------------------------------------
# suggest-title — transient Gemini error → 503, status NOT advanced
# ---------------------------------------------------------------------------

def test_suggest_title_transient_gemini_503(client):
    ref_id = _create_ref(client)
    err = RuntimeError("503 UNAVAILABLE: service overloaded")

    with patch("app.services.reference_service.GeminiTextClient") as mock_cls:
        mock_cls.return_value.generate_title_variants.side_effect = err
        # Patch the transient check to recognize our RuntimeError
        with patch("app.routes.references_api._is_transient_gemini_error", return_value=True):
            resp = client.post(f"/references/{ref_id}/suggest-title", headers=VALID_HEADERS)

    assert resp.status_code == 503

    # Status should NOT have advanced
    ref_resp = client.get(f"/references/{ref_id}", headers=VALID_HEADERS)
    assert ref_resp.json()["status"] == "scraped"


# ---------------------------------------------------------------------------
# cutout — auth guard
# ---------------------------------------------------------------------------

def test_cutout_requires_token(client):
    ref_id = _create_ref(client)
    resp = client.post(f"/references/{ref_id}/cutout", json={"image_url": "https://img.example.com/a.jpg"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# cutout — invalid image_url (not in original_images) → 400
# ---------------------------------------------------------------------------

def test_cutout_invalid_image_url_rejected(client):
    ref_id = _create_ref(client)
    resp = client.post(
        f"/references/{ref_id}/cutout",
        headers=VALID_HEADERS,
        json={"image_url": "https://img.example.com/NOT_IN_LIST.jpg"},
    )
    assert resp.status_code == 400


def test_cutout_accepts_etsy_url_at_different_size(client):
    """Stored URL is il_794xN; request comes in upgraded to il_fullxfull → still matches."""
    payload = {
        "source_url": "https://www.etsy.com/listing/22222/sample",
        "source_listing_id": "22222",
        "original_title": "Mug",
        "original_description": "desc",
        "original_images": [
            "https://i.etsystatic.com/12345/r/il/abc/999/il_794xN.999_xyz.jpg",
        ],
    }
    resp = client.post("/references/scrape", headers=VALID_HEADERS, json=payload)
    assert resp.status_code == 201, resp.text
    ref_id = resp.json()["id"]

    upgraded_url = "https://i.etsystatic.com/12345/r/il/abc/999/il_fullxfull.999_xyz.jpg"

    with (
        patch("app.services.reference_service.get_bg_removal_client") as mock_rbg_cls,
        patch("app.clients.r2_storage_client.R2StorageClient") as mock_r2_cls,
        patch("httpx.Client") as mock_http_cls,
    ):
        mock_http = MagicMock()
        mock_http.__enter__ = MagicMock(return_value=mock_http)
        mock_http.__exit__ = MagicMock(return_value=False)
        mock_http.get.return_value = MagicMock(content=_FAKE_PNG_BYTES, raise_for_status=MagicMock())
        mock_http_cls.return_value = mock_http
        mock_rbg_cls.return_value.remove_bg.return_value = _FAKE_PNG_BYTES
        mock_r2_cls.return_value.upload_image.return_value = "https://r2.example.com/designs/fake.png"

        cutout_resp = client.post(
            f"/references/{ref_id}/cutout",
            headers=VALID_HEADERS,
            json={"image_url": upgraded_url},
        )

    assert cutout_resp.status_code == 200, cutout_resp.text
    # Backend downloaded the upgraded (larger) URL — that's the whole point of the upgrade
    mock_http.get.assert_called_once_with(upgraded_url)


# ---------------------------------------------------------------------------
# cutout — 404 on missing reference
# ---------------------------------------------------------------------------

def test_cutout_not_found(client):
    resp = client.post(
        "/references/99999/cutout",
        headers=VALID_HEADERS,
        json={"image_url": "https://img.example.com/a.jpg"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# cutout — happy path: design row created, linked to reference
# ---------------------------------------------------------------------------

def test_cutout_creates_design_and_links(client):
    ref_id = _create_ref(client)

    with (
        patch("app.services.reference_service.get_bg_removal_client") as mock_rbg_cls,
        patch("app.clients.r2_storage_client.R2StorageClient") as mock_r2_cls,
        patch("httpx.Client") as mock_http_cls,
    ):
        # httpx download returns image bytes
        mock_http = MagicMock()
        mock_http.__enter__ = MagicMock(return_value=mock_http)
        mock_http.__exit__ = MagicMock(return_value=False)
        mock_http.get.return_value = MagicMock(content=_FAKE_PNG_BYTES, raise_for_status=MagicMock())
        mock_http_cls.return_value = mock_http

        # remove.bg returns valid transparent PNG
        mock_rbg_cls.return_value.remove_bg.return_value = _FAKE_PNG_BYTES

        # R2 returns a fake URL
        mock_r2_cls.return_value.upload_image.return_value = "https://r2.example.com/designs/fake.png"

        resp = client.post(
            f"/references/{ref_id}/cutout",
            headers=VALID_HEADERS,
            json={"image_url": "https://img.example.com/a.jpg"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "design_id" in body
    assert "file_url" in body
    assert body["file_url"] == "https://r2.example.com/designs/fake.png"

    # Reference should be linked and status enriched
    ref_resp = client.get(f"/references/{ref_id}", headers=VALID_HEADERS)
    ref_body = ref_resp.json()
    assert ref_body["cutout_design_id"] == body["design_id"]
    assert ref_body["status"] == "enriched"


# ---------------------------------------------------------------------------
# cutout — idempotent: second call deletes old design, creates new one
# ---------------------------------------------------------------------------

def test_cutout_idempotent_replaces_old_design(client):
    ref_id = _create_ref(client)

    def _do_cutout():
        with (
            patch("app.services.reference_service.get_bg_removal_client") as mock_rbg_cls,
            patch("app.clients.r2_storage_client.R2StorageClient") as mock_r2_cls,
            patch("httpx.Client") as mock_http_cls,
        ):
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.get.return_value = MagicMock(content=_FAKE_PNG_BYTES, raise_for_status=MagicMock())
            mock_http_cls.return_value = mock_http
            mock_rbg_cls.return_value.remove_bg.return_value = _FAKE_PNG_BYTES
            mock_r2_cls.return_value.upload_image.return_value = "https://r2.example.com/designs/fake.png"
            mock_r2_cls.return_value.delete_object = MagicMock()

            return client.post(
                f"/references/{ref_id}/cutout",
                headers=VALID_HEADERS,
                json={"image_url": "https://img.example.com/a.jpg"},
            )

    r1 = _do_cutout()
    assert r1.status_code == 200, r1.text

    r2 = _do_cutout()
    assert r2.status_code == 200, r2.text
    second_design_id = r2.json()["design_id"]

    # Exactly one Design row should exist (old was deleted before new created)
    db = _Session()
    try:
        from app.models.design import Design as DesignModel
        from sqlalchemy import select as sa_select
        all_designs = list(db.scalars(sa_select(DesignModel)).all())
        assert len(all_designs) == 1, f"Expected 1 design row, got {len(all_designs)}"
    finally:
        db.close()

    # Reference now points to the surviving design
    ref_resp = client.get(f"/references/{ref_id}", headers=VALID_HEADERS)
    assert ref_resp.json()["cutout_design_id"] == second_design_id


# ---------------------------------------------------------------------------
# cutout — crop_box validation
# ---------------------------------------------------------------------------

def test_cutout_rejects_invalid_crop_box(client):
    ref_id = _create_ref(client)
    bad_cases = [
        [0, 0, 1.5, 0.5],         # x+w > 1
        [0, 0, 0.5],              # wrong arity
        [-0.1, 0, 0.5, 0.5],      # negative
        [0, 0, 0, 0.5],           # zero width
    ]
    for crop in bad_cases:
        resp = client.post(
            f"/references/{ref_id}/cutout",
            headers=VALID_HEADERS,
            json={"image_url": "https://img.example.com/a.jpg", "crop_box": crop},
        )
        assert resp.status_code == 422, (crop, resp.text)


# ---------------------------------------------------------------------------
# cutout — crop_box honored: PIL crop applied before remove.bg
# ---------------------------------------------------------------------------

def test_cutout_with_crop_box_applies_pil_crop(client):
    """When crop_box is provided, the bytes sent to remove.bg should be the cropped sub-region."""
    ref_id = _create_ref(client)

    # Build a 100x100 image so we can detect that a crop happened by output size.
    big_buf = io.BytesIO()
    _PILImage.new("RGBA", (100, 100), (10, 200, 30, 255)).save(big_buf, format="PNG")
    big_png = big_buf.getvalue()

    received_bytes = {}

    def capture_remove_bg(image_bytes):
        received_bytes["bytes"] = image_bytes
        return _FAKE_PNG_BYTES

    with (
        patch("app.services.reference_service.get_bg_removal_client") as mock_rbg_cls,
        patch("app.clients.r2_storage_client.R2StorageClient") as mock_r2_cls,
        patch("httpx.Client") as mock_http_cls,
    ):
        mock_http = MagicMock()
        mock_http.__enter__ = MagicMock(return_value=mock_http)
        mock_http.__exit__ = MagicMock(return_value=False)
        mock_http.get.return_value = MagicMock(content=big_png, raise_for_status=MagicMock())
        mock_http_cls.return_value = mock_http

        mock_rbg_cls.return_value.remove_bg.side_effect = capture_remove_bg
        mock_r2_cls.return_value.upload_image.return_value = "https://r2.example.com/designs/c.png"

        resp = client.post(
            f"/references/{ref_id}/cutout",
            headers=VALID_HEADERS,
            json={
                "image_url": "https://img.example.com/a.jpg",
                "crop_box": [0.25, 0.25, 0.5, 0.5],  # center 50x50 crop
            },
        )

    assert resp.status_code == 200, resp.text
    cropped = _PILImage.open(io.BytesIO(received_bytes["bytes"]))
    assert cropped.size == (50, 50), f"Expected 50x50 crop, got {cropped.size}"
