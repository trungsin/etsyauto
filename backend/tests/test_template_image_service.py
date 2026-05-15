"""Tests for app.services.template_image_service — CRUD + virtualization + reorder."""
import json

import pytest
from sqlalchemy import select

from app.models.template_image import TemplateImage
from app.services import template_image_service
from tests.conftest import seed_design, seed_template, seed_template_image


class TestList:
    """Test list_for_template — real rows vs virtual."""

    def test_list_returns_real_rows_when_present(self, db_session):
        """With template_images rows, return those ordered by rank."""
        template = seed_template(db_session)
        img1 = seed_template_image(db_session, template.id, rank=1, color="White")
        img2 = seed_template_image(db_session, template.id, rank=2, color="Black")

        rows = template_image_service.list_for_template(db_session, template.id)

        assert len(rows) == 2
        assert rows[0]["id"] == img1.id and rows[0]["rank"] == 1
        assert rows[1]["id"] == img2.id and rows[1]["rank"] == 2
        assert all(not r["is_virtual"] for r in rows)

    def test_list_virtualizes_from_legacy_when_empty(self, db_session):
        """When no template_images rows, synthesize from Template.base_image_url."""
        template = seed_template(db_session)

        rows = template_image_service.list_for_template(db_session, template.id)

        assert len(rows) >= 1
        assert rows[0]["image_url"] == "https://cdn.example.com/templates/default.png"
        assert rows[0]["color"] is None and rows[0]["rank"] == 0
        assert rows[0]["is_virtual"] is True

    def test_list_virtualizes_includes_color_bases(self, db_session):
        """Virtual rows include entries from color_base_images_json."""
        template = seed_template(db_session, colors=["White", "Black"])

        rows = template_image_service.list_for_template(db_session, template.id)

        color_rows = [r for r in rows if r["color"] is not None]
        assert len(color_rows) == 2
        assert {r["color"] for r in color_rows} == {"White", "Black"}
        assert all(r["is_virtual"] for r in color_rows)

    def test_list_returns_empty_when_legacy_empty(self, db_session):
        """Template with no base_image_url and no rows → empty list."""
        template = seed_template(db_session, base_image_url="")
        # Clear color_base_images_json
        template.color_base_images_json = "{}"
        db_session.commit()

        rows = template_image_service.list_for_template(db_session, template.id)

        assert rows == []

    def test_list_returns_nonexistent_template(self, db_session):
        """list_for_template with nonexistent id returns empty."""
        rows = template_image_service.list_for_template(db_session, 999)
        assert rows == []


class TestCreate:
    """Test create — validation + DB insert."""

    def test_create_inserts_row(self, db_session):
        """create() inserts and returns TemplateImageView."""
        template = seed_template(db_session)

        view = template_image_service.create(
            db_session,
            template_id=template.id,
            image_url="https://cdn.example.com/photo.png",
            color="White",
            rank=1,
        )

        assert view["id"] is not None
        assert view["template_id"] == template.id
        assert view["color"] == "White"
        assert view["rank"] == 1
        assert view["is_virtual"] is False

    def test_create_normalizes_color_title_case(self, db_session):
        """Color is title-cased."""
        template = seed_template(db_session, colors=["White"])

        view = template_image_service.create(
            db_session,
            template_id=template.id,
            image_url="https://cdn.example.com/photo.png",
            color="white",
            rank=1,
        )

        assert view["color"] == "White"

    def test_create_validates_max_20(self, db_session):
        """Cannot exceed 20 images per template."""
        template = seed_template(db_session)
        for i in range(20):
            seed_template_image(db_session, template.id, rank=i)

        with pytest.raises(ValueError, match="max 20"):
            template_image_service.create(
                db_session,
                template_id=template.id,
                image_url="https://example.com/21.png",
                rank=20,
            )

    def test_create_validates_max_2_zones(self, db_session):
        """anchor_json with >2 zones is rejected."""
        template = seed_template(db_session)
        bad_anchor = json.dumps({
            "version": 2,
            "zones": [
                {"name": "z1", "kind": "rect", "x": 0, "y": 0, "w": 1, "h": 1},
                {"name": "z2", "kind": "rect", "x": 0, "y": 0, "w": 1, "h": 1},
                {"name": "z3", "kind": "rect", "x": 0, "y": 0, "w": 1, "h": 1},
            ]
        })

        with pytest.raises(ValueError, match="too many|max|zones"):
            template_image_service.create(
                db_session,
                template_id=template.id,
                image_url="https://example.com/photo.png",
                anchor_json=bad_anchor,
            )

    def test_create_validates_color_in_template_options(self, db_session):
        """Color must match template.variation_options.colors."""
        template = seed_template(db_session, colors=["White", "Black"])

        with pytest.raises(ValueError, match="not in template|Color"):
            template_image_service.create(
                db_session,
                template_id=template.id,
                image_url="https://example.com/photo.png",
                color="Purple",
                rank=1,
            )

    def test_create_validates_role_enum(self, db_session):
        """role must be mockup or lifestyle_no_fill."""
        template = seed_template(db_session)

        with pytest.raises(ValueError, match="role"):
            template_image_service.create(
                db_session,
                template_id=template.id,
                image_url="https://example.com/photo.png",
                role="invalid",
            )

    def test_create_allows_multiple_rows(self, db_session):
        """Multiple rows can be created for same template."""
        template = seed_template(db_session)
        img1 = seed_template_image(db_session, template.id, rank=1)
        img2 = template_image_service.create(
            db_session,
            template_id=template.id,
            image_url="https://example.com/photo2.png",
            rank=2,
        )
        assert img2["id"] is not None


