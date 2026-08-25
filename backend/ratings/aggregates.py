"""
Rating aggregate tables (ADR-004).

Three caches — per property, per unit, per landlord — rebuilt from `Review` and
nothing else.

**Why these and not a denormalised `Review.property_id`.** Both are
denormalisation; the difference is entirely in how they fail. A duplicated
foreign key that drifts from `tenancy.unit.property` is silent corruption:
reviews attributed to the wrong building, with no way to tell which value is
right. A stale cached number is found by the reconciler and rebuilt from
source. A cache with a reconciler fails loudly; a duplicated foreign key fails
silently, and on a platform whose product is trustworthy ratings that decides
it.

**`student_count` and `review_count` are separate columns and are expected to
differ.** That divergence *is* the de-duplication: a student who moves from a
bedsitter to a one-bedroom in the same block writes two genuine reviews, and
contributes one voice to the block's score.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from accounts.models import LandlordProfile
from config.tenancy import TenantScopedModel
from properties.models import Property, Unit

from .constants import MAX_RATING, MIN_RATING


def _empty_distribution() -> dict[str, int]:
    """A 1-5 histogram with every bucket present.

    Present-and-zero rather than absent, so a reader never has to decide
    whether a missing key means "none" or "not computed".
    """
    return {str(score): 0 for score in range(MIN_RATING, MAX_RATING + 1)}


class BaseRatingAggregate(models.Model):
    """Fields common to all three caches.

    ``average_rating`` is **nullable, and null is the honest empty state.** No
    review means "no verified reviews yet" — never a neutral score, never a
    placeholder star count, never an average over zero rows. On a trust
    platform a fabricated signal is worse than no signal, because it is
    indistinguishable from a real one and it is the platform doing the
    fabricating (ADR-004).
    """

    average_rating = models.DecimalField(
        _("average rating"),
        max_digits=3,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("Null means no verified reviews yet. Never a default score."),
    )

    #: Distinct contributors. The public figure, labelled "from N students" and
    #: never "N reviews", so the denominator means what a reader assumes.
    student_count = models.PositiveIntegerField(_("students"), default=0)

    #: Rows behind it. Differs from student_count whenever anyone reviewed more
    #: than one stay in the same scope, which is exactly the case the
    #: de-duplication exists for.
    review_count = models.PositiveIntegerField(_("reviews"), default=0)

    rating_distribution = models.JSONField(_("rating distribution"), default=_empty_distribution)

    last_review_at = models.DateTimeField(_("last review at"), null=True, blank=True)
    computed_at = models.DateTimeField(_("computed at"), auto_now=True)

    class Meta:
        abstract = True

    def is_empty(self) -> bool:
        """Whether to render the honest empty state rather than a number."""
        return self.review_count == 0


class PropertyRatingAggregate(BaseRatingAggregate, TenantScopedModel):
    """One row per property. **One contribution per (property, tenant).**

    A student who moved twice within one block would otherwise be weighted 2x
    in that block's score.
    """

    tenant_lookup = "property__campus_distances__university"

    property_reviewed = models.OneToOneField(
        Property,
        on_delete=models.CASCADE,
        related_name="rating_aggregate",
        # Not named `property`: a field called `property` shadows the builtin
        # in the class namespace and breaks any @property on the same model
        # (see tests/test_architecture.py).
        db_column="property_id",
    )

    class Meta(BaseRatingAggregate.Meta):
        verbose_name = _("Property rating")
        verbose_name_plural = _("Property ratings")
        base_manager_name = "all_objects"
        default_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["-average_rating"], name="prop_rating_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(average_rating__isnull=True)
                | (Q(average_rating__gte=MIN_RATING) & Q(average_rating__lte=MAX_RATING)),
                name="property_aggregate_rating_range",
            ),
            # student_count <= review_count, always: de-duplication can only
            # ever collapse rows, never invent contributors.
            models.CheckConstraint(
                condition=Q(student_count__lte=models.F("review_count")),
                name="property_aggregate_students_within_reviews",
            ),
            # An average with no rows behind it is the fabricated signal this
            # design exists to prevent.
            models.CheckConstraint(
                condition=Q(review_count__gt=0) | Q(average_rating__isnull=True),
                name="property_aggregate_empty_has_no_score",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.property_reviewed}: {self.average_rating or 'no reviews'}"


class UnitRatingAggregate(BaseRatingAggregate, TenantScopedModel):
    """One row per unit.

    No de-duplication here beyond the schema's own: one stay, one review, and a
    tenant cannot hold overlapping stays in the same unit. A student who
    genuinely returns to the same unit years later contributes twice, and that
    is two separate experiences of the same room.
    """

    tenant_lookup = "unit__property__campus_distances__university"

    unit = models.OneToOneField(Unit, on_delete=models.CASCADE, related_name="rating_aggregate")

    class Meta(BaseRatingAggregate.Meta):
        verbose_name = _("Unit rating")
        verbose_name_plural = _("Unit ratings")
        base_manager_name = "all_objects"
        default_manager_name = "all_objects"
        constraints = [
            models.CheckConstraint(
                condition=Q(average_rating__isnull=True)
                | (Q(average_rating__gte=MIN_RATING) & Q(average_rating__lte=MAX_RATING)),
                name="unit_aggregate_rating_range",
            ),
            models.CheckConstraint(
                condition=Q(student_count__lte=models.F("review_count")),
                name="unit_aggregate_students_within_reviews",
            ),
            models.CheckConstraint(
                condition=Q(review_count__gt=0) | Q(average_rating__isnull=True),
                name="unit_aggregate_empty_has_no_score",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.unit}: {self.average_rating or 'no reviews'}"


class LandlordRatingAggregate(BaseRatingAggregate):
    """One row per landlord, across every property they own.

    **Not tenant-scoped.** A landlord's record spans universities, and scoping
    it to one would report a different number to each — which is worse than
    reporting one number, because both would look authoritative.

    This is the secondary signal for the cold start (ADR-004): a property with
    no reviews can show its landlord's record across their other properties,
    **explicitly labelled as being about the landlord**, never about this
    property. It is a reputation number attached to a person, so it is
    rebuildable from source and never adjusted by hand.
    """

    #: LandlordProfile, matching Property.landlord. Keyed on the profile
    #: rather than the User so the two cannot point at different things.
    landlord = models.OneToOneField(
        LandlordProfile, on_delete=models.CASCADE, related_name="rating_aggregate"
    )

    #: How many properties the figure spans. Shown alongside it, because "4.2
    #: across one property" and "4.2 across nine" are different claims.
    property_count = models.PositiveIntegerField(_("properties"), default=0)

    class Meta(BaseRatingAggregate.Meta):
        verbose_name = _("Landlord rating")
        verbose_name_plural = _("Landlord ratings")
        constraints = [
            models.CheckConstraint(
                condition=Q(average_rating__isnull=True)
                | (Q(average_rating__gte=MIN_RATING) & Q(average_rating__lte=MAX_RATING)),
                name="landlord_aggregate_rating_range",
            ),
            models.CheckConstraint(
                condition=Q(student_count__lte=models.F("review_count")),
                name="landlord_aggregate_students_within_reviews",
            ),
            models.CheckConstraint(
                condition=Q(review_count__gt=0) | Q(average_rating__isnull=True),
                name="landlord_aggregate_empty_has_no_score",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.landlord}: {self.average_rating or 'no reviews'}"
