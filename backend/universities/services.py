"""
Rules the database cannot express.

Everything that *can* be a constraint is one. What lands here is the small set
of rules that span tables or reference "now", which PostgreSQL check
constraints cannot do. Each is a single named function that every write path
calls, so there is one place to read and one place to test.
"""

from __future__ import annotations

import datetime as dt

from django.apps import apps
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .constants import SignupPolicy, VerificationStatus
from .models import University


class UnsafeSignupPolicyError(ValidationError):
    """Setting this policy would lock the university's own students out."""


def assert_signup_policy_is_safe(university: University, policy: str) -> None:
    """Refuse a signup policy that would exclude every student.

    ADR-003. ``signup_policy`` may not be set to ``verification_required``
    unless the university already has at least one verified student.

    The rule this replaced checked *configuration* — are any verification
    methods enabled — and so passed in the exact case that matters: a school
    turns on email-domain verification, sets the flag, and has not yet issued
    addresses to its first-years. Methods are enabled, the check passes, and an
    entire intake cannot sign up.

    Checking an outcome instead asks the question that matters: has
    verification ever actually worked here, for anybody? A school with zero
    verified students cannot switch on a policy that requires one.

    This cannot be a ``CheckConstraint``: it spans ``University`` and
    ``StudentProfile``. It is therefore enforced here, called from the
    serializer and from every other write path, and covered by a named test for
    exactly the failure case above.
    """
    if policy != SignupPolicy.REQUIRED:
        return

    if not _has_any_verified_student(university):
        raise UnsafeSignupPolicyError(
            _(
                "%(name)s has no verified students yet, so requiring verification "
                "at signup would lock out everyone including its own intake. "
                "Verify at least one student first, or use "
                "'verification_encouraged'."
            )
            % {"name": university.name},
            code="no_verified_students",
        )


def _has_any_verified_student(university: University) -> bool:
    """Whether verification has ever succeeded for this university.

    Resolved through the app registry rather than imported: ``accounts``
    imports ``universities`` for the StudentProfile foreign key, so a module
    -level import here would be a cycle. ``LookupError`` means the accounts app
    has not shipped its profile model yet, which is the same answer as "no
    verified students".
    """
    try:
        student_profile = apps.get_model("accounts", "StudentProfile")
    except LookupError:  # pragma: no cover - only before accounts ships
        return False

    return (
        student_profile.objects.for_tenant(university)
        .filter(verification_status=VerificationStatus.VERIFIED)
        .exists()
    )


def signup_verification_is_enforced(university: University, *, on: dt.date | None = None) -> bool:
    """Whether signup gating actually bites for this university today.

    ``verification_enforced_from`` lets a school announce a change before it
    takes effect. Computed in one place: inlining the date comparison at call
    sites is how "is signup gated?" starts having different answers in
    different parts of the codebase.
    """
    if university.signup_policy != SignupPolicy.REQUIRED:
        return False

    if university.verification_enforced_from is None:
        return True

    today = on or timezone.localdate()
    return today >= university.verification_enforced_from


class VerificationMethodNotOfferedError(ValidationError):
    """This university has not enabled that verification method."""


def verification_method_is_enabled(university, method: str) -> bool:
    """Whether this university offers this way of proving enrolment (ADR-003).

    ``verification_methods_enabled`` existed as configuration from phase 2 and
    was read by nothing until both paths were built. A school that had turned
    email-domain verification off could still have students verify that way,
    and one with no document reviewers could still receive identity documents
    into a queue nobody would ever work -- which, given the retention clock
    starts at upload, meant collecting national IDs purely to delete them 30
    days later.
    """
    return method in (university.verification_methods_enabled or [])


def assert_verification_method_is_enabled(university, method: str) -> None:
    """Gate, in one place, so both paths and the admin go through it."""
    if not verification_method_is_enabled(university, method):
        raise VerificationMethodNotOfferedError(
            {
                "method": _("%(university)s does not offer this way of verifying.")
                % {"university": university.display_name or university.name}
            }
        )
