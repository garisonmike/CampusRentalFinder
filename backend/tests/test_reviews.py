"""
Reviews (ADR-004).

The draft this replaces let any authenticated user review any property they had
never been to. `tests/test_api_contract.py` recorded that as
`test_any_tenant_can_review_any_rental_without_ever_living_there`, marked
"expected to be INVERTED by the rewrite". This file is that inversion.

Everything here is one property in different lights: **a review exists only
because a stay does**, and the database will not store one that does not.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.conf import settings
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone

from config.tenancy import TenantScopeError
from reviews.constants import CATEGORY_RATING_FIELDS, DisputeAnnotation
from reviews.models import Review, ReviewResponse
from reviews.services import (
    ReviewFrozenError,
    TenancyNotReviewableError,
    assert_tenancy_is_reviewable,
    create_review,
    respond_to_review,
    review_dispute_annotation,
    review_is_verified,
    stay_days,
    update_review,
)
from tenancies.constants import ConfirmationSource, DisputeReason
from tenancies.services import (
    accept_correction,
    confirm_claim,
    create_claim,
    raise_dispute,
)

pytestmark = pytest.mark.django_db


MINIMUM = settings.REVIEW_MINIMUM_STAY_DAYS


def a_tenancy_of(tenancy_factory, days: int, **kwargs):
    """A stay of exactly ``days``, ending yesterday."""
    end = dt.date.today() - dt.timedelta(days=1)
    return tenancy_factory(start_date=end - dt.timedelta(days=days), end_date=end, **kwargs)


# ---------------------------------------------------------------------------
# The trust property
# ---------------------------------------------------------------------------


class TestAReviewRequiresAStay:
    """The inversion of the draft's core contract test.

    In the draft: no tenancy, no stay, no proof of any relationship to the
    property was required, and the API returned 201. Here the requirement is a
    NOT NULL foreign key, so there is no code path — serializer, admin,
    management command or shell — that can produce a review without one.
    """

    def test_a_review_cannot_exist_without_a_tenancy(self):
        with pytest.raises(IntegrityError), transaction.atomic():
            Review.all_objects.create(tenancy=None, rating=5, comment="Great place.")

    def test_the_tenancy_field_is_not_nullable(self):
        """Asserted structurally as well, because a NOT NULL that quietly
        becomes nullable in a later migration would restore the draft's hole
        without failing any behavioural test that does not think to try."""
        assert Review._meta.get_field("tenancy").null is False

    def test_one_review_per_stay(self, tenancy_factory):
        tenancy = a_tenancy_of(tenancy_factory, MINIMUM + 10)
        create_review(tenancy, rating=4)

        with pytest.raises(IntegrityError), transaction.atomic():
            Review.all_objects.create(tenancy=tenancy, rating=1)

    def test_the_service_refuses_a_second_review_with_an_explanation(self, tenancy_factory):
        tenancy = a_tenancy_of(tenancy_factory, MINIMUM + 10)
        create_review(tenancy, rating=4)

        with pytest.raises(TenancyNotReviewableError) as caught:
            create_review(tenancy, rating=1)

        assert "already been reviewed" in str(caught.value)

    def test_the_reviewer_is_read_through_the_tenancy(self, tenancy_factory):
        """Never copied onto the review. A denormalised copy is a chance for
        the two to disagree, and disagreement here IS the trust property
        failing."""
        tenancy = a_tenancy_of(tenancy_factory, MINIMUM + 10)
        review = create_review(tenancy, rating=4)

        assert review.reviewer() == tenancy.tenant
        assert not hasattr(review, "tenant_id")
        assert not hasattr(review, "unit_id")

    def test_the_stay_cannot_be_deleted_out_from_under_the_review(self, tenancy_factory):
        """PROTECT, not CASCADE: deleting the tenancy would remove the evidence
        while leaving the review's meaning intact."""
        tenancy = a_tenancy_of(tenancy_factory, MINIMUM + 10)
        create_review(tenancy, rating=4)

        with pytest.raises(IntegrityError), transaction.atomic():
            tenancy.delete()


