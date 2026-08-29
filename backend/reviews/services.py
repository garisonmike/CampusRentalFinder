"""
Review service functions (ADR-004).

Two rules cannot be database constraints, and both live here as **one named
function each** that every path goes through — the serializer, the admin, a
management command, a shell session.

This is the single documented exception to "constraints in the database". It is
an exception because the rules compare against *today*: a tenancy that is
thirty days old right now was twenty-nine days old yesterday, and no
`CheckConstraint` can express a predicate whose truth changes while the row
sits still.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _

from accounts.gating import GatedAction
from tenancies.models import Tenancy

from .constants import DisputeAnnotation
from .models import Review, ReviewResponse


def _queue_aggregate_refresh(review: Review) -> None:
    """Recompute this review's three aggregates, after the transaction commits.

    ``on_commit``, because a job that reads the row before the write lands
    computes an average over data that does not exist yet. Never inline: a page
    load that recomputes an aggregate is a page load whose cost grows with the
    property's popularity.
    """
    from .jobs import enqueue_aggregate_refresh

    transaction.on_commit(lambda: enqueue_aggregate_refresh(review.pk))


def _assert_verification_permits(user, action) -> None:
    """The single call site for student-verification gating in this app.

    Delegates to `accounts.gating.can_perform`, which is the one place the
    policy is answered -- so the rule cannot drift between the API, the admin
    and a background job.
    """
    from accounts.gating import GateReason, can_perform

    decision = can_perform(user, action)
    if decision.allowed:
        return

    if decision.reason is GateReason.REJECTED:
        message = _("Your student verification was not accepted.")
    else:
        message = _("Your university asks students to verify before reviewing.")

    raise VerificationRequiredError({"verification": message})


class VerificationRequiredError(ValidationError):
    """This university gates this action on student verification."""


class TenancyNotReviewableError(ValidationError):
    """This stay does not yet support a review."""


class ReviewFrozenError(ValidationError):
    """The edit window on this review has closed."""


def stay_days(tenancy: Tenancy, *, today: dt.date | None = None) -> int:
    """How long the stay has lasted so far.

    **One definition, in `tenancies.services`.** This was a second copy of the
    same arithmetic and it carried the same defect: `end_date` was used
    whenever one was set, so a twelve-month lease reported 365 days on its
    first morning. 3889249 fixed the copy in `tenancies` and left this one --
    which is the shape `docs/OPERATIONS.md` collects, produced by the fix for
    an instance of itself.

    It mattered here in a different way. The eligibility gate reads the latch,
    so this figure only reached the error message -- but `ReviewSerializer`
    also renders it as `stay_months`, a public field, so a review card could
    say "stayed 12 months" about somebody three days into a lease.
    """
    from tenancies.services import effective_stay_days

    return effective_stay_days(tenancy, today=today)


def assert_tenancy_is_reviewable(tenancy: Tenancy, *, today: dt.date | None = None) -> None:
    """The minimum-stay rule, in one place.

    ``settings.REVIEW_MINIMUM_STAY_DAYS`` (30). A week in a room tells you
    about the viewing, not about living there; the water going off every third
    Thursday takes a month to notice.

    Tested directly at this boundary, not only through the API — an invariant
    that is only exercised through a serializer is an invariant the admin can
    walk past.
    """
    _assert_verification_permits(tenancy.tenant, GatedAction.WRITE_REVIEW)

    # Read the LATCH, not a live date computation. Once a stay has earned the
    # right to be reviewed, moving `end_date` backwards cannot take it away --
    # otherwise a landlord could delete a review right by terminating early,
    # which is correction_defeats_review at a different door (ADR-004 §2b).
    from tenancies.services import review_eligibility_date

    if review_eligibility_date(tenancy, today=today) is None:
        days = stay_days(tenancy, today=today)
        raise TenancyNotReviewableError(
            {
                "tenancy": _(
                    "A stay must reach %(minimum)d days before it can be reviewed. "
                    "This one is %(days)d."
                )
                % {"minimum": settings.REVIEW_MINIMUM_STAY_DAYS, "days": days}
            }
        )

    if hasattr(tenancy, "review"):
        raise TenancyNotReviewableError({"tenancy": _("This stay has already been reviewed.")})


@transaction.atomic
def create_review(tenancy: Tenancy, **fields) -> Review:
    """The single write path for a review.

    Every caller goes through the gate. There is deliberately no way to create
    a `Review` correctly that skips it.
    """
    assert_tenancy_is_reviewable(tenancy)
    review = Review.all_objects.create(tenancy=tenancy, **fields)
    _queue_aggregate_refresh(review)
    return review


@transaction.atomic
def update_review(review: Review, *, now: dt.datetime | None = None, **fields) -> Review:
    """Edit a review inside its window.

    The window exists so that a review means something at a point in time. A
    review that can be rewritten for ever can be rewritten under pressure, and
    the pressure would come from the party with more of it.
    """
    if not review.is_editable(now=now):
        raise ReviewFrozenError(
            {
                "editable_until": _(
                    "This review was frozen on %(when)s. Reviews are editable for %(days)d days."
                )
                % {
                    "when": review.editable_until.date().isoformat(),
                    "days": settings.REVIEW_EDIT_WINDOW_DAYS,
                }
            }
        )

    for field, value in fields.items():
        setattr(review, field, value)
    review.save()
    _queue_aggregate_refresh(review)
    return review


@transaction.atomic
def respond_to_review(review: Review, *, author, body: str) -> ReviewResponse:
    """The landlord's one reply.

    Not the caretaker's. A caretaker can confirm that somebody lived
    somewhere — a fact they are well placed to know — but speaking for the
    business in public is the owner's own act (ADR-003).
    """
    return ReviewResponse.all_objects.create(review=review, author=author, body=body)


# ---------------------------------------------------------------------------
# The dispute annotation (ADR-004 §3a)
# ---------------------------------------------------------------------------


def review_dispute_annotation(review: Review) -> str | None:
    """What, if anything, to tell a reader about a disputed stay.

    **Derived at read time, never stored.** The draft of this ADR put a
    `disputed_by_landlord` boolean on `Review`, which is permanent and has no
    path to removal even when the dispute is later shown to be spurious.
    Storing the fact (`claim.disputed_at`) and deriving the presentation means
    changing the policy is a function edit rather than a migration over live
    reviews.

    Returns ``None`` — no annotation at all — when:

    - the stay was never disputed;
    - the disputer withdrew the dispute, which the stored boolean could not
      have undone;
    - the landlord's disputes are overwhelmingly not upheld, behind a
      settings-gated hook that is **off by default**. A landlord who disputes
      everything would otherwise annotate every review of their properties,
      which is the veto returning through the annotation.

    The annotation itself is neutral: "the landlord disputed this stay". Never
    a discredit. The review is not greyed out, collapsed, excluded from the
    average or labelled unverified.
    """
    claim = review.tenancy.claim

    if claim is None or claim.disputed_at is None:
        return None

    if claim.dispute_withdrawn_at is not None:
        return None

    if settings.REVIEW_ANNOTATION_RESPECTS_DISPUTE_RECORD and _disputes_are_noise(claim):
        return None

    return DisputeAnnotation.DISPUTED


def _disputes_are_noise(claim) -> bool:
    """Whether this disputer's record makes their dispute uninformative.

    Off by default. Two guards, because a rate over a small sample says nothing:
    a minimum number of resolved disputes AND a low upheld rate.
    """
    from tenancies.constants import ClaimStatus
    from tenancies.models import TenancyClaim

    if claim.disputed_by_id is None:
        return False

    resolved = TenancyClaim.all_objects.filter(
        disputed_by_id=claim.disputed_by_id, resolved_at__isnull=False
    )
    total = resolved.count()

    if total < settings.REVIEW_ANNOTATION_MINIMUM_DISPUTE_SAMPLE:
        return False

    upheld = resolved.filter(status=ClaimStatus.WITHDRAWN).count()
    return (upheld / total) < settings.REVIEW_ANNOTATION_MINIMUM_UPHELD_RATE


def review_is_verified(review: Review) -> bool:
    """Whether to show the verified badge.

    Read at render time from the student's profile, never copied onto the
    review. When the university does not require verification to review (the
    default), an unverified student may still post and the badge is simply
    absent (ADR-003).
    """
    from universities.constants import VerificationStatus

    profile = getattr(review.tenancy.tenant, "student_profile", None)
    return profile is not None and profile.verification_status == VerificationStatus.VERIFIED


def dispute_annotations_for(reviews) -> dict[int, str | None]:
    """Annotations for a whole page of reviews, in a fixed number of queries.

    ADR-004 §2.1. The per-review function walks
    ``review -> tenancy -> claim`` and, when the disputer-record hook is on,
    counts that landlord's resolved disputes -- so rendering a page of fifty
    costs fifty walks plus fifty counts. The annotation is derived rather than
    stored precisely so the policy can change without a migration, and that
    only stays affordable if deriving it is batched.

    **Cost does not grow with the number of reviews.** Two queries when the
    hook is off, three when it is on, whether the page holds one review or a
    hundred. `tests/test_api_reviews.py` asserts exactly that, with the hook
    both enabled and disabled.
    """
    from tenancies.models import TenancyClaim

    reviews = list(reviews)
    if not reviews:
        return {}

    # One query: every claim behind this page, with only the fields the
    # decision reads.
    claims = {
        row["tenancies__review__pk"]: row
        for row in TenancyClaim.all_objects.filter(
            tenancies__review__pk__in=[review.pk for review in reviews]
        ).values(
            "tenancies__review__pk",
            "disputed_at",
            "dispute_withdrawn_at",
            "disputed_by_id",
        )
    }

    noisy: set[int] = set()
    if settings.REVIEW_ANNOTATION_RESPECTS_DISPUTE_RECORD:
        noisy = _noisy_disputers(
            {row["disputed_by_id"] for row in claims.values() if row["disputed_by_id"] is not None}
        )

    annotations: dict[int, str | None] = {}
    for review in reviews:
        row = claims.get(review.pk)
        if (
            row is None
            or row["disputed_at"] is None
            or row["dispute_withdrawn_at"] is not None
            or row["disputed_by_id"] in noisy
        ):
            annotations[review.pk] = None
        else:
            annotations[review.pk] = DisputeAnnotation.DISPUTED

    return annotations


def _noisy_disputers(disputer_ids: set[int]) -> set[int]:
    """Which of these disputers' records make their disputes uninformative.

    One grouped query for the whole set rather than two per landlord. Same two
    guards as the single-claim version: a minimum sample AND a low upheld rate,
    because a rate over three disputes says nothing.
    """
    from django.db.models import Count, Q

    from tenancies.constants import ClaimStatus
    from tenancies.models import TenancyClaim

    if not disputer_ids:
        return set()

    rows = (
        TenancyClaim.all_objects.filter(disputed_by_id__in=disputer_ids, resolved_at__isnull=False)
        .values("disputed_by_id")
        .annotate(
            total=Count("pk"),
            upheld=Count("pk", filter=Q(status=ClaimStatus.WITHDRAWN)),
        )
    )

    return {
        row["disputed_by_id"]
        for row in rows
        if row["total"] >= settings.REVIEW_ANNOTATION_MINIMUM_DISPUTE_SAMPLE
        and (row["upheld"] / row["total"]) < settings.REVIEW_ANNOTATION_MINIMUM_UPHELD_RATE
    }
