"""
Background jobs for claims and disputes (ADR-004).

Both jobs implement a **deadline that binds the platform, not the tenant**.
Landlord silence auto-confirms a claim; platform backlog auto-resolves a
dispute in the tenant's favour. That direction is the whole point: an
indefinite block turns our capacity into a landlord veto by proxy, and the
default on our own failure must favour the party with less power.

Both fail silently if the worker stops — no request 500s, nothing errors — so
both are monitored on the **age of the oldest overdue row**, never on job
success and never on a count. `docs/OPERATIONS.md` states the thresholds.
"""

from __future__ import annotations

import datetime as dt

import django_rq
import structlog
from django.utils import timezone

from config.jobs.sweeps import oldest_overdue_age

from .constants import ClaimStatus, ConfirmationSource
from .models import TenancyClaim
from .services import confirm_claim

logger = structlog.get_logger("campusrental.jobs")


# ---------------------------------------------------------------------------
# Sweep ordering
# ---------------------------------------------------------------------------
#
# Every sweep below orders on a NOT NULL deadline column and filters
# `deadline__lte=now` first, so PostgreSQL's NULLS LAST default cannot strand a
# row the way it did in properties.jobs.route_stale_distances, where a nullable
# `routed_at` ordered ascending handed back already-processed rows for ever.
#
# The filter is what makes this safe, not the ordering: a row with a null
# deadline is excluded by `__lte` regardless of where it would have sorted. If
# a future sweep orders on a nullable column WITHOUT such a filter, it must say
# `nulls_first=True` explicitly.


# ---------------------------------------------------------------------------
# Claim auto-confirmation
# ---------------------------------------------------------------------------


def overdue_claims(now: dt.datetime | None = None):
    """Pending claims whose confirmation window has elapsed."""
    return TenancyClaim.all_objects.filter(
        status=ClaimStatus.PENDING,
        confirmation_deadline__lte=now or timezone.now(),
    )


def auto_confirm_claim(claim_id: int) -> None:
    """Confirm one claim the landlord neither confirmed nor disputed.

    Landlord silence is a signal, not a veto (ADR-004). ``confirmation_source``
    records that nobody decided this, and ``confirmed_by`` stays null because
    silence has no author.
    """
    claim = TenancyClaim.all_objects.filter(pk=claim_id).first()
    if claim is None:
        # Deleted between enqueue and run. Jobs must tolerate the row being
        # gone rather than treating it as an error.
        logger.info("claim_auto_confirm_skipped", claim_id=claim_id, reason="deleted")
        return

    if claim.status != ClaimStatus.PENDING:
        # Confirmed or disputed after the sweep enqueued it. The race is
        # expected and the human action wins.
        logger.info(
            "claim_auto_confirm_skipped",
            claim_id=claim_id,
            reason="no_longer_pending",
            status=claim.status,
        )
        return

    confirm_claim(claim, source=ConfirmationSource.AUTO)
    logger.info("claim_auto_confirmed", claim_id=claim_id)


def sweep_overdue_claims(limit: int = 500, now: dt.datetime | None = None) -> int:
    """Enqueue auto-confirmation for every claim past its window.

    Scheduled hourly. Oldest first, so a backlog drains in the order the
    deadlines passed rather than starving the earliest claims.
    """
    now = now or timezone.now()
    queryset = overdue_claims(now)

    waiting = oldest_overdue_age(queryset, "confirmation_deadline")
    claim_ids = list(
        queryset.order_by("confirmation_deadline").values_list("pk", flat=True)[:limit]
    )

    for claim_id in claim_ids:
        django_rq.get_queue("default").enqueue(auto_confirm_claim, claim_id)

    logger.info(
        "claim_confirmation_sweep",
        enqueued=len(claim_ids),
        oldest_overdue_seconds=None if waiting is None else int(waiting.total_seconds()),
    )
    return len(claim_ids)


# ---------------------------------------------------------------------------
# Dispute auto-resolution
# ---------------------------------------------------------------------------


def overdue_disputes(now: dt.datetime | None = None):
    """Escalated claims whose resolution window has elapsed."""
    return TenancyClaim.all_objects.filter(
        status=ClaimStatus.ESCALATED,
        escalation_deadline__lte=now or timezone.now(),
    )


