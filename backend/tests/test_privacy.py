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
    AlreadyErasedError,
    display_name_for,
    erase_personal_data,
    export_personal_data,
    is_tombstoned,
)
from ratings.models import Review
from ratings.recompute import recompute_unit
from ratings.services import create_review
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
