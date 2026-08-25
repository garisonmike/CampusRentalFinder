"""
Email-domain verification (ADR-003).

The automated path: a student proves enrolment by receiving mail at an address
their university issued. No human in the loop, so no reviewer is recorded — a
``verified_by`` on this path would name someone who did not make a decision.

Three things here are load-bearing and each has a failure mode worth naming:

**Exact domain match, never a suffix.** ``endswith("kyu.ac.ke")`` accepts
``evil-kyu.ac.ke``, which anyone can register. The comparison splits on the
final ``@`` and compares the whole domain, case-folded.

**Single use, consumed atomically.** A token that verifies twice is a token
worth stealing twice; the consuming update is conditional on the row still
being unconsumed, so a replay loses the race rather than winning it.

**Enumeration-safe responses.** "That address is already registered" tells an
attacker which students exist at a university. The endpoint's answer does not
depend on whether the address is known.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from universities.constants import VerificationMethod, VerificationStatus
from universities.models import University
from universities.services import assert_verification_method_is_enabled

from .models import StudentProfile


class EmailDomainNotAcceptedError(ValidationError):
    """The address does not belong to a domain this university issued."""


class VerificationRateLimitError(ValidationError):
    """Too many verification attempts."""


class InvalidVerificationTokenError(ValidationError):
    """The token is unknown, expired, or already used."""


# ---------------------------------------------------------------------------
# Domain matching
# ---------------------------------------------------------------------------


def domain_of(email: str) -> str:
    """The domain part, case-folded.

    Splits on the **last** ``@``: the local part of an address may legally
    contain one inside quotes, and taking the first would hand back something
    that is not a domain at all.
    """
    _local, _, domain = email.strip().rpartition("@")
    return domain.casefold()


def email_domain_is_accepted(university: University, email: str) -> bool:
    """Whether this address proves enrolment here.

    **Exact match on the full domain.** A suffix check — ``endswith`` or ``in``
    — accepts ``evil-kyu.ac.ke`` for a university that configured ``kyu.ac.ke``,
    and that domain is available to register for the price of a domain. It also
    accepts ``kyu.ac.ke.attacker.com`` in the other direction.
    """
    candidate = domain_of(email)
    if not candidate:
        return False

    return any(
        candidate == configured.strip().casefold()
        for configured in university.student_email_domains
        if configured.strip()
    )


def assert_email_domain_is_accepted(university: University, email: str) -> None:
    """Gate, in one place, so the admin and any future path go through it."""
    if not email_domain_is_accepted(university, email):
        raise EmailDomainNotAcceptedError(
            {
                "student_email": _("%(university)s does not issue addresses at that domain.")
                % {"university": university.display_name or university.name}
            }
        )


# ---------------------------------------------------------------------------
# The token
# ---------------------------------------------------------------------------


def _hash_token(raw: str) -> str:
    """What gets stored.

    The raw token goes in an email and nowhere else. Storing only its hash
    means a database read — a backup, a log, an over-broad admin query — does
    not hand anyone a working verification link.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class EmailVerificationToken(models.Model):
    """A single-use, time-limited proof of address ownership.

    Not tenant-scoped: it is reached only by its own secret, never listed, and
    the profile it points at carries the tenant. A scoped manager here would be
    ceremony around a table nobody queries by university.
    """

    profile = models.ForeignKey(
        StudentProfile, on_delete=models.CASCADE, related_name="verification_tokens"
    )
    #: The address being proved. Stored for audit; it is NOT a login identifier
    #: and does not replace User.email.
    email = models.EmailField(_("student email"))

    token_hash = models.CharField(_("token hash"), max_length=64, unique=True)
    expires_at = models.DateTimeField(_("expires at"))
    consumed_at = models.DateTimeField(_("consumed at"), null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Email verification token")
        verbose_name_plural = _("Email verification tokens")
        ordering = ["-created_at"]
        indexes = [
            # The rate limit reads per profile; the sweep reads per address.
            models.Index(fields=["profile", "-created_at"], name="emailtok_profile_idx"),
            models.Index(fields=["email", "-created_at"], name="emailtok_email_idx"),
        ]

    def __str__(self) -> str:
        return f"verification token for {self.email}"

    def is_usable(self, *, now: dt.datetime | None = None) -> bool:
        now = now or timezone.now()
        return self.consumed_at is None and self.expires_at > now


def _assert_within_rate_limit(
    profile: StudentProfile, email: str, *, now: dt.datetime | None = None
) -> None:
    """Per user **and** per address.

    Per user alone lets one attacker mail-bomb many addresses; per address
    alone lets one account grind through a university's whole namespace. Both
    windows are needed and they are independent.
    """
    now = now or timezone.now()
    since = now - dt.timedelta(hours=settings.EMAIL_VERIFICATION_RATE_WINDOW_HOURS)

    by_user = EmailVerificationToken.objects.filter(profile=profile, created_at__gte=since).count()
    if by_user >= settings.EMAIL_VERIFICATION_MAX_PER_USER:
        raise VerificationRateLimitError(
            {"detail": _("Too many verification requests. Try again later.")}
        )

    by_address = EmailVerificationToken.objects.filter(
        email__iexact=email, created_at__gte=since
    ).count()
    if by_address >= settings.EMAIL_VERIFICATION_MAX_PER_ADDRESS:
        raise VerificationRateLimitError(
            {"detail": _("Too many verification requests. Try again later.")}
        )


@transaction.atomic
def issue_email_token(
    profile: StudentProfile, email: str, *, now: dt.datetime | None = None
) -> tuple[EmailVerificationToken, str]:
    """Create a token and return it with its **raw** secret.

    The raw value is returned once, for the caller to mail, and is never
    recoverable afterwards. Only its hash is stored.
    """
    now = now or timezone.now()
    email = email.strip()

    assert_verification_method_is_enabled(profile.university, VerificationMethod.EMAIL_DOMAIN)
    assert_email_domain_is_accepted(profile.university, email)
    _assert_within_rate_limit(profile, email, now=now)

    raw = secrets.token_urlsafe(48)
    token = EmailVerificationToken.objects.create(
        profile=profile,
        email=email,
        token_hash=_hash_token(raw),
        expires_at=now + dt.timedelta(hours=settings.EMAIL_VERIFICATION_TOKEN_HOURS),
    )
    return token, raw


@transaction.atomic
def consume_email_token(raw: str, *, now: dt.datetime | None = None) -> StudentProfile:
    """Verify a student from their token, exactly once.

    The consuming write is a **conditional UPDATE** — ``consumed_at IS NULL``
    is part of the WHERE clause — so two simultaneous replays cannot both see
    an unconsumed row and both proceed. A `select_for_update` would also work;
    this needs no lock and no retry.
    """
    now = now or timezone.now()

    updated = EmailVerificationToken.objects.filter(
        token_hash=_hash_token(raw),
        consumed_at__isnull=True,
        expires_at__gt=now,
    ).update(consumed_at=now)

    if not updated:
        # Deliberately one message for unknown, expired and already-used. The
        # three are different to us and identical to anyone probing.
        raise InvalidVerificationTokenError(
            {"token": _("That verification link is invalid or has expired.")}
        )

    token = EmailVerificationToken.objects.get(token_hash=_hash_token(raw))
    profile = token.profile

    profile.student_email = token.email
    profile.verification_status = VerificationStatus.VERIFIED
    profile.verification_method = VerificationMethod.EMAIL_DOMAIN
    profile.verified_at = now
    # No verified_by. Nobody decided this; a domain did.
    profile.verified_by = None
    profile.rejection_reason = ""
    profile.save(
        update_fields=[
            "student_email",
            "verification_status",
            "verification_method",
            "verified_at",
            "verified_by",
            "rejection_reason",
            "updated_at",
        ]
    )
    return profile
