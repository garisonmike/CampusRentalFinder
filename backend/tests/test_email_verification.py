"""
Email-domain verification (ADR-003).

The automated path. Nobody reviews anything, so every guard has to be in the
code — there is no human downstream who might notice that the address looked
odd.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.conf import settings
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone

from accounts.verification import (
    EmailDomainNotAcceptedError,
    EmailVerificationToken,
    InvalidVerificationTokenError,
    VerificationRateLimitError,
    consume_email_token,
    domain_of,
    email_domain_is_accepted,
    issue_email_token,
)
from universities.constants import VerificationMethod, VerificationStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def kyu(university):
    university.student_email_domains = ["s.kyu.ac.ke", "kyu.ac.ke"]
    university.save(update_fields=["student_email_domains"])
    return university


@pytest.fixture
def profile(student_profile, kyu):
    student_profile.university = kyu
    student_profile.save(update_fields=["university"])
    return student_profile


# ---------------------------------------------------------------------------
# Domain matching
# ---------------------------------------------------------------------------


class TestDomainMatching:
    def test_an_issued_domain_is_accepted(self, kyu):
        assert email_domain_is_accepted(kyu, "brenda@s.kyu.ac.ke") is True

    def test_matching_is_case_insensitive(self, kyu):
        assert email_domain_is_accepted(kyu, "Brenda@S.KYU.AC.KE") is True

    def test_a_lookalike_domain_is_refused(self, kyu):
        """`endswith("kyu.ac.ke")` accepts this, and the domain is available to
        register for the price of a domain. This is the whole reason the
        comparison is an exact match on the full domain."""
        assert email_domain_is_accepted(kyu, "attacker@evil-kyu.ac.ke") is False

    def test_a_subdomain_of_an_attacker_domain_is_refused(self, kyu):
        """The suffix check fails in this direction too, if written the other
        way round: `"kyu.ac.ke" in candidate`."""
        assert email_domain_is_accepted(kyu, "attacker@kyu.ac.ke.attacker.com") is False

    def test_an_unlisted_subdomain_is_refused(self, kyu):
        """`alumni.kyu.ac.ke` is not `kyu.ac.ke`. If a school wants it, they
        configure it — the platform does not infer enrolment from hierarchy."""
        assert email_domain_is_accepted(kyu, "old@alumni.kyu.ac.ke") is False

    def test_a_different_university_is_refused(self, kyu):
        assert email_domain_is_accepted(kyu, "someone@uonbi.ac.ke") is False

    def test_an_address_with_no_domain_is_refused(self, kyu):
        assert email_domain_is_accepted(kyu, "brenda") is False

    def test_a_university_with_no_domains_accepts_nothing(self, university):
        university.student_email_domains = []
        university.save(update_fields=["student_email_domains"])

        assert email_domain_is_accepted(university, "anyone@anywhere.ac.ke") is False

    def test_a_blank_configured_domain_matches_nothing(self, university):
        """Otherwise a stray empty string in the array turns into a wildcard
        for addresses with no domain part."""
        university.student_email_domains = ["", "  "]
        university.save(update_fields=["student_email_domains"])

        assert email_domain_is_accepted(university, "brenda@") is False
        assert email_domain_is_accepted(university, "brenda") is False

    def test_the_domain_is_taken_from_the_last_at_sign(self):
        """A quoted local part may legally contain one, and splitting on the
        first would hand back something that is not a domain."""
        assert domain_of('"odd@name"@s.kyu.ac.ke') == "s.kyu.ac.ke"

    def test_issuing_refuses_an_unaccepted_domain(self, profile):
        with pytest.raises(EmailDomainNotAcceptedError):
            issue_email_token(profile, "attacker@evil-kyu.ac.ke")


# ---------------------------------------------------------------------------
# The token
# ---------------------------------------------------------------------------


class TestTokenIssue:
    def test_a_token_is_issued_with_its_raw_secret(self, profile):
        token, raw = issue_email_token(profile, "brenda@s.kyu.ac.ke")

        assert raw
        assert token.is_usable() is True

    def test_the_raw_secret_is_never_stored(self, profile):
        """A database read — a backup, a log, an over-broad admin query —
        must not hand anyone a working verification link."""
        _token, raw = issue_email_token(profile, "brenda@s.kyu.ac.ke")

        stored = EmailVerificationToken.objects.get()

        assert raw not in stored.token_hash
        assert len(stored.token_hash) == 64

    def test_two_tokens_never_collide(self, profile):
        _first, first_raw = issue_email_token(profile, "brenda@s.kyu.ac.ke")
        _second, second_raw = issue_email_token(profile, "brenda@s.kyu.ac.ke")

        assert first_raw != second_raw

    def test_the_expiry_comes_from_settings(self, profile):
        now = timezone.now()
        token, _raw = issue_email_token(profile, "brenda@s.kyu.ac.ke", now=now)

        expected = now + dt.timedelta(hours=settings.EMAIL_VERIFICATION_TOKEN_HOURS)
        assert token.expires_at == expected


class TestTokenConsumption:
    def test_consuming_verifies_the_student(self, profile):
        _token, raw = issue_email_token(profile, "brenda@s.kyu.ac.ke")

        verified = consume_email_token(raw)

        assert verified.verification_status == VerificationStatus.VERIFIED
        assert verified.verification_method == VerificationMethod.EMAIL_DOMAIN
        assert verified.verified_at is not None

    def test_no_reviewer_is_recorded(self, profile):
        """Nobody decided this; a domain did. A `verified_by` here would name
        someone who made no decision."""
        _token, raw = issue_email_token(profile, "brenda@s.kyu.ac.ke")

        assert consume_email_token(raw).verified_by is None

    def test_the_address_is_stored_for_audit(self, profile):
        _token, raw = issue_email_token(profile, "brenda@s.kyu.ac.ke")

        assert consume_email_token(raw).student_email == "brenda@s.kyu.ac.ke"

    def test_it_does_not_become_a_login_identifier(self, profile):
        """`student_email` is evidence, not credentials. Replacing User.email
        would silently move where the account's password reset goes."""
        original = profile.user.email
        _token, raw = issue_email_token(profile, "brenda@s.kyu.ac.ke")

        verified = consume_email_token(raw)
        verified.user.refresh_from_db()

        assert verified.user.email == original
        assert verified.user.email != verified.student_email

    def test_a_replayed_token_fails(self, profile):
        """Single use. A token that verifies twice is worth stealing twice."""
        _token, raw = issue_email_token(profile, "brenda@s.kyu.ac.ke")
        consume_email_token(raw)

        with pytest.raises(InvalidVerificationTokenError):
            consume_email_token(raw)

    def test_consumption_is_atomic(self, profile):
        """The consuming write is a conditional UPDATE with
        `consumed_at IS NULL` in the WHERE clause, so two simultaneous replays
        cannot both see an unconsumed row. Asserted by consuming the row out
        from under a second attempt.
        """
        _token, raw = issue_email_token(profile, "brenda@s.kyu.ac.ke")

        first = EmailVerificationToken.objects.filter(
            consumed_at__isnull=True, expires_at__gt=timezone.now()
        ).update(consumed_at=timezone.now())
        assert first == 1

        with pytest.raises(InvalidVerificationTokenError):
            consume_email_token(raw)

    def test_an_expired_token_fails(self, profile):
        token, raw = issue_email_token(profile, "brenda@s.kyu.ac.ke")
        EmailVerificationToken.objects.filter(pk=token.pk).update(
            expires_at=timezone.now() - dt.timedelta(seconds=1)
        )

        with pytest.raises(InvalidVerificationTokenError):
            consume_email_token(raw)

    def test_an_unknown_token_fails(self, profile):
        with pytest.raises(InvalidVerificationTokenError):
            consume_email_token("not-a-real-token")

    def test_all_three_failures_are_indistinguishable(self, profile):
        """Unknown, expired and already-used are different to us and identical
        to anyone probing. A distinct message for "already used" would confirm
        that an address exists and has been verified."""
        token, used_raw = issue_email_token(profile, "brenda@s.kyu.ac.ke")
        consume_email_token(used_raw)

        expired, expired_raw = issue_email_token(profile, "brenda@s.kyu.ac.ke")
        EmailVerificationToken.objects.filter(pk=expired.pk).update(
            expires_at=timezone.now() - dt.timedelta(seconds=1)
        )

        messages = set()
        for raw in (used_raw, expired_raw, "never-existed"):
            with pytest.raises(InvalidVerificationTokenError) as caught:
                consume_email_token(raw)
            messages.add(str(caught.value))

        assert len(messages) == 1, messages
        assert token.pk is not None

    def test_a_token_hash_is_unique(self, profile):
        token, _raw = issue_email_token(profile, "brenda@s.kyu.ac.ke")

        with pytest.raises(IntegrityError), transaction.atomic():
            EmailVerificationToken.objects.create(
                profile=profile,
                email="other@s.kyu.ac.ke",
                token_hash=token.token_hash,
                expires_at=timezone.now() + dt.timedelta(hours=1),
            )

    def test_deleting_the_profile_takes_its_tokens(self, profile):
        issue_email_token(profile, "brenda@s.kyu.ac.ke")

        profile.delete()

        assert EmailVerificationToken.objects.count() == 0


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimit:
    def test_a_user_may_request_up_to_the_cap(self, profile):
        with override_settings(
            EMAIL_VERIFICATION_MAX_PER_USER=3, EMAIL_VERIFICATION_MAX_PER_ADDRESS=10
        ):
            for index in range(3):
                issue_email_token(profile, f"brenda{index}@s.kyu.ac.ke")

    def test_the_per_user_cap_bites(self, profile):
        """Per address alone would let one account grind through a
        university's whole namespace."""
        with override_settings(
            EMAIL_VERIFICATION_MAX_PER_USER=2, EMAIL_VERIFICATION_MAX_PER_ADDRESS=10
        ):
            issue_email_token(profile, "a@s.kyu.ac.ke")
            issue_email_token(profile, "b@s.kyu.ac.ke")

            with pytest.raises(VerificationRateLimitError):
                issue_email_token(profile, "c@s.kyu.ac.ke")

    def test_the_per_address_cap_bites(self, profile, student_profile_factory, kyu):
        """Per user alone would let several accounts mail-bomb one address."""
        other = student_profile_factory(university=kyu)

        with override_settings(
            EMAIL_VERIFICATION_MAX_PER_USER=10, EMAIL_VERIFICATION_MAX_PER_ADDRESS=2
        ):
            issue_email_token(profile, "victim@s.kyu.ac.ke")
            issue_email_token(other, "victim@s.kyu.ac.ke")

            with pytest.raises(VerificationRateLimitError):
                issue_email_token(profile, "victim@s.kyu.ac.ke")

    def test_the_address_cap_is_case_insensitive(self, profile):
        """Otherwise the limit is bypassed by changing one letter's case."""
        with override_settings(
            EMAIL_VERIFICATION_MAX_PER_USER=10, EMAIL_VERIFICATION_MAX_PER_ADDRESS=2
        ):
            issue_email_token(profile, "victim@s.kyu.ac.ke")
            issue_email_token(profile, "VICTIM@s.kyu.ac.ke")

            with pytest.raises(VerificationRateLimitError):
                issue_email_token(profile, "Victim@S.Kyu.Ac.Ke")

    def test_the_window_rolls(self, profile):
        old, _raw = issue_email_token(profile, "brenda@s.kyu.ac.ke")
        EmailVerificationToken.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - dt.timedelta(days=30)
        )

        with override_settings(EMAIL_VERIFICATION_MAX_PER_USER=1):
            issue_email_token(profile, "brenda@s.kyu.ac.ke")

    def test_the_rate_limit_message_reveals_nothing(self, profile):
        """It must not say whether the address is known, registered, or
        already verified."""
        with (
            override_settings(EMAIL_VERIFICATION_MAX_PER_USER=0),
            pytest.raises(VerificationRateLimitError) as caught,
        ):
            issue_email_token(profile, "brenda@s.kyu.ac.ke")

        message = str(caught.value).lower()
        for leak in ("registered", "exists", "already", "verified", "unknown"):
            assert leak not in message
