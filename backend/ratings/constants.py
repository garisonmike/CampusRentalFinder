"""Reference data for reviews (ADR-004)."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

#: The rating scale. One scale, used by the overall rating and every category.
MIN_RATING = 1
MAX_RATING = 5

#: Category ratings, all optional. Named here rather than inferred from the
#: model so the aggregate tables and the recompute job iterate over one list.
CATEGORY_RATING_FIELDS = (
    "cleanliness_rating",
    "security_rating",
    "water_reliability_rating",
    "landlord_rating",
    "value_rating",
)

MAX_COMMENT_LENGTH = 2000
MAX_RESPONSE_LENGTH = 1000


class DisputeAnnotation(models.TextChoices):
    """What a reader is told about a disputed stay (ADR-004 §3).

    Neutral by construction. A landlord who disputes honestly and one who
    disputes tactically produce the same annotation, which is precisely why it
    must not read as a verdict.
    """

    DISPUTED = "disputed", _("The landlord disputed this stay")
