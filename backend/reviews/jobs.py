"""
Aggregate jobs (ADR-004, docs/OPERATIONS.md §3b).

Both entry points call `reviews.recompute`. Nothing here reimplements an
average.
"""

from __future__ import annotations

import django_rq
import structlog

from .recompute import (
    recompute_for_review,
    reconcile_landlords,
    reconcile_properties,
    reconcile_units,
)

logger = structlog.get_logger("campusrental.jobs")


def refresh_aggregates_for_review(review_id: int) -> None:
    """Recompute the three aggregates one review affects."""
    recompute_for_review(review_id)
    logger.info("rating_aggregates_refreshed", review_id=review_id)


def enqueue_aggregate_refresh(review_id: int) -> None:
    """Queue a refresh. Called on review create, edit and moderation change.

    Never inline in a request: a page load that recomputes an aggregate is a
    page load whose cost grows with the property's popularity.
    """
    django_rq.get_queue("default").enqueue(refresh_aggregates_for_review, review_id)


def reconcile_rating_aggregates(sample_size: int = 100) -> int:
    """Recompute a rolling sample, compare, and **alert on drift**.

    It never silently corrects. Self-healing hides the bug that caused the
    drift, and the bug is the thing worth knowing about — a drift alert means
    "recompute this one and then find out why", not "the system fixed itself".

    Returns the number of drifted fields, which is what the alert reads.
    """
    from .aggregates import (
        LandlordRatingAggregate,
        PropertyRatingAggregate,
        UnitRatingAggregate,
    )

    # Oldest-computed first, so the sample rolls rather than re-checking the
    # same rows for ever. computed_at is auto_now and therefore NOT NULL, so
    # there is no null-ordering trap here (docs/OPERATIONS.md).
    properties = list(
        PropertyRatingAggregate.all_objects.order_by("computed_at").values_list(
            "property_reviewed_id", flat=True
        )[:sample_size]
    )
    units = list(
        UnitRatingAggregate.all_objects.order_by("computed_at").values_list("unit_id", flat=True)[
            :sample_size
        ]
    )
    landlords = list(
        LandlordRatingAggregate.objects.order_by("computed_at").values_list(
            "landlord_id", flat=True
        )[:sample_size]
    )

    drifts = [
        *reconcile_properties(properties),
        *reconcile_units(units),
        *reconcile_landlords(landlords),
    ]

    # A reviewed property with NO aggregate row at all.
    #
    # The sampling above walks existing aggregates, so a property whose
    # aggregate was never created is not sampled, does not drift, and does not
    # appear in the count -- and the job logs `drifted=0`, which reads as
    # health. On a seeded platform with 33 reviews and zero aggregates it
    # reported exactly that: a clean bill from a check that had looked at
    # nothing.
    #
    # This is the shape docs/OPERATIONS.md calls "a check whose scope is
    # narrower than the belief attached to it". The fix is not a wider sample;
    # it is asking the other question -- who should have an aggregate and does
    # not -- and reporting it as its own number so the two can never be
    # confused.
    missing = _reviewed_subjects_without_aggregates()

    for kind, subject_id in missing:
        logger.error(
            "rating_aggregate_missing",
            kind=kind,
            subject_id=subject_id,
            reason="reviews exist but no aggregate row does",
        )

    for drift in drifts:
        logger.error(
            # ERROR: a rating that disagrees with its source is the worst
            # available failure on a platform selling trustworthy ratings.
            # Every page still renders a number; the number is simply wrong.
            "rating_aggregate_drift",
            kind=drift.kind,
            subject_id=drift.subject_id,
            field=drift.field,
            stored=str(drift.stored),
            computed=str(drift.computed),
        )

    logger.info(
        "rating_reconciliation",
        sampled=len(properties) + len(units) + len(landlords),
        drifted=len(drifts),
        missing=len(missing),
    )
    return len(drifts) + len(missing)


def _reviewed_subjects_without_aggregates() -> list[tuple[str, int]]:
    """Properties and units that have published reviews but no aggregate.

    Reported separately from drift because they are a different failure with a
    different cause. Drift means the recompute job ran and the number moved
    since; absence means it never ran at all -- a queue that was down, a
    review written by a path that skipped the enqueue, or a restore from a
    backup that brought reviews without their caches.
    """
    from django.db.models import Exists, OuterRef

    from .aggregates import PropertyRatingAggregate, UnitRatingAggregate
    from .models import Review

    reviewed_properties = (
        Review.all_objects.filter(is_published=True)
        .exclude(
            Exists(
                PropertyRatingAggregate.all_objects.filter(
                    property_reviewed_id=OuterRef("tenancy__unit__property_id")
                )
            )
        )
        .values_list("tenancy__unit__property_id", flat=True)
        .distinct()
    )
    reviewed_units = (
        Review.all_objects.filter(is_published=True)
        .exclude(
            Exists(UnitRatingAggregate.all_objects.filter(unit_id=OuterRef("tenancy__unit_id")))
        )
        .values_list("tenancy__unit_id", flat=True)
        .distinct()
    )

    return [
        *(("property", subject_id) for subject_id in reviewed_properties),
        *(("unit", subject_id) for subject_id in reviewed_units),
    ]
