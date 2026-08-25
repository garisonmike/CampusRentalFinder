"""
Aggregate jobs (ADR-004, docs/OPERATIONS.md §3b).

Both entry points call `ratings.recompute`. Nothing here reimplements an
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
    )
    return len(drifts)
