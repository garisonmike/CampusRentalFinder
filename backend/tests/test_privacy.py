"""
Subject access and erasure (ADR-008, Data Protection Act 2019).

The rule that shapes everything here:

> Erasure **anonymises**; it does not cascade. If it deleted reviews, anyone
> could remove criticism of a property by deleting the account that wrote it.
> The right to be forgotten is not a right to unpublish what you said about
> someone else.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from accounts.privacy import (
    ERASED_EMAIL_DOMAIN,
    TOMBSTONE_NAME,
    ActiveTenanciesError,
    AlreadyErasedError,
    display_name_for,
    erase_landlord_data,
    erase_personal_data,
    export_personal_data,
    is_tombstoned,
    landlord_erasure_blockers,
)
from reviews.models import Review
from reviews.recompute import recompute_unit
from reviews.services import create_review
from tenancies.models import Tenancy, TenancyClaim
from tenancies.services import confirm_claim, create_claim

pytestmark = pytest.mark.django_db


MINIMUM_STAY = 90


@pytest.fixture
def a_student_with_history(student_profile, tenancy_factory, unit_factory, landlord):
    """A student with a stay, a review and a claim: the realistic subject."""
    user = student_profile.user
    end = dt.date.today() - dt.timedelta(days=1)
    unit = unit_factory()

    tenancy = tenancy_factory(
        unit=unit, tenant=user, start_date=end - dt.timedelta(days=MINIMUM_STAY), end_date=end
    )
    review = create_review(tenancy, rating=2, comment="The water went off every Thursday.")

    claim = create_claim(
        unit=unit_factory(),
        claimant=user,
        start_date=dt.date(2023, 1, 1),
        end_date=dt.date(2023, 6, 30),
        monthly_rent_kes=Decimal("9000.00"),
        is_retrospective=True,
    )
    confirm_claim(claim, source="landlord", confirmed_by=landlord)

    return user, student_profile, tenancy, review


# ---------------------------------------------------------------------------
# Subject access
# ---------------------------------------------------------------------------


class TestExport:
    def test_it_covers_everything_the_dpa_asks_for(self, a_student_with_history):
        user, _profile, _tenancy, _review = a_student_with_history

        export = export_personal_data(user)

        for section in (
            "account",
            "student_profile",
            "verification_requests",
            "applications",
            "tenancy_claims",
            "tenancies",
            "reviews",
        ):
            assert section in export

    def test_the_account_section_holds_the_identifiers(self, a_student_with_history):
        user, _profile, _tenancy, _review = a_student_with_history

        account = export_personal_data(user)["account"]

        assert account["email"] == user.email
        assert account["first_name"] == user.first_name

    def test_reviews_come_back_with_their_text(self, a_student_with_history):
        user, _profile, _tenancy, _review = a_student_with_history

        reviews = export_personal_data(user)["reviews"]

        assert len(reviews) == 1
        assert "Thursday" in reviews[0]["comment"]

    def test_tenancies_and_claims_come_back(self, a_student_with_history):
        user, _profile, _tenancy, _review = a_student_with_history

        export = export_personal_data(user)

        assert len(export["tenancies"]) == 2  # the factory stay, and the claim
        assert len(export["tenancy_claims"]) == 1

    def test_verification_outcomes_come_back_without_the_image(self, student_profile):
        """The decision is the record; the document was only ever how it was
        reached. Returning the image would re-expose an identity document to
        whatever channel the export travels over."""
        import io

        from PIL import Image

        from accounts.documents import submit_verification_document

        buffer = io.BytesIO()
        Image.new("RGB", (16, 16)).save(buffer, format="JPEG")
        submit_verification_document(student_profile, buffer.getvalue())

        entries = export_personal_data(student_profile.user)["verification_requests"]

        assert len(entries) == 1
        assert "status" in entries[0]
        assert "storage_key" not in entries[0]
        assert "document" not in entries[0]

    def test_it_does_not_name_the_reviewer(self, student_profile, university_staff):
        """Naming the member of staff who refused a student's ID, in a document
        handed to that student, is how a policy decision becomes a personal
        one."""
        import io

        from PIL import Image

        from accounts.documents import reject_verification, submit_verification_document

        buffer = io.BytesIO()
        Image.new("RGB", (16, 16)).save(buffer, format="JPEG")
        request = submit_verification_document(student_profile, buffer.getvalue())
        reject_verification(request, reviewer=university_staff, reason="Unreadable.")

        entries = export_personal_data(student_profile.user)["verification_requests"]

        assert entries[0]["decision_reason"] == "Unreadable."
        assert "reviewed_by" not in entries[0]
        assert university_staff.get_full_name() not in str(entries)

    def test_it_does_not_leak_the_document_access_log(self, student_profile, university_staff):
        """That log is an audit trail about our staff, not the student's own
        data. A generic relation walk would have handed it over."""
        import io

        from PIL import Image

        from accounts.documents import signed_document_url, submit_verification_document

        buffer = io.BytesIO()
        Image.new("RGB", (16, 16)).save(buffer, format="JPEG")
        request = submit_verification_document(student_profile, buffer.getvalue())
        signed_document_url(request.document, reviewer=university_staff)

        export = str(export_personal_data(student_profile.user))

        assert university_staff.get_full_name() not in export

    def test_a_user_with_no_history_exports_cleanly(self, landlord):
        export = export_personal_data(landlord)

        assert export["student_profile"] is None
        assert export["reviews"] == []


# ---------------------------------------------------------------------------
# Erasure
# ---------------------------------------------------------------------------


class TestErasureAnonymises:
    def test_the_account_no_longer_names_a_person(self, a_student_with_history):
        user, _profile, _tenancy, _review = a_student_with_history
        original_email = user.email
        original_name = user.get_full_name()

        erase_personal_data(user)
        user.refresh_from_db()

        assert user.email != original_email
        assert original_name not in user.get_full_name()
        assert user.phone_number == ""

    def test_the_tombstone_email_is_unroutable(self, a_student_with_history):
        """RFC 2606 reserves `.invalid`, so a stray message cannot reach a real
        person."""
        user, _profile, _tenancy, _review = a_student_with_history

        erase_personal_data(user)
        user.refresh_from_db()

        assert user.email.endswith(f"@{ERASED_EMAIL_DOMAIN}")

    def test_two_erasures_do_not_collide(self, a_student_with_history, student_profile_factory):
        """`User.email` is the login identifier and is UNIQUE; a shared
        placeholder would make the second erasure fail."""
        user, _profile, _tenancy, _review = a_student_with_history
        other = student_profile_factory().user

        erase_personal_data(user)
        erase_personal_data(other)
        user.refresh_from_db()
        other.refresh_from_db()

        assert user.email != other.email

    def test_the_account_is_deactivated_not_deleted(self, a_student_with_history):
        """Deleting the row would cascade into the profile and be refused by
        PROTECT on Tenancy anyway. Keeping it is what lets the tombstone
        exist."""
        user, _profile, _tenancy, _review = a_student_with_history

        erase_personal_data(user)
        user.refresh_from_db()

        assert user.is_active is False
        assert user.pk is not None

    def test_the_password_no_longer_works(self, a_student_with_history):
        user, _profile, _tenancy, _review = a_student_with_history

        erase_personal_data(user)
        user.refresh_from_db()

        assert user.has_usable_password() is False

    def test_the_profile_is_scrubbed(self, a_student_with_history):
        user, profile, _tenancy, _review = a_student_with_history
        profile.course = "BSc Computer Science"
        profile.year_of_study = 3
        profile.student_email = "brenda@s.kyu.ac.ke"
        profile.save()

        erase_personal_data(user)
        profile.refresh_from_db()

        assert profile.course == ""
        assert profile.year_of_study is None
        assert profile.student_email == ""

    def test_erasing_twice_is_refused(self, a_student_with_history):
        user, _profile, _tenancy, _review = a_student_with_history
        erase_personal_data(user)
        user.refresh_from_db()

        with pytest.raises(AlreadyErasedError):
            erase_personal_data(user)

    def test_it_is_marked_and_queryable(self, a_student_with_history):
        user, _profile, _tenancy, _review = a_student_with_history

        assert is_tombstoned(user) is False

        erase_personal_data(user)
        user.refresh_from_db()

        assert is_tombstoned(user) is True
        assert user.erased_at is not None


class TestErasureDoesNotCascade:
    """The property the whole design turns on.

    If erasure deleted reviews, a landlord who wanted a bad review gone would
    need one cooperating student and one support ticket.
    """

    def test_the_review_survives_with_its_text(self, a_student_with_history):
        user, _profile, _tenancy, review = a_student_with_history

        erase_personal_data(user)
        review.refresh_from_db()

        assert Review.all_objects.filter(pk=review.pk).exists()
        assert "Thursday" in review.comment
        assert review.rating == 2

    def test_the_review_stays_published(self, a_student_with_history):
        """Not hidden, not unpublished. Erasure removes the link to a person,
        not the content."""
        user, _profile, _tenancy, review = a_student_with_history

        erase_personal_data(user)
        review.refresh_from_db()

        assert review.is_published is True

    def test_the_author_becomes_a_tombstone(self, a_student_with_history):
        user, _profile, _tenancy, review = a_student_with_history
        real_name = user.get_full_name()

        erase_personal_data(user)
        review.refresh_from_db()

        assert display_name_for(review.reviewer()) == TOMBSTONE_NAME
        assert real_name not in display_name_for(review.reviewer())

    def test_the_tenancy_survives(self, a_student_with_history):
        """It is the evidence the review rests on, and it is also the
        landlord's record of who lived in their property."""
        user, _profile, tenancy, _review = a_student_with_history

        erase_personal_data(user)

        assert Tenancy.all_objects.filter(pk=tenancy.pk).exists()

    def test_claims_survive(self, a_student_with_history):
        user, _profile, _tenancy, _review = a_student_with_history

        erase_personal_data(user)

        assert TenancyClaim.all_objects.filter(claimant=user).count() == 1

    def test_the_rating_is_unchanged(self, a_student_with_history):
        """The review survives, so the number it feeds does too. An erasure
        that silently moved a property's rating would be a way to launder a
        score."""
        user, _profile, tenancy, _review = a_student_with_history
        before = recompute_unit(tenancy.unit_id).average_rating

        erase_personal_data(user)
        after = recompute_unit(tenancy.unit_id).average_rating

        assert after == before

    def test_the_access_log_survives(self, student_profile, university_staff):
        """An audit trail about our staff's actions. It is not the student's
        to erase, and a regulator asking "who looked at this" months later
        needs an answer."""
        import io

        from PIL import Image

        from accounts.documents import (
            DocumentAccessLog,
            signed_document_url,
            submit_verification_document,
        )

        buffer = io.BytesIO()
        Image.new("RGB", (16, 16)).save(buffer, format="JPEG")
        request = submit_verification_document(student_profile, buffer.getvalue())
        signed_document_url(request.document, reviewer=university_staff)

        erase_personal_data(student_profile.user)

        assert DocumentAccessLog.objects.count() == 1

    def test_the_report_says_what_was_kept(self, a_student_with_history):
        """The subject is told what survives and why, not just that something
        happened."""
        user, _profile, _tenancy, _review = a_student_with_history

        report = erase_personal_data(user)

        assert report.reviews_tombstoned == 1
        assert report.tenancies_retained == 2
        assert report.claims_retained == 1
        assert any("tombstone" in line.lower() for line in report.retained)
        assert any("landlord" in line.lower() for line in report.retained)