def auto_resolve_dispute(claim_id: int) -> None:
    """Resolve one escalated dispute in the tenant's favour.

    **The deadline binds us, not the tenant.** Missing it is the platform's
    failure, so the claim confirms and the review becomes possible. The
    resulting review carries a neutral annotation — "the landlord disputed this
    stay" — derived at read time, never a discredit and never a stored boolean
    (ADR-004 §3, §3a).
    """
    claim = TenancyClaim.all_objects.filter(pk=claim_id).first()
    if claim is None:
        logger.info("dispute_auto_resolve_skipped", claim_id=claim_id, reason="deleted")
        return

    if claim.status != ClaimStatus.ESCALATED:
        # An admin got to it first, which is the outcome we would prefer.
        logger.info(
            "dispute_auto_resolve_skipped",
            claim_id=claim_id,
            reason="already_resolved",
            status=claim.status,
        )
        return

    confirm_claim(claim, source=ConfirmationSource.DISPUTE_TIMEOUT)
    logger.warning(
        # WARNING, not INFO: every one of these is a case we failed to work in
        # time. It is the correct default, and it is still a miss.
        "dispute_auto_resolved_on_timeout",
        claim_id=claim_id,
        escalation_reason=claim.escalation_reason,
    )


def sweep_overdue_disputes(limit: int = 500, now: dt.datetime | None = None) -> int:
    """Enqueue auto-resolution for every dispute past its deadline.

    Scheduled hourly. This returning anything but zero is a signal about the
    platform, not about landlords.
    """
    now = now or timezone.now()
    queryset = overdue_disputes(now)

    waiting = oldest_overdue_age(queryset, "escalation_deadline")
    claim_ids = list(queryset.order_by("escalation_deadline").values_list("pk", flat=True)[:limit])

    for claim_id in claim_ids:
        django_rq.get_queue("default").enqueue(auto_resolve_dispute, claim_id)

    logger.info(
        "dispute_resolution_sweep",
        enqueued=len(claim_ids),
        oldest_overdue_seconds=None if waiting is None else int(waiting.total_seconds()),
    )
    return len(claim_ids)


# ---------------------------------------------------------------------------
# Termination auto-confirmation
# ---------------------------------------------------------------------------


def overdue_terminations(now: dt.datetime | None = None):
    """Pending terminations whose confirmation window has elapsed.

    **Pending only.** A termination that escalated as
    `termination_defeats_review` is deliberately not here: letting silence
    confirm it would delete the counterparty's own review right by inaction,
    which is the exact outcome escalating it exists to prevent.
    """
    from .models import TerminationRequest

    return TerminationRequest.all_objects.filter(
        status=ClaimStatus.PENDING,
        confirmation_deadline__lte=now or timezone.now(),
    )


def auto_confirm_termination(request_id: int) -> None:
    """Confirm one termination the counterparty neither confirmed nor disputed.

    Same principle as a claim: silence is a signal, not a veto. A stay that
    ended is a fact, and an indefinite wait would let either side deny it by
    ignoring the request.
    """
    from .models import TerminationRequest
    from .services import confirm_termination

    request = TerminationRequest.all_objects.filter(pk=request_id).first()
    if request is None:
        logger.info("termination_auto_confirm_skipped", request_id=request_id, reason="deleted")
        return

    if request.status != ClaimStatus.PENDING:
        logger.info(
            "termination_auto_confirm_skipped",
            request_id=request_id,
            reason="no_longer_pending",
            status=request.status,
        )
        return

    confirm_termination(request)
    logger.info("termination_auto_confirmed", request_id=request_id)


def sweep_overdue_terminations(limit: int = 500, now: dt.datetime | None = None) -> int:
    """Enqueue auto-confirmation for every termination past its window.

    Scheduled hourly. `confirmation_deadline` is NOT NULL, so the ascending
    order has no NULLs to strand (docs/OPERATIONS.md).
    """
    now = now or timezone.now()
    queryset = overdue_terminations(now)

    waiting = oldest_overdue_age(queryset, "confirmation_deadline")
    request_ids = list(
        queryset.order_by("confirmation_deadline").values_list("pk", flat=True)[:limit]
    )

    for request_id in request_ids:
        django_rq.get_queue("default").enqueue(auto_confirm_termination, request_id)

    logger.info(
        "termination_confirmation_sweep",
        enqueued=len(request_ids),
        oldest_overdue_seconds=None if waiting is None else int(waiting.total_seconds()),
    )
    return len(request_ids)
