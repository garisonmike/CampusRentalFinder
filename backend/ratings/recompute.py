"""
Aggregate recomputation (ADR-004).

**One implementation, three entry points.** The job, the management command and
the reconciler all call the functions here. If the rebuild and the incremental
update were separate code, they would drift, and only one of them would be
right — with no way to tell which from the outside.

Everything is computed from `Review` alone. That is what makes the aggregates a
cache rather than a second source of truth, and what lets the reconciler *find*
drift instead of merely suspecting it.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction

from properties.models import Property, Unit

from .aggregates import (
    LandlordRatingAggregate,
    PropertyRatingAggregate,
    UnitRatingAggregate,
)
from .constants import MAX_RATING, MIN_RATING
from .models import Review


@dataclass
class RatingSummary:
    """The computed shape of one aggregate row.

    A value object rather than a written row, so the reconciler can compute one
    and compare it against what is stored without touching the database.
    """

    average_rating: Decimal | None = None
    student_count: int = 0
    review_count: int = 0
    rating_distribution: dict[str, int] = field(
        default_factory=lambda: {str(n): 0 for n in range(MIN_RATING, MAX_RATING + 1)}
    )
    last_review_at: dt.datetime | None = None
    property_count: int = 0

    def as_fields(self) -> dict:
        """The subset written to a `BaseRatingAggregate`."""
        return {
            "average_rating": self.average_rating,
            "student_count": self.student_count,
            "review_count": self.review_count,
            "rating_distribution": self.rating_distribution,
            "last_review_at": self.last_review_at,
        }


def _published(**filters):
    """Reviews that count.

    A hidden review is excluded from every aggregate. It is still a row, still
    attached to its stay, and restoring it recomputes the number — which is the
    difference between moderation and deletion.
    """
    return Review.all_objects.filter(is_published=True, **filters).select_related("tenancy")


def _mean(values: Iterable[Decimal | int | None]) -> Decimal | None:
    """The average, or None over an empty set.

    Never zero, never a placeholder. Null is the honest empty state and the
    check constraint enforces that an empty aggregate carries no score.
    """
    present = [Decimal(str(value)) for value in values if value is not None]
    if not present:
        return None

    total = sum(present, Decimal(0))
    return (total / len(present)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _summarise(reviews, *, dedupe_by_tenant: bool) -> RatingSummary:
    """Turn a set of reviews into one aggregate row.

    ``dedupe_by_tenant`` is the whole design decision in one flag. With it on,
    each tenant contributes the **mean of their own reviews** as a single
    voice; without it, every review counts once.

    The distribution and `review_count` always describe the raw reviews, so a
    reader who wants "how many people wrote something" and one who wants "how
    the scores fall" both get an honest answer.
    """
    reviews = list(reviews)
    summary = RatingSummary(review_count=len(reviews))

    if not reviews:
        return summary

    for review in reviews:
        summary.rating_distribution[str(review.rating)] += 1

    summary.last_review_at = max(review.created_at for review in reviews)

    if dedupe_by_tenant:
        by_tenant: dict[int, list[int]] = defaultdict(list)
        for review in reviews:
            by_tenant[review.tenancy.tenant_id].append(review.rating)

        summary.student_count = len(by_tenant)
        # The mean of per-tenant means: one voice each, whatever they wrote.
        summary.average_rating = _mean([_mean(ratings) for ratings in by_tenant.values()])
    else:
        summary.student_count = len({review.tenancy.tenant_id for review in reviews})
        summary.average_rating = _mean([review.rating for review in reviews])

    return summary


# ---------------------------------------------------------------------------
# Computing one aggregate
# ---------------------------------------------------------------------------


def summarise_property(property_id: int) -> RatingSummary:
    """One contribution per (property, tenant).

    A student who moves from a bedsitter to a one-bedroom in the same block has
    two genuinely different experiences to describe, and there is no honest way
    to choose which review to discard. So both are kept and the *aggregate*
    de-duplicates — which is why `student_count` and `review_count` diverge.
    """
    return _summarise(_published(tenancy__unit__property_id=property_id), dedupe_by_tenant=True)


def summarise_unit(unit_id: int) -> RatingSummary:
    """No de-duplication beyond the schema's own.

    One stay, one review, and a tenant cannot hold overlapping stays in one
    unit. A student who returns years later has two separate experiences of the
    same room.
    """
    return _summarise(_published(tenancy__unit_id=unit_id), dedupe_by_tenant=False)


def summarise_landlord(landlord_id: int) -> RatingSummary:
    """The landlord's record across every property they own.

    De-duplicated per tenant for the same reason as the property figure: a
    student who rented two of the same landlord's blocks is one person's
    opinion of that landlord.
    """
    summary = _summarise(
        _published(tenancy__unit__property__landlord_id=landlord_id), dedupe_by_tenant=True
    )
    summary.property_count = (
        Property.all_objects.filter(landlord_id=landlord_id)
        .filter(units__tenancies__review__is_published=True)
        .distinct()
        .count()
    )
    return summary


# ---------------------------------------------------------------------------
# Writing it
# ---------------------------------------------------------------------------


@transaction.atomic
def recompute_property(property_id: int) -> PropertyRatingAggregate:
    summary = summarise_property(property_id)
    aggregate, _created = PropertyRatingAggregate.all_objects.update_or_create(
        property_reviewed_id=property_id, defaults=summary.as_fields()
    )
    return aggregate


@transaction.atomic
def recompute_unit(unit_id: int) -> UnitRatingAggregate:
    summary = summarise_unit(unit_id)
    aggregate, _created = UnitRatingAggregate.all_objects.update_or_create(
        unit_id=unit_id, defaults=summary.as_fields()
    )
    return aggregate


@transaction.atomic
def recompute_landlord(landlord_id: int) -> LandlordRatingAggregate:
    summary = summarise_landlord(landlord_id)
    aggregate, _created = LandlordRatingAggregate.objects.update_or_create(
        landlord_id=landlord_id,
        defaults={**summary.as_fields(), "property_count": summary.property_count},
    )
    return aggregate


@transaction.atomic
def recompute_for_review(review_id: int) -> None:
    """Refresh all three aggregates a review affects.

    Called on create, edit and moderation state change. **Never inline in a
    request** — a page load that recomputes an aggregate is a page load whose
    cost grows with the property's popularity.
    """
    review = (
        Review.all_objects.filter(pk=review_id).select_related("tenancy__unit__property").first()
    )
    if review is None:
        return

    unit = review.tenancy.unit
    recompute_unit(unit.pk)
    recompute_property(unit.property_id)
    recompute_landlord(unit.property.landlord_id)


def recompute_all() -> dict[str, int]:
    """Rebuild every aggregate from `Review`.

    The management command's body. Deliberately the same functions the job
    calls: one implementation, two entry points.
    """
    counts = {"units": 0, "properties": 0, "landlords": 0}

    for unit_id in Unit.all_objects.values_list("pk", flat=True).iterator():
        recompute_unit(unit_id)
        counts["units"] += 1

    for property_id in Property.all_objects.values_list("pk", flat=True).iterator():
        recompute_property(property_id)
        counts["properties"] += 1

    landlord_ids = Property.all_objects.values_list("landlord_id", flat=True).distinct().iterator()
    for landlord_id in landlord_ids:
        recompute_landlord(landlord_id)
        counts["landlords"] += 1

    return counts


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Drift:
    """One aggregate whose stored value disagrees with its source."""

    kind: str
    subject_id: int
    field: str
    stored: object
    computed: object

    def __str__(self) -> str:
        return (
            f"{self.kind}#{self.subject_id}.{self.field}: "
            f"stored={self.stored!r} computed={self.computed!r}"
        )


#: Compared field by field. `computed_at` is excluded because it is metadata
#: about the cache rather than a claim about the reviews.
RECONCILED_FIELDS = (
    "average_rating",
    "student_count",
    "review_count",
    "rating_distribution",
)


def _compare(kind: str, subject_id: int, stored, summary: RatingSummary) -> list[Drift]:
    drifts = []
    expected = summary.as_fields()

    for name in RECONCILED_FIELDS:
        if getattr(stored, name) != expected[name]:
            drifts.append(
                Drift(
                    kind=kind,
                    subject_id=subject_id,
                    field=name,
                    stored=getattr(stored, name),
                    computed=expected[name],
                )
            )
    return drifts


def reconcile_properties(sample: list[int] | None = None) -> list[Drift]:
    """Recompute stored property aggregates and report disagreements.

    **It never silently corrects.** Self-healing would hide the bug that caused
    the drift, and the bug is the thing worth knowing about. A drift alert
    means "recompute this one and then find out why", not "the system fixed
    itself".
    """
    stored_rows = PropertyRatingAggregate.all_objects.all()
    if sample is not None:
        stored_rows = stored_rows.filter(property_reviewed_id__in=sample)

    drifts: list[Drift] = []
    for row in stored_rows:
        drifts.extend(
            _compare(
                "property",
                row.property_reviewed_id,
                row,
                summarise_property(row.property_reviewed_id),
            )
        )
    return drifts


def reconcile_units(sample: list[int] | None = None) -> list[Drift]:
    """As :func:`reconcile_properties`, for units."""
    stored_rows = UnitRatingAggregate.all_objects.all()
    if sample is not None:
        stored_rows = stored_rows.filter(unit_id__in=sample)

    drifts: list[Drift] = []
    for row in stored_rows:
        drifts.extend(_compare("unit", row.unit_id, row, summarise_unit(row.unit_id)))
    return drifts


def reconcile_landlords(sample: list[int] | None = None) -> list[Drift]:
    """As :func:`reconcile_properties`, for landlords."""
    stored_rows = LandlordRatingAggregate.objects.all()
    if sample is not None:
        stored_rows = stored_rows.filter(landlord_id__in=sample)

    drifts: list[Drift] = []
    for row in stored_rows:
        drifts.extend(
            _compare("landlord", row.landlord_id, row, summarise_landlord(row.landlord_id))
        )
    return drifts


def landlord_ids_with_aggregates() -> list[int]:
    """Every landlord carrying an aggregate row."""
    return list(LandlordRatingAggregate.objects.values_list("landlord_id", flat=True))