# ---------------------------------------------------------------------------
# The minimum stay
# ---------------------------------------------------------------------------


class TestMinimumStay:
    """The single documented exception to "constraints in the database".

    It compares against *today*: a tenancy that is thirty days old now was
    twenty-nine yesterday, and no CheckConstraint can express a predicate whose
    truth changes while the row sits still. So it is one named function, and it
    is tested here directly rather than only through a serializer — an
    invariant exercised only through the API is one the admin can walk past.
    """

    def test_a_short_stay_cannot_be_reviewed(self, tenancy_factory):
        tenancy = a_tenancy_of(tenancy_factory, MINIMUM - 1)

        with pytest.raises(TenancyNotReviewableError):
            assert_tenancy_is_reviewable(tenancy)

    def test_the_refusal_says_how_short(self, tenancy_factory):
        tenancy = a_tenancy_of(tenancy_factory, 7)

        with pytest.raises(TenancyNotReviewableError) as caught:
            assert_tenancy_is_reviewable(tenancy)

        assert str(MINIMUM) in str(caught.value)
        assert "7" in str(caught.value)

    def test_exactly_the_minimum_is_enough(self, tenancy_factory):
        """A floor, not a fence."""
        assert_tenancy_is_reviewable(a_tenancy_of(tenancy_factory, MINIMUM))

    def test_the_create_path_goes_through_the_gate(self, tenancy_factory):
        """There is deliberately no way to create a Review correctly that
        skips it."""
        tenancy = a_tenancy_of(tenancy_factory, MINIMUM - 5)

        with pytest.raises(TenancyNotReviewableError):
            create_review(tenancy, rating=5)

        assert Review.all_objects.count() == 0

    def test_an_ongoing_stay_counts_up_to_today(self, tenancy_factory):
        """This is exactly why the rule cannot be a check constraint."""
        tenancy = tenancy_factory(
            start_date=dt.date.today() - dt.timedelta(days=MINIMUM + 5), end_date=None
        )

        assert stay_days(tenancy) == MINIMUM + 5
        assert_tenancy_is_reviewable(tenancy)

    def test_an_ongoing_stay_that_is_still_too_new_is_refused(self, tenancy_factory):
        tenancy = tenancy_factory(start_date=dt.date.today() - dt.timedelta(days=3), end_date=None)

        with pytest.raises(TenancyNotReviewableError):
            assert_tenancy_is_reviewable(tenancy)

    def test_the_threshold_comes_from_settings(self, tenancy_factory):
        tenancy = a_tenancy_of(tenancy_factory, 10)

        with override_settings(REVIEW_MINIMUM_STAY_DAYS=5):
            assert_tenancy_is_reviewable(tenancy)


# ---------------------------------------------------------------------------
# The edit window
# ---------------------------------------------------------------------------


class TestEditWindow:
    """The draft allowed an author to edit their review with no time limit.

    A review that can be rewritten for ever can be rewritten under pressure,
    and the pressure would come from the party with more of it.
    """

    def review(self, tenancy_factory, **kwargs):
        return create_review(a_tenancy_of(tenancy_factory, MINIMUM + 10), rating=4, **kwargs)

    def test_a_fresh_review_is_editable(self, tenancy_factory):
        assert self.review(tenancy_factory).is_editable() is True

    def test_the_window_is_the_configured_length(self, tenancy_factory):
        review = self.review(tenancy_factory)
        expected = review.created_at + dt.timedelta(days=settings.REVIEW_EDIT_WINDOW_DAYS)

        assert abs((review.editable_until - expected).total_seconds()) < 5

    def test_a_review_freezes_after_the_window(self, tenancy_factory):
        review = self.review(tenancy_factory)
        past = review.editable_until + dt.timedelta(seconds=1)

        assert review.is_editable(now=past) is False

    def test_editing_a_frozen_review_is_refused(self, tenancy_factory):
        review = self.review(tenancy_factory)
        past = review.editable_until + dt.timedelta(days=1)

        with pytest.raises(ReviewFrozenError):
            update_review(review, now=past, rating=1)

    def test_editing_inside_the_window_works(self, tenancy_factory):
        review = self.review(tenancy_factory)

        update_review(review, rating=2, comment="The gate broke again.")
        review.refresh_from_db()

        assert review.rating == 2

    def test_the_window_is_stored_not_recomputed(self, tenancy_factory):
        """So that changing the setting later does not retroactively reopen
        reviews that had already closed."""
        review = self.review(tenancy_factory)
        original = review.editable_until

        with override_settings(REVIEW_EDIT_WINDOW_DAYS=365):
            review.refresh_from_db()

        assert review.editable_until == original