class TestMaterialize:
    """Test materialize — legacy→real conversion."""

    def test_materialize_idempotent(self, db_session):
        """materialize on already-materialized template returns 0."""
        template = seed_template(db_session, colors=["White"])
        seed_template_image(db_session, template.id, rank=0)

        result = template_image_service.materialize(db_session, template.id)

        assert result == 0

    def test_materialize_creates_correct_count(self, db_session):
        """materialize creates rows for virtual images."""
        template = seed_template(db_session, colors=["White", "Black"])

        result = template_image_service.materialize(db_session, template.id)

        # Base + 2 colors = 3
        assert result == 3

        rows = db_session.scalars(
            select(TemplateImage).where(TemplateImage.template_id == template.id)
        ).all()
        assert len(rows) == 3

    def test_materialize_idempotent_on_second_call(self, db_session):
        """Second call to materialize returns 0."""
        template = seed_template(db_session, colors=["White"])

        result1 = template_image_service.materialize(db_session, template.id)
        assert result1 == 2  # base + 1 color

        result2 = template_image_service.materialize(db_session, template.id)
        assert result2 == 0


class TestReorder:
    """Test reorder — bulk rank updates."""

    def test_reorder_swaps_ranks_two_pass(self, db_session):
        """reorder swaps ranks atomically via two-pass."""
        template = seed_template(db_session)
        img1 = seed_template_image(db_session, template.id, rank=1)
        img2 = seed_template_image(db_session, template.id, rank=2)

        count = template_image_service.reorder(
            db_session,
            template.id,
            [
                {"id": img1.id, "rank": 2},
                {"id": img2.id, "rank": 1},
            ],
        )

        assert count == 2
        db_session.refresh(img1)
        db_session.refresh(img2)
        assert img1.rank == 2
        assert img2.rank == 1

    def test_reorder_atomic_validation(self, db_session):
        """reorder rejects ids not in template."""
        template = seed_template(db_session)
        other_template = seed_template(db_session, name="Other")
        img1 = seed_template_image(db_session, template.id, rank=1)
        img_other = seed_template_image(db_session, other_template.id, rank=1)

        with pytest.raises(ValueError, match="does not belong"):
            template_image_service.reorder(
                db_session,
                template.id,
                [
                    {"id": img1.id, "rank": 1},
                    {"id": img_other.id, "rank": 2},
                ],
            )

    def test_reorder_empty_list(self, db_session):
        """reorder with empty items returns 0."""
        template = seed_template(db_session)

        count = template_image_service.reorder(db_session, template.id, [])

        assert count == 0


class TestGetAndDelete:
    """Test get and delete."""

    def test_get_returns_view(self, db_session):
        """get() returns TemplateImageView by id."""
        template = seed_template(db_session)
        img = seed_template_image(db_session, template.id, color="White", rank=1)

        view = template_image_service.get(db_session, img.id)

        assert view is not None
        assert view["id"] == img.id
        assert view["color"] == "White"

    def test_get_returns_none_for_missing(self, db_session):
        """get() returns None if not found."""
        view = template_image_service.get(db_session, 999)
        assert view is None

    def test_delete_removes_row(self, db_session):
        """delete() removes the row and returns True."""
        template = seed_template(db_session)
        img = seed_template_image(db_session, template.id, rank=1)

        result = template_image_service.delete(db_session, img.id)

        assert result is True
        rows = db_session.scalars(
            select(TemplateImage).where(TemplateImage.id == img.id)
        ).all()
        assert len(rows) == 0

    def test_delete_returns_false_for_missing(self, db_session):
        """delete() returns False if not found."""
        result = template_image_service.delete(db_session, 999)
        assert result is False


class TestGetViewByColor:
    """Test get_view_by_color — back-compat lookup."""

    def test_get_view_by_color_finds_real_row(self, db_session):
        """get_view_by_color finds a real TemplateImage by color."""
        template = seed_template(db_session)
        img = seed_template_image(db_session, template.id, color="White", rank=1)

        view = template_image_service.get_view_by_color(db_session, template.id, "white")

        assert view is not None
        assert view["id"] == img.id

    def test_get_view_by_color_falls_back_to_virtual(self, db_session):
        """Falls back to virtual rows from color_base_images_json."""
        template = seed_template(db_session, colors=["Black"])

        view = template_image_service.get_view_by_color(db_session, template.id, "Black")

        assert view is not None
        assert view["is_virtual"] is True

    def test_get_view_by_color_none_color_means_universal(self, db_session):
        """color=None matches the universal row."""
        template = seed_template(db_session)

        view = template_image_service.get_view_by_color(db_session, template.id, None)

        assert view is not None
        assert view["color"] is None