class TestDisplayName:
    def test_a_live_account_shows_its_name(self, tenant):
        assert display_name_for(tenant) == tenant.get_full_name()

    def test_an_erased_account_shows_the_tombstone(self, a_student_with_history):
        user, _profile, _tenancy, _review = a_student_with_history

        erase_personal_data(user)
        user.refresh_from_db()

        assert display_name_for(user) == TOMBSTONE_NAME

    def test_the_tombstone_is_not_a_name(self):
        """It must read as a category, not as a person somebody could be
        confused with."""
        assert TOMBSTONE_NAME == "Former student"


class TestExportAfterErasure:
    def test_it_reports_the_erasure(self, a_student_with_history):
        """An erased subject who asks again is told the account was erased,
        rather than handed a plausible-looking empty record."""
        user, _profile, _tenancy, _review = a_student_with_history
        erased_at = erase_personal_data(user).erased_at
        user.refresh_from_db()

        export = export_personal_data(user)

        assert export["account"]["erased_at"] == erased_at
        assert export["account"]["first_name"] == TOMBSTONE_NAME

    def test_the_reviews_are_still_listed(self, a_student_with_history):
        """Honest: they still exist, and the subject should be told so rather
        than left believing they were deleted."""
        user, _profile, _tenancy, _review = a_student_with_history
        erase_personal_data(user)
        user.refresh_from_db()

        assert len(export_personal_data(user)["reviews"]) == 1