# ---------------------------------------------------------------------------
# Ratings
# ---------------------------------------------------------------------------


class TestRatings:
    def test_the_overall_rating_is_bounded(self, tenancy_factory):
        tenancy = a_tenancy_of(tenancy_factory, MINIMUM + 10)

        for bad in (0, 6):
            with pytest.raises(IntegrityError), transaction.atomic():
                Review.all_objects.create(tenancy=tenancy, rating=bad)

    def test_every_category_rating_is_bounded(self, tenancy_factory):
        for field in CATEGORY_RATING_FIELDS:
            tenancy = a_tenancy_of(tenancy_factory, MINIMUM + 10)

            with pytest.raises(IntegrityError), transaction.atomic():
                Review.all_objects.create(tenancy=tenancy, rating=3, **{field: 9})

    def test_category_ratings_are_optional(self, tenancy_factory):
        review = create_review(a_tenancy_of(tenancy_factory, MINIMUM + 10), rating=3)

        for field in CATEGORY_RATING_FIELDS:
            assert getattr(review, field) is None

    def test_water_reliability_is_a_first_class_category(self):
        """The complaint that actually recurs in Kenyan student housing, so it
        is a field rather than something to find in prose."""
        assert "water_reliability_rating" in CATEGORY_RATING_FIELDS

    def test_a_hidden_review_must_say_why(self, tenancy_factory):
        """Unexplained moderation of the content the platform exists to
        protect is not acceptable."""
        review = create_review(a_tenancy_of(tenancy_factory, MINIMUM + 10), rating=1)
        review.is_published = False

        with pytest.raises(IntegrityError), transaction.atomic():
            review.save()


# ---------------------------------------------------------------------------
# The dispute annotation
# ---------------------------------------------------------------------------


