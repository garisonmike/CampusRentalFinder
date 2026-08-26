"""
Reviews (ADR-004).

A review hangs off a ``Tenancy`` and nothing else. That single `OneToOneField`
is the whole trust property: to write a review you must have a stay the
platform either witnessed or tested, and the database will not store a review
without one.

The draft this replaces let any authenticated user review any property they had
never been to, with self-reported move-in dates nobody checked. Everything
below exists because of that.

**The reviewer and the unit are not stored here.** They are
``review.tenancy.tenant`` and ``review.tenancy.unit``. A denormalised copy is a
chance for the two to disagree, and disagreement here *is* the trust property
failing.
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

if TYPE_CHECKING:  # django-stubs is a dev dependency; prod must not import it.
    from django_stubs_ext import StrOrPromise

from accounts.models import User
from config.tenancy import TenantScopedModel
from tenancies.models import Tenancy

from .constants import (
    CATEGORY_RATING_FIELDS,
    MAX_COMMENT_LENGTH,
    MAX_RATING,
    MAX_RESPONSE_LENGTH,
    MIN_RATING,
)


def _rating_field(label: StrOrPromise, *, null: bool = False) -> models.PositiveSmallIntegerField:
    """One rating on the shared 1..5 scale.

    Validators for the API's error messages; a check constraint for the truth.
    """
    return models.PositiveSmallIntegerField(
        label,
        null=null,
        blank=null,
        validators=[MinValueValidator(MIN_RATING), MaxValueValidator(MAX_RATING)],
    )


def _default_editable_until() -> dt.datetime:
    return timezone.now() + dt.timedelta(days=settings.REVIEW_EDIT_WINDOW_DAYS)


class Review(TenantScopedModel):
    """One student's account of one stay.

    Reachable only through a confirmed tenancy, editable only for a window, and
    carrying no stored judgement about whether it should be believed.
    """

    tenant_lookup = "tenancy__unit__property__campus_distances__university"

    #: PROTECT, not CASCADE. Deleting the tenancy would delete the evidence for
    #: the review while leaving the review's meaning intact -- a state where a
    #: published review no longer has anything behind it.
    tenancy = models.OneToOneField(
        Tenancy,
        on_delete=models.PROTECT,
        related_name="review",
        help_text=_("The stay being reviewed. Not nullable: it is the whole point."),
    )

    rating = _rating_field(_("overall rating"))
    cleanliness_rating = _rating_field(_("cleanliness"), null=True)
    security_rating = _rating_field(_("security"), null=True)
    #: The complaint that actually recurs in Kenyan student housing, which is
    #: why it is a first-class category rather than something to find in prose.
    water_reliability_rating = _rating_field(_("water reliability"), null=True)
    landlord_rating = _rating_field(_("landlord"), null=True)
    value_rating = _rating_field(_("value for money"), null=True)

    comment = models.TextField(_("comment"), max_length=MAX_COMMENT_LENGTH, blank=True)
    would_recommend = models.BooleanField(_("would recommend"), null=True, blank=True)

    #: Frozen after this. Stored rather than computed so that changing the
    #: window later does not retroactively reopen reviews that had closed.
    editable_until = models.DateTimeField(_("editable until"), default=_default_editable_until)

    is_published = models.BooleanField(_("published"), default=True)
    hidden_reason = models.CharField(_("hidden reason"), max_length=200, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Review")
        verbose_name_plural = _("Reviews")
        ordering = ["-created_at"]
        base_manager_name = "all_objects"
        default_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["is_published", "-created_at"], name="review_published_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(rating__gte=MIN_RATING) & Q(rating__lte=MAX_RATING),
                name="review_rating_range",
            ),
            *[
                models.CheckConstraint(
                    condition=Q(**{f"{field}__isnull": True})
                    | (Q(**{f"{field}__gte": MIN_RATING}) & Q(**{f"{field}__lte": MAX_RATING})),
                    name=f"review_{field}_range",
                )
                for field in CATEGORY_RATING_FIELDS
            ],
            # A hidden review says why. Staff-only, and unexplained moderation
            # of the content the platform exists to protect is not acceptable.
            models.CheckConstraint(
                condition=Q(is_published=True) | ~Q(hidden_reason=""),
                name="review_hidden_states_why",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.rating}/5 for {self.tenancy.unit}"

    # -- Derived, never stored ---------------------------------------------

    def is_editable(self, *, now: dt.datetime | None = None) -> bool:
        """Whether the edit window is still open.

        A method, not a property: `tenancy` is a field on this model and the
        `@property` builtin is shadowed in this class's namespace as a result
        (see tests/test_architecture.py).
        """
        return (now or timezone.now()) < self.editable_until

    def reviewer(self) -> User:
        """The author. Read through the tenancy; never copied onto the review."""
        return self.tenancy.tenant


class ReviewResponse(TenantScopedModel):
    """The landlord's single reply to a review (ADR-004).

    One response, ever, enforced by the schema rather than by the
    ``if review.landlord_response:`` check the draft used -- which the admin, a
    management command and a data migration all routed around.
    """

    tenant_lookup = "review__tenancy__unit__property__campus_distances__university"

    review = models.OneToOneField(Review, on_delete=models.CASCADE, related_name="response")
    #: Landlord only, never a caretaker. A caretaker can confirm a tenancy;
    #: speaking for the business in public is the owner's own act (ADR-003).
    author = models.ForeignKey(User, on_delete=models.PROTECT, related_name="review_responses")
    body = models.TextField(_("response"), max_length=MAX_RESPONSE_LENGTH)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Review response")
        verbose_name_plural = _("Review responses")
        ordering = ["-created_at"]
        base_manager_name = "all_objects"
        default_manager_name = "all_objects"
        constraints = [
            models.CheckConstraint(condition=~Q(body=""), name="review_response_not_empty"),
        ]

    def __str__(self) -> str:
        return f"response to {self.review_id}"


# The aggregate caches live in their own module for readability, but Django
# discovers models through `models`. Re-exported rather than moved: the
# distinction between the source of truth and the caches over it is worth
# keeping visible in the file layout.
from .aggregates import (  # noqa: E402
    BaseRatingAggregate,
    LandlordRatingAggregate,
    PropertyRatingAggregate,
    UnitRatingAggregate,
)

__all__ = [
    "BaseRatingAggregate",
    "LandlordRatingAggregate",
    "PropertyRatingAggregate",
    "Review",
    "ReviewResponse",
    "UnitRatingAggregate",
]