# ---------------------------------------------------------------------------
# The access log after erasure
# ---------------------------------------------------------------------------


def reachable_from(instance, *, max_depth: int = 6) -> list[str]:
    """Every object reachable from ``instance`` by following forward FKs.

    Walks the relations rather than asserting on the columns anyone happens to
    remember. A field added later that reintroduces a path to a person is
    exactly the regression this must catch, and a hand-written column list
    cannot -- it would still pass, because it would not know to look.

    Returns "app.Model#pk" strings so a failure names the path it found.
    """
    seen: set[tuple[str, int]] = set()
    found: list[str] = []
    frontier = [(instance, 0)]

    while frontier:
        obj, depth = frontier.pop()
        if obj is None or depth > max_depth:
            continue

        key = (obj._meta.label, obj.pk)
        if key in seen:
            continue
        seen.add(key)
        found.append(f"{obj._meta.label}#{obj.pk}")

        for field in obj._meta.get_fields():
            if not field.is_relation or not (field.many_to_one or field.one_to_one):
                continue
            if not hasattr(field, "attname"):
                continue
            try:
                related = getattr(obj, field.name, None)
            except Exception:  # noqa: S112 - a broken relation is not a leak
                continue
            if related is not None and hasattr(related, "_meta"):
                frontier.append((related, depth + 1))

    return found


