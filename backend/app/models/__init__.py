"""Eagerly import every ORM model so SQLAlchemy's class registry is complete
whenever any model is imported. Required for string-name relationships like
Idea.design -> "Design" to resolve in standalone scripts and isolated routes."""

from app.models.api_credential import ApiCredential  # noqa: F401
from app.models.design import Design  # noqa: F401  — FK target for Idea, Listing
from app.models.job import Job  # noqa: F401
from app.models.keyword import Keyword  # noqa: F401
from app.models.listing import Listing  # noqa: F401
from app.models.mockup_variant import MockupVariant  # noqa: F401
from app.models.reference import Reference  # noqa: F401
from app.models.template import Template  # noqa: F401
from app.models.template_variation import TemplateVariation  # noqa: F401
from app.models.title_variant import TitleVariant  # noqa: F401
from app.models.idea import Idea  # noqa: F401
from app.models.idea_signal import IdeaSignal  # noqa: F401
from app.models.idea_to_listing import IdeaToListing  # noqa: F401
