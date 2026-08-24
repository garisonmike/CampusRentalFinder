"""
Whether a student's unverified status blocks an action (ADR-003).

Register-then-verify, with a grace period. Verify-then-register is
chicken-and-egg for the email-domain path — there is no account to attach a
confirmed address to — and impossible for manual ID review, because there is
nowhere to upload a document and nobody to attach the decision to.

So accounts always create, and gating happens afterwards. The rules, in the
order they matter:

1. **Read access is never gated.** Search, browsing, saved properties and
   inquiries work regardless of verification status, grace period, or policy.
2. **Only the actions a school has explicitly gated are ever blocked.**
3. **The grace period softens the wait, it does not create access.** Expiry
   blocks the gated actions and nothing else. Never delete, never lock out,
   never log out.
4. **Policy changes apply to new signups only.** An existing unverified student
   keeps everything they had.

The verification *mechanisms* land in phase 6. This module is the policy, and it
is complete: phase 6 adds ways to become verified, not new rules about what
being unverified means.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum

from django.utils import timezone

from universities.constants import SignupPolicy, VerificationStatus
from universities.models import University
from universities.services import signup_verification_is_enforced


class GatedAction(Enum):
    """Actions a university may gate on student verification.

    Deliberately a short list, and deliberately not including anything that
    would amount to read access. A student who cannot search is a student who
    cannot use the platform, which is not a verification policy — it is an
    outage (ADR-003).
    """

    WRITE_REVIEW = "write_review"
    CLAIM_TENANCY = "claim_tenancy"
    SUBMIT_APPLICATION = "submit_application"


#: Actions that are never gated, whatever a university configures.
#:
#: Listed explicitly so that adding a gate for one of them is a deliberate act
#: someone has to argue for, rather than an omission.
NEVER_GATED = frozenset(
    {
        "search",
        "browse_listings",
        "view_property",
        "save_property",
        "send_inquiry",
        "read_reviews",
        "edit_own_profile",
    }
)


class GateReason(Enum):
    """Why an action was allowed or blocked. Surfaced to the client verbatim."""

    NOT_GATED = "not_gated"
    NO_PROFILE = "no_profile"
    VERIFIED = "verified"
    WITHIN_GRACE = "within_grace"
    GRACE_EXPIRED = "grace_expired"
    REJECTED = "rejected"


@dataclass(frozen=True)
class GateDecision:
    """The answer, with the reason attached.

    A bare boolean would leave the client guessing why, and "you must verify"
    versus "your grace period has ended" are different messages to a student.
    """

    allowed: bool
    reason: GateReason
    grace_period_ends_at: dt.datetime | None = None

    def __bool__(self) -> bool:
        return self.allowed


def university_gates(university: University, action: GatedAction) -> bool:
    """Whether this university gates this action on verification."""
    if action is GatedAction.WRITE_REVIEW:
        return university.verification_required_to_review
    # CLAIM_TENANCY and SUBMIT_APPLICATION follow the signup policy: a school
    # that requires verification to join requires it to transact.
    return signup_verification_is_enforced(university)


def can_perform(user, action: GatedAction, *, now: dt.datetime | None = None) -> GateDecision:
    """Whether ``user`` may perform ``action``.

    The single place this question is answered. Every gated code path calls it,
    so the policy cannot drift between the API, the admin and a future job.
    """
    profile = getattr(user, "student_profile", None) if user.is_authenticated else None

    if profile is None:
        # Not a student here. Student verification has nothing to say about
        # them; other permission classes decide.
        return GateDecision(allowed=True, reason=GateReason.NO_PROFILE)

    if not university_gates(profile.university, action):
        return GateDecision(allowed=True, reason=GateReason.NOT_GATED)

    if profile.verification_status == VerificationStatus.VERIFIED:
        return GateDecision(allowed=True, reason=GateReason.VERIFIED)

    if profile.verification_status == VerificationStatus.REJECTED:
        return GateDecision(allowed=False, reason=GateReason.REJECTED)

    ends_at = profile.grace_period_ends_at
    if ends_at is not None and (now or timezone.now()) < ends_at:
        # Verification waits on the registry or on a human reviewer, neither of
        # which the student controls. Blocking them meanwhile would punish them
        # for the school's queue.
        return GateDecision(
            allowed=True, reason=GateReason.WITHIN_GRACE, grace_period_ends_at=ends_at
        )

    return GateDecision(
        allowed=False, reason=GateReason.GRACE_EXPIRED, grace_period_ends_at=ends_at
    )


def grace_period_end_for(
    university: University, *, now: dt.datetime | None = None
) -> dt.datetime | None:
    """When a student signing up now would stop being covered by grace.

    ``None`` when the university gates nothing at signup, so there is no grace
    period to run out — the field stays null rather than recording a deadline
    that means nothing.
    """
    if not signup_verification_is_enforced(university):
        return None
    return (now or timezone.now()) + dt.timedelta(days=university.verification_grace_period_days)


def initial_verification_status(university: University) -> str:
    """The status a new student profile starts in.

    ``pending`` where the school gates on verification, so the profile records
    that something is expected of it. ``unverified`` otherwise — nothing is
    outstanding, and marking it pending would imply a queue that does not exist.
    """
    if university.signup_policy == SignupPolicy.OPEN:
        return VerificationStatus.UNVERIFIED
    if signup_verification_is_enforced(university):
        return VerificationStatus.PENDING
    return VerificationStatus.UNVERIFIED