class TestAccessLogIsPseudonymisedAtErasure:
    """ADR-008 §2.1. Neither deleting the audit trail nor keeping the link.

    The log records what **our staff** did. Deleting it destroys evidence a
    regulator is entitled to and the subject has no right to remove. Keeping
    the foreign keys leaves the subject reachable from it. So the row survives
    and every link to the person is cut.
    """

    def logged_access(self, student_profile, university_staff):
        import io

        from PIL import Image

        from accounts.documents import (
            DocumentAccessLog,
            signed_document_url,
            submit_verification_document,
        )

        buffer = io.BytesIO()
        Image.new("RGB", (16, 16)).save(buffer, format="JPEG")
        request = submit_verification_document(student_profile, buffer.getvalue())
        signed_document_url(request.document, reviewer=university_staff)
        entry = DocumentAccessLog.objects.filter(verification_request=request).get()
        return entry, request

    def test_the_log_carries_the_cases_token(self, student_profile, university_staff):
        entry, request = self.logged_access(student_profile, university_staff)

        assert entry.subject_token == request.subject_token
        assert len(entry.subject_token) >= 32

    def test_the_token_is_random_not_derived(self, student_profile, university_staff):
        """A hash of a user id is reversible by enumerating the users, which is
        obfuscation rather than pseudonymisation.

        Tested as **non-reproducibility**, not by looking for the pk inside the
        string: a 3-digit pk turns up in random hex by chance often enough that
        such an assertion is a coin flip. What matters is that nobody holding
        the identifiers can regenerate the token.
        """
        import hashlib

        entry, _request = self.logged_access(student_profile, university_staff)
        token = entry.subject_token

        identifiers = (
            str(student_profile.pk),
            str(student_profile.user_id),
            student_profile.user.email,
        )
        for identifier in identifiers:
            for candidate in (
                identifier,
                hashlib.sha256(identifier.encode()).hexdigest(),
                hashlib.md5(identifier.encode()).hexdigest(),  # noqa: S324
            ):
                assert token != candidate

    def test_the_same_student_gets_a_new_token_per_case(self, student_profile, university_staff):
        """The property that rules out derivation from anything about the
        student: a derived token would repeat for the same student."""
        from accounts.documents import reject_verification

        first_entry, first_request = self.logged_access(student_profile, university_staff)
        reject_verification(first_request, reviewer=university_staff, reason="Blurry.")
        second_entry, _second = self.logged_access(student_profile, university_staff)

        assert first_entry.subject_token != second_entry.subject_token

    def test_two_cases_get_different_tokens(
        self, student_profile, student_profile_factory, university_staff
    ):
        entry, _request = self.logged_access(student_profile, university_staff)
        other, _other = self.logged_access(student_profile_factory(), university_staff)

        assert entry.subject_token != other.subject_token

    def test_two_accesses_to_one_case_share_its_token(self, student_profile, university_staff):
        """The grouping the post-erasure trail depends on. Minting a fresh
        token per access would leave a log nobody could group at all."""
        from accounts.documents import DocumentAccessLog, signed_document_url

        _entry, request = self.logged_access(student_profile, university_staff)
        signed_document_url(request.document, reviewer=university_staff)

        tokens = set(
            DocumentAccessLog.objects.filter(verification_request=request).values_list(
                "subject_token", flat=True
            )
        )

        assert len(tokens) == 1

    def test_the_row_survives_erasure(self, student_profile, university_staff):
        from accounts.documents import DocumentAccessLog

        self.logged_access(student_profile, university_staff)

        erase_personal_data(student_profile.user)

        assert DocumentAccessLog.objects.count() == 1

    def test_what_survives_still_answers_the_audit_question(
        self, student_profile, university_staff
    ):
        """ "Who opened this case, when, and why" must still have an answer."""
        entry, _request = self.logged_access(student_profile, university_staff)
        token = entry.subject_token

        erase_personal_data(student_profile.user)
        entry.refresh_from_db()

        assert entry.subject_token == token
        assert entry.reviewer_label
        assert entry.accessed_at is not None
        assert entry.purpose

    def test_the_links_to_the_person_are_cut(self, student_profile, university_staff):
        entry, _request = self.logged_access(student_profile, university_staff)

        erase_personal_data(student_profile.user)
        entry.refresh_from_db()

        assert entry.document_id is None
        assert entry.verification_request_id is None

    def test_no_join_path_reaches_surviving_personal_data(self, student_profile, university_staff):
        """The property that matters, walked rather than remembered.

        Follows every forward relation from the log row and asserts none of
        them lands on the student, their profile, their request or their
        document. A test asserting on the two columns I happen to remember
        would still pass after somebody added a third.
        """
        entry, request = self.logged_access(student_profile, university_staff)
        user = student_profile.user

        # The path exists before erasure -- otherwise this proves nothing.
        before = reachable_from(entry)
        assert f"{user._meta.label}#{user.pk}" in before

        erase_personal_data(user)
        entry.refresh_from_db()

        after = reachable_from(entry)
        forbidden = {
            f"{user._meta.label}#{user.pk}",
            f"{student_profile._meta.label}#{student_profile.pk}",
            f"{request._meta.label}#{request.pk}",
            f"{request.document._meta.label}#{request.document.pk}",
        }

        assert not (set(after) & forbidden), (
            f"erasure left a path from the access log to personal data: "
            f"{sorted(set(after) & forbidden)}\nfull walk: {after}"
        )

    def test_the_reviewer_is_still_reachable_and_that_is_intended(
        self, student_profile, university_staff
    ):
        """The trail is about the reviewer. Cutting that link too would leave a
        row saying only that *somebody* looked at *something*."""
        entry, _request = self.logged_access(student_profile, university_staff)

        erase_personal_data(student_profile.user)
        entry.refresh_from_db()

        assert f"{university_staff._meta.label}#{university_staff.pk}" in reachable_from(entry)

    def test_it_is_irreversible(self, student_profile, university_staff):
        """The token is random, so there is nothing to reverse -- no key, no
        salt, no lookup table anywhere in the system."""
        from accounts.documents import DocumentAccessLog

        _entry, request = self.logged_access(student_profile, university_staff)
        token = request.subject_token

        erase_personal_data(student_profile.user)

        # The token still exists on the request row, but the request is no
        # longer reachable FROM the log, and nothing else stores the mapping.
        assert DocumentAccessLog.objects.filter(subject_token=token).exists()
        assert not DocumentAccessLog.objects.filter(
            subject_token=token, verification_request__isnull=False
        ).exists()

    def test_the_report_counts_what_it_pseudonymised(self, student_profile, university_staff):
        self.logged_access(student_profile, university_staff)

        report = erase_personal_data(student_profile.user)

        assert report.access_log_rows_pseudonymised == 1


