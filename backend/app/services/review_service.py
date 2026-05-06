"""Review service — selection logic for approving title and mockup variants."""
import logging

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.listing import Listing
from app.models.mockup_variant import MockupVariant
from app.models.title_variant import TitleVariant

logger = logging.getLogger(__name__)

# Notion selection values map to 1-based variant indices
_VARIANT_MAP = {"variant-1": 1, "variant-2": 2, "variant-3": 3}


def mark_variants_selected(
    session: Session,
    listing_id: int,
    title_selection: str,
    mockup_selection: str,
) -> None:
    """Mark the chosen title and mockup variants as selected, set listing.status='approved'.

    Args:
        session: Active SQLAlchemy session.
        listing_id: Internal listing PK.
        title_selection: Notion select value — 'variant-1', 'variant-2', or 'variant-3'.
        mockup_selection: Notion select value — 'variant-1', 'variant-2', or 'variant-3'.

    Raises:
        ValueError: if selection strings are not recognised or variants not found.
    """
    title_idx = _parse_variant_index(title_selection, "title")
    mockup_idx = _parse_variant_index(mockup_selection, "mockup")

    # Validate variants exist for this listing
    title_variants = (
        session.query(TitleVariant)
        .filter(TitleVariant.listing_id == listing_id)
        .order_by(TitleVariant.id)
        .all()
    )
    mockup_variants = (
        session.query(MockupVariant)
        .filter(MockupVariant.listing_id == listing_id)
        .order_by(MockupVariant.id)
        .all()
    )

    if len(title_variants) < title_idx:
        raise ValueError(
            f"Listing {listing_id}: requested title variant {title_idx} "
            f"but only {len(title_variants)} exist"
        )
    if len(mockup_variants) < mockup_idx:
        raise ValueError(
            f"Listing {listing_id}: requested mockup variant {mockup_idx} "
            f"but only {len(mockup_variants)} exist"
        )

    chosen_title = title_variants[title_idx - 1]
    chosen_mockup = mockup_variants[mockup_idx - 1]

    # Clear previous selections then mark new ones
    session.execute(
        update(TitleVariant)
        .where(TitleVariant.listing_id == listing_id)
        .values(selected=False)
    )
    session.execute(
        update(MockupVariant)
        .where(MockupVariant.listing_id == listing_id)
        .values(selected=False)
    )

    chosen_title.selected = True
    chosen_mockup.selected = True

    session.execute(
        update(Listing)
        .where(Listing.id == listing_id)
        .values(status="approved")
    )
    session.commit()

    logger.info(
        "Listing %d approved: title_variant_id=%d, mockup_variant_id=%d",
        listing_id,
        chosen_title.id,
        chosen_mockup.id,
    )


def _parse_variant_index(selection: str, kind: str) -> int:
    """Convert 'variant-N' string to 1-based integer index.

    Raises:
        ValueError: if the string is not a recognised variant key.
    """
    idx = _VARIANT_MAP.get(selection)
    if idx is None:
        raise ValueError(
            f"Unknown {kind} selection {selection!r}. "
            f"Expected one of: {list(_VARIANT_MAP.keys())}"
        )
    return idx