class TestDisputeAnnotation:
    """ADR-004 §3a: derived at read time, never stored.

    The first version of the amendment put a `disputed_by_landlord` boolean on
    Review. It is permanent, with no path to removal even when the dispute is
    later shown to be spurious.
    """

    def reviewed_claim(self, unit_factory, tenant, landlord, dispute=True):
        start = dt.date.today() - dt.timedelta(days=200)
        claim = create_claim(
            unit=unit_factory(),
            claimant=tenant,
            start_date=start,
            end_date=start + dt.timedelta(days=MINIMUM + 20),
            monthly_rent_kes=Decimal("9500.00"),
        )
        if dispute:
            raise_dispute(
                claim,
                reason=DisputeReason.DATES_INCORRECT,
                disputed_by=landlord,
                proposed_start_date=claim.start_date,
                proposed_end_date=claim.end_date - dt.timedelta(days=2),
            )
            claim.refresh_from_db()
            tenancy = accept_correction(claim)
        else:
            tenancy = confirm_claim(
                claim, source=ConfirmationSource.LANDLORD, confirmed_by=landlord
            )
        return create_review(tenancy, rating=2), claim

    def test_no_dispute_means_no_annotation(self, unit_factory, tenant, landlord):
        review, _ = self.reviewed_claim(unit_factory, tenant, landlord, dispute=False)

        assert review_dispute_annotation(review) is None

    def test_a_witnessed_tenancy_has_no_claim_and_no_annotation(self, tenancy_factory):
        review = create_review(a_tenancy_of(tenancy_factory, MINIMUM + 10), rating=5)

        assert review.tenancy.claim is None
        assert review_dispute_annotation(review) is None

    def test_a_disputed_stay_is_annotated(self, unit_factory, tenant, landlord):
        review, _ = self.reviewed_claim(unit_factory, tenant, landlord)

        assert review_dispute_annotation(review) == DisputeAnnotation.DISPUTED

    def test_the_annotation_is_neutral(self):
        """ "The landlord disputed this stay" — a fact, left for the reader to
        weigh. A landlord who disputes honestly and one who disputes
        tactically produce the same annotation, which is exactly why it must
        not read as a verdict."""
        text = str(DisputeAnnotation.DISPUTED.label).lower()

        for verdict in ("unverified", "unreliable", "questionable", "suspect", "fake"):
            assert verdict not in text

    def test_a_withdrawn_dispute_clears_the_annotation(self, unit_factory, tenant, landlord):
        """The whole reason it is derived. A stored boolean could not have
        undone this without a migration over live reviews."""
        review, claim = self.reviewed_claim(unit_factory, tenant, landlord)
        claim.dispute_withdrawn_at = timezone.now()
        claim.save(update_fields=["dispute_withdrawn_at", "updated_at"])
        review.refresh_from_db()

        assert review_dispute_annotation(review) is None

    def test_the_review_is_not_hidden_or_demoted_by_a_dispute(self, unit_factory, tenant, landlord):
        """Not greyed out, not collapsed, not excluded from the average, not
        labelled unverified. The annotation is the entire consequence."""
        review, _ = self.reviewed_claim(unit_factory, tenant, landlord)

        assert review.is_published is True
        assert review.hidden_reason == ""

    def test_the_disputer_record_hook_is_off_by_default(self):
        """Suppressing the annotation for a landlord whose disputes are rarely
        upheld is defensible, but it is a judgement the platform makes about a
        named person and should be switched on deliberately."""
        assert settings.REVIEW_ANNOTATION_RESPECTS_DISPUTE_RECORD is False

    def test_the_hook_does_nothing_over_a_small_sample(self, unit_factory, tenant, landlord):
        """A rate over three disputes says nothing."""
        review, _ = self.reviewed_claim(unit_factory, tenant, landlord)

        with override_settings(
            REVIEW_ANNOTATION_RESPECTS_DISPUTE_RECORD=True,
            REVIEW_ANNOTATION_MINIMUM_DISPUTE_SAMPLE=50,
        ):
            assert review_dispute_annotation(review) == DisputeAnnotation.DISPUTED


class TestVerifiedBadge:
    def test_an_unverified_author_has_no_badge(self, tenancy_factory, student_profile):
        tenancy = a_tenancy_of(tenancy_factory, MINIMUM + 10, tenant=student_profile.user)

        assert review_is_verified(create_review(tenancy, rating=4)) is False

    def test_a_verified_author_has_one(self, tenancy_factory, verified_student_profile):
        """Read through the tenancy's tenant at render time, never copied onto
        the review: a student who verifies after posting should not have to
        repost to be believed."""
        tenancy = a_tenancy_of(tenancy_factory, MINIMUM + 10, tenant=verified_student_profile.user)

        assert review_is_verified(create_review(tenancy, rating=4)) is True

    def test_an_unverified_student_may_still_post(self, tenancy_factory):
        """The badge is simply absent (ADR-003). Verification gates the badge,
        not the right to speak, unless the university says otherwise."""
        review = create_review(a_tenancy_of(tenancy_factory, MINIMUM + 10), rating=3)

        assert review.is_published is True
        assert review_is_verified(review) is False


# ---------------------------------------------------------------------------
# ReviewResponse
# ---------------------------------------------------------------------------