# ---------------------------------------------------------------------------
# Landlord erasure
# ---------------------------------------------------------------------------


class TestLandlordErasure:
    """ADR-008 §2.2. A landlord is two things at once.

    A natural person, whose personal data they may have erased; and a
    counterparty to contracts other people are still relying on, whose business
    record they may not delete on their own say-so.
    """

    def a_landlord_with_a_block(
        self, landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
    ):
        prop = property_factory(landlord=landlord_profile)
        unit = unit_factory(property=prop)
        end = dt.date.today() - dt.timedelta(days=1)
        tenancy = tenancy_factory(
            unit=unit, tenant=tenant, start_date=end - dt.timedelta(days=120), end_date=end
        )
        review = create_review(tenancy, rating=2, comment="The gate never locked.")
        return landlord_profile.user, prop, unit, review

    def test_personal_data_is_erased(
        self, landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
    ):
        user, _prop, _unit, _review = self.a_landlord_with_a_block(
            landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
        )
        landlord_profile.national_id = "12345678"
        landlord_profile.kra_pin = "A001234567X"
        landlord_profile.payout_phone = "+254700000000"
        landlord_profile.business_name = "Mwangi Rentals"
        landlord_profile.save()

        erase_landlord_data(user)
        user.refresh_from_db()
        landlord_profile.refresh_from_db()

        assert user.phone_number == ""
        assert landlord_profile.national_id == ""
        assert landlord_profile.kra_pin == ""
        assert landlord_profile.payout_phone == ""
        assert landlord_profile.business_name == ""

    def test_the_business_record_is_retained(
        self, landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
    ):
        """Deleting the properties would erase other people's tenancy history
        and their reviews, which is not the landlord's right to exercise."""
        from properties.models import Property, Unit

        user, prop, unit, review = self.a_landlord_with_a_block(
            landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
        )

        erase_landlord_data(user)

        assert Property.all_objects.filter(pk=prop.pk).exists()
        assert Unit.all_objects.filter(pk=unit.pk).exists()
        assert Review.all_objects.filter(pk=review.pk).exists()

    def test_properties_go_dormant_rather_than_cascading(
        self, landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
    ):
        from properties.constants import PropertyStatus

        user, prop, unit, _review = self.a_landlord_with_a_block(
            landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
        )

        erase_landlord_data(user)
        prop.refresh_from_db()
        unit.refresh_from_db()

        assert prop.status == PropertyStatus.DORMANT
        assert prop.published_at is None
        assert unit.is_active is False

    def test_a_dormant_property_is_unsearchable(
        self,
        landlord_profile,
        property_factory,
        unit_factory,
        tenancy_factory,
        tenant,
        university,
        campus_factory,
        campus_distance_factory,
    ):
        """Unlisted means unlisted. There is nobody left to answer an inquiry
        about it."""
        from properties.constants import TRANSACTABLE_PROPERTY_STATUSES
        from properties.models import Property

        user, prop, _unit, _review = self.a_landlord_with_a_block(
            landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
        )
        campus_distance_factory(
            property=prop, university=university, campus=campus_factory(university=university)
        )

        erase_landlord_data(user)

        listable = Property.objects.for_tenant(university).filter(
            status__in=TRANSACTABLE_PROPERTY_STATUSES
        )
        assert prop not in listable

    def test_dormant_is_not_a_state_the_owner_chose(self):
        """Distinct from ARCHIVED, which is a decision an owner made about a
        listing they still hold. Nobody can move a property back out of
        dormant, because there is no owner left to do it."""
        from properties.constants import PropertyStatus

        assert PropertyStatus.DORMANT != PropertyStatus.ARCHIVED
        from properties.constants import TRANSACTABLE_PROPERTY_STATUSES

        assert PropertyStatus.DORMANT not in TRANSACTABLE_PROPERTY_STATUSES

    def test_reviews_stay_visible(
        self, landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
    ):
        """Same reasoning as student erasure: deleting criticism by deleting
        the account cannot be an available move."""
        user, _prop, _unit, review = self.a_landlord_with_a_block(
            landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
        )

        erase_landlord_data(user)
        review.refresh_from_db()

        assert review.is_published is True
        assert "gate" in review.comment

    def test_the_landlord_aggregate_survives_under_the_tombstone(
        self, landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
    ):
        from reviews.aggregates import LandlordRatingAggregate
        from reviews.recompute import recompute_landlord

        user, _prop, _unit, _review = self.a_landlord_with_a_block(
            landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
        )
        recompute_landlord(landlord_profile.pk)

        erase_landlord_data(user)

        aggregate = LandlordRatingAggregate.objects.get(landlord=landlord_profile)
        assert aggregate.average_rating is not None
        assert display_name_for(aggregate.landlord.user) == "Former landlord"

    def test_the_tombstone_says_landlord(
        self, landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
    ):
        user, _prop, _unit, _review = self.a_landlord_with_a_block(
            landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
        )

        erase_landlord_data(user)
        user.refresh_from_db()

        assert display_name_for(user) == "Former landlord"


