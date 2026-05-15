"""Shared test fixtures and helpers for backend tests."""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.design import Design
from app.models.template import Template
from app.models.template_image import TemplateImage


_TEST_DB_URL = "sqlite:///:memory:"
_engine = create_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


@pytest.fixture(autouse=True)
def _reset_db():
    """Auto-reset DB between tests."""
    Base.metadata.drop_all(bind=_engine)
    Base.metadata.create_all(bind=_engine)
    yield


@pytest.fixture
def db_session():
    """Provide a test DB session."""
    s = _TestingSessionLocal()
    yield s
    s.close()


def seed_template(
    session,
    *,
    name: str = "Comfort Tee",
    colors: list[str] | None = None,
    primary: str = "White",
    base_image_url: str = "https://cdn.example.com/templates/default.png",
) -> Template:
    """Create a Template with optional color base images."""
    if colors is None:
        colors = ["White", "Black"]

    color_bases = {c: f"https://cdn.example.com/templates/{c.lower()}.png" for c in colors}
    options = {
        "sizes": [
            {"name": "S", "price_cents": 1900},
            {"name": "M", "price_cents": 1900},
            {"name": "XL", "price_cents": 2200},
        ],
        "colors": colors,
        "primary_color": primary,
        "etsy_taxonomy_id": 1209,
    }

    t = Template(
        name=name,
        category="apparel",
        base_image_url=base_image_url,
        composite_anchor_json=json.dumps({"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6}),
        default_price_cents=1900,
        variation_options_json=json.dumps(options),
        color_base_images_json=json.dumps(color_bases),
    )
    session.add(t)
    session.commit()
    session.refresh(t)
    return t


def seed_design(
    session,
    *,
    name: str = "logo",
    source_type: str = "upload",
    file_url: str = "https://cdn.example.com/designs/logo.png",
) -> Design:
    """Create a Design."""
    d = Design(
        name=name,
        source_type=source_type,
        file_url=file_url,
        width=1000,
        height=1000,
    )
    session.add(d)
    session.commit()
    session.refresh(d)
    return d


def seed_template_image(
    session,
    template_id: int,
    *,
    image_url: str = "https://cdn.example.com/templates/image.png",
    color: str | None = None,
    rank: int = 0,
    role: str = "mockup",
    anchor_json: str = '{"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6}',
) -> TemplateImage:
    """Create a TemplateImage row."""
    row = TemplateImage(
        template_id=template_id,
        image_url=image_url,
        color=color,
        rank=rank,
        role=role,
        anchor_json=anchor_json,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def seed_template_with_pool(
    session,
    *,
    n_images: int = 3,
    with_colors: bool = True,
    with_lifestyle: bool = False,
) -> tuple[int, list[int]]:
    """Create a template + N template_image rows. Returns (template_id, [image_ids])."""
    colors = ["White", "Black", "Sand"] if with_colors else []
    template = seed_template(session, colors=colors)

    image_ids = []

    # Universal image at rank 0
    img = seed_template_image(session, template.id, rank=0, color=None, role="mockup")
    image_ids.append(img.id)

    # Per-color images
    for i, color in enumerate(colors[:n_images - 1], start=1):
        img = seed_template_image(
            session,
            template.id,
            rank=i,
            color=color,
            role="lifestyle_no_fill" if with_lifestyle else "mockup",
            image_url=f"https://cdn.example.com/templates/{color.lower()}.png",
        )
        image_ids.append(img.id)

    return template.id, image_ids
