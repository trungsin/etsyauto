"""Tests for keyword_service CRUD functions.

Covers:
- create_keyword success + duplicate raises ValueError
- list_keywords with and without enabled_only filter
- get_keyword found / not found
- delete_keyword removes row
- toggle_keyword round-trip
- touch_last_run sets a timestamp
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.design import Design  # noqa: F401
from app.models.template import Template  # noqa: F401
from app.models.template_variation import TemplateVariation  # noqa: F401
from app.models.reference import Reference  # noqa: F401
from app.models.keyword import Keyword  # noqa: F401
from app.models.idea import Idea  # noqa: F401
from app.models.idea_signal import IdeaSignal  # noqa: F401
from app.models.idea_to_listing import IdeaToListing  # noqa: F401
from app.services import keyword_service


_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_Session = sessionmaker(autocommit=False, autoflush=False, bind=_ENGINE)


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=_ENGINE)
    Base.metadata.create_all(bind=_ENGINE)
    yield


@pytest.fixture
def db():
    session = _Session()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# create_keyword
# ---------------------------------------------------------------------------


def test_create_keyword_success(db):
    kw = keyword_service.create_keyword(db, "coffee mug")
    assert kw.id is not None
    assert kw.term == "coffee mug"
    assert kw.enabled is True


def test_create_keyword_strips_whitespace(db):
    kw = keyword_service.create_keyword(db, "  wall art  ")
    assert kw.term == "wall art"


def test_create_keyword_duplicate_raises(db):
    keyword_service.create_keyword(db, "birthday gift")
    with pytest.raises(ValueError, match="already exists"):
        keyword_service.create_keyword(db, "birthday gift")


# ---------------------------------------------------------------------------
# list_keywords
# ---------------------------------------------------------------------------


def test_list_keywords_empty(db):
    result = keyword_service.list_keywords(db)
    assert result == []


def test_list_keywords_returns_all(db):
    keyword_service.create_keyword(db, "kw1")
    keyword_service.create_keyword(db, "kw2")
    result = keyword_service.list_keywords(db)
    assert len(result) == 2


def test_list_keywords_enabled_only_filter(db):
    kw1 = keyword_service.create_keyword(db, "enabled-kw")
    kw2 = keyword_service.create_keyword(db, "disabled-kw")
    keyword_service.toggle_keyword(db, kw2.id, enabled=False)

    all_kws = keyword_service.list_keywords(db)
    enabled_kws = keyword_service.list_keywords(db, enabled_only=True)

    assert len(all_kws) == 2
    assert len(enabled_kws) == 1
    assert enabled_kws[0].id == kw1.id


# ---------------------------------------------------------------------------
# get_keyword
# ---------------------------------------------------------------------------


def test_get_keyword_found(db):
    kw = keyword_service.create_keyword(db, "found-kw")
    fetched = keyword_service.get_keyword(db, kw.id)
    assert fetched is not None
    assert fetched.term == "found-kw"


def test_get_keyword_not_found(db):
    assert keyword_service.get_keyword(db, 9999) is None


# ---------------------------------------------------------------------------
# delete_keyword
# ---------------------------------------------------------------------------


def test_delete_keyword_success(db):
    kw = keyword_service.create_keyword(db, "to-delete")
    keyword_service.delete_keyword(db, kw.id)
    assert keyword_service.get_keyword(db, kw.id) is None


def test_delete_keyword_not_found(db):
    with pytest.raises(ValueError, match="not found"):
        keyword_service.delete_keyword(db, 9999)


# ---------------------------------------------------------------------------
# toggle_keyword
# ---------------------------------------------------------------------------


def test_toggle_keyword_disable(db):
    kw = keyword_service.create_keyword(db, "toggle-me")
    assert kw.enabled is True

    updated = keyword_service.toggle_keyword(db, kw.id, enabled=False)
    assert updated.enabled is False


def test_toggle_keyword_round_trip(db):
    kw = keyword_service.create_keyword(db, "round-trip")
    keyword_service.toggle_keyword(db, kw.id, enabled=False)
    result = keyword_service.toggle_keyword(db, kw.id, enabled=True)
    assert result.enabled is True


def test_toggle_keyword_not_found(db):
    with pytest.raises(ValueError, match="not found"):
        keyword_service.toggle_keyword(db, 9999, enabled=False)


# ---------------------------------------------------------------------------
# touch_last_run
# ---------------------------------------------------------------------------


def test_touch_last_run_sets_timestamp(db):
    kw = keyword_service.create_keyword(db, "touchable")
    assert kw.last_run_at is None

    keyword_service.touch_last_run(db, kw.id)
    db.expire(kw)
    kw = keyword_service.get_keyword(db, kw.id)
    assert kw.last_run_at is not None


def test_touch_last_run_not_found(db):
    with pytest.raises(ValueError, match="not found"):
        keyword_service.touch_last_run(db, 9999)