class TestLandlordErasureIsBlockedByRunningTenancies:
    """Flag, never silently partial.

    Erasing the safe fields and leaving the rest is the worst outcome: the
    subject believes they are erased, the platform believes it complied, and
    neither is true.
    """

    def a_landlord_with_a_running_stay(
        self, landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
    ):
        prop = property_factory(landlord=landlord_profile)
        unit = unit_factory(property=prop)
        tenancy_factory(
            unit=unit,
            tenant=tenant,
            start_date=dt.date.today() - dt.timedelta(days=30),
            end_date=dt.date.today() + dt.timedelta(days=90),
        )
        return landlord_profile.user, prop

    def test_it_is_refused(
        self, landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
    ):
        """A landlord with students living in their property is a party to a
        running contract. Erasing their contact details mid-tenancy leaves
        those students with nobody to call."""
        user, _prop = self.a_landlord_with_a_running_stay(
            landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
        )

        with pytest.raises(ActiveTenanciesError):
            erase_landlord_data(user)

    def test_nothing_is_erased_when_it_is_refused(
        self, landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
    ):
        from properties.constants import PropertyStatus

        user, prop = self.a_landlord_with_a_running_stay(
            landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
        )
        landlord_profile.national_id = "12345678"
        landlord_profile.save(update_fields=["national_id"])
        original_email = user.email

        with pytest.raises(ActiveTenanciesError):
            erase_landlord_data(user)

        user.refresh_from_db()
        landlord_profile.refresh_from_db()
        prop.refresh_from_db()

        assert user.email == original_email
        assert user.erased_at is None
        assert landlord_profile.national_id == "12345678"
        assert prop.status != PropertyStatus.DORMANT

    def test_the_blocker_says_what_is_in_the_way(
        self, landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
    ):
        user, _prop = self.a_landlord_with_a_running_stay(
            landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
        )

        blockers = landlord_erasure_blockers(user)

        assert len(blockers) == 1
        assert "tenanc" in blockers[0].lower()
        assert "once they end" in blockers[0]

    def test_it_completes_once_the_stay_ends(
        self, landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
    ):
        """Currency is derived, so this needs no job to have run -- the same
        rows answer differently on a later date."""
        user, _prop = self.a_landlord_with_a_running_stay(
            landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
        )
        after_the_stay = dt.date.today() + dt.timedelta(days=200)

        assert landlord_erasure_blockers(user, today=after_the_stay) == []

        report = erase_landlord_data(user, today=after_the_stay)

        assert report.properties_made_dormant == 1

    def test_a_past_tenancy_does_not_block(
        self, landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
    ):
        prop = property_factory(landlord=landlord_profile)
        end = dt.date.today() - dt.timedelta(days=1)
        tenancy_factory(
            unit=unit_factory(property=prop),
            tenant=tenant,
            start_date=end - dt.timedelta(days=120),
            end_date=end,
        )

        assert landlord_erasure_blockers(landlord_profile.user) == []