class TestReviewResponse:
    def review(self, tenancy_factory):
        return create_review(a_tenancy_of(tenancy_factory, MINIMUM + 10), rating=2)

    def test_a_landlord_may_respond(self, tenancy_factory, landlord):
        review = self.review(tenancy_factory)

        response = respond_to_review(review, author=landlord, body="Fixed the gate.")

        assert response.review == review

    def test_only_one_response_ever(self, tenancy_factory, landlord):
        """Enforced by the schema, not by the `if review.landlord_response:`
        check the draft used — which the admin, a management command and a
        data migration all routed around."""
        review = self.review(tenancy_factory)
        respond_to_review(review, author=landlord, body="Fixed the gate.")

        with pytest.raises(IntegrityError), transaction.atomic():
            respond_to_review(review, author=landlord, body="Actually, no.")

    def test_an_empty_response_is_refused(self, tenancy_factory, landlord):
        review = self.review(tenancy_factory)

        with pytest.raises(IntegrityError), transaction.atomic():
            respond_to_review(review, author=landlord, body="")

    def test_the_author_cannot_be_deleted_out_from_under_it(self, tenancy_factory, landlord):
        review = self.review(tenancy_factory)
        respond_to_review(review, author=landlord, body="Fixed the gate.")

        with pytest.raises(IntegrityError), transaction.atomic():
            landlord.delete()

    def test_deleting_the_review_takes_the_response_with_it(self, tenancy_factory, landlord):
        """CASCADE here, unlike the review's own PROTECT: a reply to nothing is
        not evidence of anything."""
        review = self.review(tenancy_factory)
        respond_to_review(review, author=landlord, body="Fixed the gate.")

        review.delete()

        assert ReviewResponse.all_objects.count() == 0


# ---------------------------------------------------------------------------
# Tenant scoping
# ---------------------------------------------------------------------------


class TestReviewScoping:
    def test_unqualified_queries_raise(self, review_factory):
        review_factory()

        with pytest.raises(TenantScopeError):
            list(Review.objects.all())
        with pytest.raises(TenantScopeError):
            list(ReviewResponse.objects.all())

    def test_reviews_scope_through_the_tenancy(
        self,
        tenancy_factory,
        unit_factory,
        property_factory,
        campus_factory,
        campus_distance_factory,
        university,
        university_factory,
    ):
        prop = property_factory()
        campus_distance_factory(
            property=prop, university=university, campus=campus_factory(university=university)
        )
        tenancy = a_tenancy_of(tenancy_factory, MINIMUM + 10, unit=unit_factory(property=prop))
        review = create_review(tenancy, rating=4)

        assert review in Review.objects.for_tenant(university)
        assert review not in Review.objects.for_tenant(university_factory())


class TestStayLengthHasOneDefinition:
    """`stay_days` was a second copy of `effective_stay_days`, carrying the
    same defect after that one was fixed.

    The eligibility gate reads the latch, so the wrong figure never reached a
    decision -- but `ReviewSerializer.stay_months` renders it, so a review card
    could say "stayed 12 months" about somebody three days into a lease.
    """

    def test_a_current_lease_reports_elapsed_not_agreed(self, tenancy_factory, unit_factory):
        tenancy = tenancy_factory(
            unit=unit_factory(),
            start_date=dt.date.today() - dt.timedelta(days=3),
            end_date=dt.date.today() + dt.timedelta(days=362),
        )

        assert stay_days(tenancy) == 3

    def test_the_review_card_does_not_overstate_the_stay(
        self, review_factory, tenancy_factory, unit_factory
    ):
        """The public field. `stay_months` is what a student reads when
        weighing how much a review is worth."""
        from reviews.serializers import ReviewSerializer

        review = review_factory(
            tenancy=tenancy_factory(
                unit=unit_factory(),
                start_date=dt.date.today() - dt.timedelta(days=40),
                end_date=dt.date.today() + dt.timedelta(days=300),
            )
        )

        assert ReviewSerializer(review).data["stay_months"] == 1

    def test_it_agrees_with_the_tenancies_definition(self, tenancy_factory, unit_factory):
        """Delegation rather than a matching implementation: two functions that
        agree today are two functions that can stop agreeing."""
        from tenancies.services import effective_stay_days

        for start_offset, end_offset in [(-3, 362), (-200, -50), (-40, None)]:
            tenancy = tenancy_factory(
                unit=unit_factory(),
                start_date=dt.date.today() + dt.timedelta(days=start_offset),
                end_date=(
                    None if end_offset is None else dt.date.today() + dt.timedelta(days=end_offset)
                ),
            )

            assert stay_days(tenancy) == effective_stay_days(tenancy)