class TestLandlordErasureIsBlockedByUpcomingTenancies:
    """An agreed stay that has not started blocks too.

    Checking only `.current()` let a landlord with a tenancy starting next
    month erase today, leaving that student a dormant listing and no
    counterparty on move-in day. That is precisely the harm the block exists to
    prevent, and it is worse than the running case: the student has not had the
    tenancy long enough to have noticed anything going wrong.
    """

    def a_landlord_with_a_future_stay(
        self, landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
    ):
        prop = property_factory(landlord=landlord_profile)
        tenancy_factory(unit=unit_factory(property=prop), tenant=tenant, upcoming=True)
        return landlord_profile.user

    def test_it_is_refused(
        self, landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
    ):
        user = self.a_landlord_with_a_future_stay(
            landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
        )

        with pytest.raises(ActiveTenanciesError):
            erase_landlord_data(user)

    def test_the_blocker_says_the_stay_has_not_started(
        self, landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
    ):
        """ "Currently running" and "due to start" need different wording, or
        the operator reading the flag looks for a tenant who is not there."""
        user = self.a_landlord_with_a_future_stay(
            landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
        )

        blockers = landlord_erasure_blockers(user)

        assert len(blockers) == 1
        assert "due to start" in blockers[0]

    def test_nothing_is_erased(
        self, landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
    ):
        """Flag, never silently partial."""
        user = self.a_landlord_with_a_future_stay(
            landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
        )
        original = user.email

        with pytest.raises(ActiveTenanciesError):
            erase_landlord_data(user)
        user.refresh_from_db()

        assert user.email == original
        assert user.erased_at is None

    def test_a_past_stay_alone_does_not_block(
        self, landlord_profile, property_factory, unit_factory, tenancy_factory, tenant
    ):
        """History is not a running obligation. The default fixture is a
        finished stay, which is exactly this case."""
        prop = property_factory(landlord=landlord_profile)
        tenancy_factory(unit=unit_factory(property=prop), tenant=tenant)

        assert landlord_erasure_blockers(landlord_profile.user) == []

    def test_both_kinds_are_reported_together(
        self,
        landlord_profile,
        property_factory,
        unit_factory,
        tenancy_factory,
        tenant,
        student_profile,
    ):
        """An operator clearing the way needs the whole list, not the first
        obstacle followed by another one after they fix it."""
        prop = property_factory(landlord=landlord_profile)
        tenancy_factory(unit=unit_factory(property=prop, label="A1"), tenant=tenant, current=True)
        tenancy_factory(
            unit=unit_factory(property=prop, label="A2"),
            tenant=student_profile.user,
            upcoming=True,
        )

        blockers = landlord_erasure_blockers(landlord_profile.user)

        assert len(blockers) == 2
        assert any("currently running" in line for line in blockers)
        assert any("due to start" in line for line in blockers)
