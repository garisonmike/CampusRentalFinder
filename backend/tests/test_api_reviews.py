"""
Review, response and rating endpoints (ADR-004).

The test that carries the most weight here is
:meth:`TestTheAnnotationIsBatched.test_fifty_reviews_cost_what_one_does`.

The dispute annotation is derived rather than stored **so that changing the
policy is a function edit rather than a migration over live reviews**. That
trade is only affordable if deriving it is batched — a per-review derivation is
one query per row plus, with the disputer-record hook on, a second per row. At
that point somebody would store it again for speed and the flexibility would be
gone.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import cast

import pytest
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from properties.constants import PropertyStatus
from reviews.recompute import recompute_property, recompute_unit
from reviews.services import create_review
from tenancies.constants import DisputeReason
from tenancies.models import Tenancy
from tenancies.services import accept_correction, create_claim, raise_dispute

pytestmark = pytest.mark.django_db

MINIMUM_STAY = 90


@pytest.fixture
def host(university):
    return f"{university.subdomain}.example.co.ke"


@pytest.fixture
def block(university, campus_factory, property_factory, unit_factory, campus_distance_factory):
    """A published property in the tenant, with one unit."""
    campus = campus_factory(university=university, is_main=True)
    prop = property_factory(status=PropertyStatus.PUBLISHED)
    campus_distance_factory(property=prop, university=university, campus=campus)
    unit = unit_factory(property=prop, total_count=60, vacant_count=60)
    return prop, unit


def get(api_client, url, host, **params):
    with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
        return api_client.get(url, params, HTTP_HOST=host)


def post(client, url, host, payload):
    with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
        return client.post(url, payload, format="json", HTTP_HOST=host)


def a_stay(tenancy_factory, unit, tenant, *, offset: int = 0):
    end = dt.date.today() - dt.timedelta(days=1 + offset)
    return tenancy_factory(
        unit=unit, tenant=tenant, start_date=end - dt.timedelta(days=MINIMUM_STAY), end_date=end
    )


# ---------------------------------------------------------------------------
# Ratings
# ---------------------------------------------------------------------------


class TestPropertyRating:
    def test_an_unreviewed_property_reports_null_not_zero(self, api_client, block, host):
        """`null` means 'no verified reviews yet'. A zero is a fabricated
        signal, and on a trust platform that is worse than no signal because it
        is indistinguishable from a real one."""
        prop, _unit = block

        body = get(api_client, f"/api/v1/reviews/properties/{prop.slug}/rating/", host).json()

        assert body["property"]["average_rating"] is None
        assert body["property"]["review_count"] == 0

    def test_it_reports_the_two_counts_separately(
        self, api_client, block, host, tenancy_factory, tenant, unit_factory
    ):
        """One student, two stays in the same block, two reviews: two rows
        behind the number and one voice in it. The divergence IS the
        de-duplication (ADR-004)."""
        prop, unit = block
        second_unit = unit_factory(property=prop, label="One-bedroom")
        create_review(a_stay(tenancy_factory, unit, tenant, offset=300), rating=2)
        create_review(a_stay(tenancy_factory, second_unit, tenant), rating=4)
        recompute_property(prop.pk)

        rating = get(api_client, f"/api/v1/reviews/properties/{prop.slug}/rating/", host).json()[
            "property"
        ]

        assert rating["review_count"] == 2
        assert rating["student_count"] == 1
        assert Decimal(rating["average_rating"]) == Decimal("3.00")

    def test_the_landlord_figure_is_a_separate_key(self, api_client, block, host):
        """Not a fallback value. A landlord's record is not this property's
        rating, and merging them would be the platform quietly answering a
        question nobody asked."""
        prop, _unit = block

        body = get(api_client, f"/api/v1/reviews/properties/{prop.slug}/rating/", host).json()

        assert "landlord" in body
        assert "property_count" in body["landlord"]

    def test_a_unit_rating_does_not_dedupe(
        self, api_client, block, host, tenancy_factory, tenant, student_profile
    ):
        """One stay, one review, and a tenant cannot hold overlapping stays in
        one unit -- there is nothing left to collapse."""
        _prop, unit = block
        create_review(a_stay(tenancy_factory, unit, tenant), rating=5)
        create_review(a_stay(tenancy_factory, unit, student_profile.user), rating=1)
        recompute_unit(unit.pk)

        body = get(api_client, f"/api/v1/reviews/units/{unit.pk}/rating/", host).json()

        assert body["review_count"] == 2
        assert body["student_count"] == 2

    def test_ratings_are_public(self, api_client, block, host):
        """A trust signal behind a login persuades nobody."""
        prop, _unit = block

        assert (
            get(api_client, f"/api/v1/reviews/properties/{prop.slug}/rating/", host).status_code
            == 200
        )


# ---------------------------------------------------------------------------
# The batched annotation
# ---------------------------------------------------------------------------


class TestTheAnnotationIsBatched:
    """ADR-004 §2.1, and the reason the annotation can stay derived."""

    def disputed_review(self, unit, tenant, landlord, *, offset: int):
        """A stay that was disputed, corrected and confirmed -- so the review
        exists and carries an annotation."""
        start = dt.date.today() - dt.timedelta(days=400 + offset)
        end = start + dt.timedelta(days=MINIMUM_STAY)
        claim = create_claim(
            unit=unit,
            claimant=tenant,
            start_date=start,
            end_date=end,
            monthly_rent_kes=Decimal("9000.00"),
        )
        raise_dispute(
            claim,
            reason=DisputeReason.DATES_INCORRECT,
            disputed_by=landlord,
            proposed_start_date=start,
            proposed_end_date=end - dt.timedelta(days=2),
        )
        claim.refresh_from_db()
        tenancy = accept_correction(claim)
        assert isinstance(tenancy, Tenancy), "the correction should have confirmed"
        return create_review(tenancy, rating=2, comment="Disputed stay.")

    def plain_review(self, unit, *, offset: int):
        """An application-sourced stay: no claim, so no dispute to annotate."""
        from tests.factories import StudentProfileFactory, TenancyFactory

        student = StudentProfileFactory()
        start = dt.date.today() - dt.timedelta(days=400 + offset)
        tenancy = cast(
            Tenancy,
            TenancyFactory(
                unit=unit,
                tenant=student.user,
                start_date=start,
                end_date=start + dt.timedelta(days=MINIMUM_STAY),
            ),
        )
        return create_review(tenancy, rating=4, comment="Undisputed stay.")

    def withdrawn_dispute_review(self, unit, landlord, *, offset: int):
        """Disputed and then withdrawn. The annotation must clear -- an
        annotation that outlives the dispute is the veto returning by another
        route (ADR-004 §2.1)."""
        from django.utils import timezone

        from tests.factories import StudentProfileFactory

        student = StudentProfileFactory()
        review = self.disputed_review(unit, student.user, landlord, offset=offset)
        claim = review.tenancy.claim
        claim.dispute_withdrawn_at = timezone.now()
        claim.save(update_fields=["dispute_withdrawn_at", "updated_at"])
        return review

    def noisy_disputer_review(self, unit, landlord, *, offset: int):
        """A landlord who disputes everything.

        The branch where the two implementations are furthest apart in shape:
        the batched path computes noise per disputer into a set, the single
        path counts per claim.
        """
        from django.conf import settings

        from tests.factories import StudentProfileFactory

        student = StudentProfileFactory()
        review = self.disputed_review(unit, student.user, landlord, offset=offset)

        # Enough resolved disputes by the same landlord to clear the minimum
        # sample the heuristic requires before it will call anybody noisy.
        for extra in range(settings.REVIEW_ANNOTATION_MINIMUM_DISPUTE_SAMPLE + 1):
            other = StudentProfileFactory()
            self.disputed_review(unit, other.user, landlord, offset=offset + 200 + extra * 40)

        return review

    def make_reviews(self, block, tenancy_factory, student_profile_factory, count: int):
        _prop, unit = block
        for index in range(count):
            student = student_profile_factory()
            create_review(a_stay(tenancy_factory, unit, student.user, offset=index), rating=4)

    def test_fifty_reviews_cost_what_one_does(
        self, api_client, block, host, tenancy_factory, student_profile_factory
    ):
        """The assertion the whole design rests on.

        Written as a comparison rather than a fixed number: a fixed count fails
        on every unrelated optimisation and gets bumped without being read.
        """
        prop, _unit = block
        url = f"/api/v1/reviews/properties/{prop.slug}/"

        self.make_reviews(block, tenancy_factory, student_profile_factory, 1)
        with CaptureQueriesContext(connection) as one:
            get(api_client, url, host)

        self.make_reviews(block, tenancy_factory, student_profile_factory, 49)
        with CaptureQueriesContext(connection) as fifty:
            response = get(api_client, url, host, page_size=100)

        assert len(response.json()["results"]) == 50
        assert len(fifty.captured_queries) == len(one.captured_queries), (
            f"{len(one.captured_queries)} queries for 1 review, "
            f"{len(fifty.captured_queries)} for 50 -- the annotation is being "
            f"derived per row, and somebody will store it again for speed."
        )

    def test_it_still_holds_with_the_disputer_hook_on(
        self, api_client, block, host, tenancy_factory, student_profile_factory
    ):
        """The hook counts a landlord's resolved disputes. Per review that is a
        second query per row; batched it is one grouped query for the page."""
        prop, _unit = block
        url = f"/api/v1/reviews/properties/{prop.slug}/"

        with override_settings(REVIEW_ANNOTATION_RESPECTS_DISPUTE_RECORD=True):
            self.make_reviews(block, tenancy_factory, student_profile_factory, 1)
            with CaptureQueriesContext(connection) as one:
                get(api_client, url, host)

            self.make_reviews(block, tenancy_factory, student_profile_factory, 29)
            with CaptureQueriesContext(connection) as thirty:
                response = get(api_client, url, host, page_size=100)

        assert len(response.json()["results"]) == 30
        assert len(thirty.captured_queries) == len(one.captured_queries)

    def test_the_hook_costs_at_most_one_extra_query(
        self, api_client, block, host, tenancy_factory, student_profile_factory
    ):
        prop, _unit = block
        url = f"/api/v1/reviews/properties/{prop.slug}/"
        self.make_reviews(block, tenancy_factory, student_profile_factory, 10)

        with CaptureQueriesContext(connection) as off:
            get(api_client, url, host)

        with (
            override_settings(REVIEW_ANNOTATION_RESPECTS_DISPUTE_RECORD=True),
            CaptureQueriesContext(connection) as on,
        ):
            get(api_client, url, host)

        assert len(on.captured_queries) <= len(off.captured_queries) + 1

    def test_an_undisputed_review_is_annotated_null(
        self, api_client, block, host, tenancy_factory, student_profile_factory
    ):
        prop, _unit = block
        self.make_reviews(block, tenancy_factory, student_profile_factory, 1)

        results = get(api_client, f"/api/v1/reviews/properties/{prop.slug}/", host).json()[
            "results"
        ]

        assert results[0]["dispute_annotation"] is None

    def test_a_disputed_review_carries_the_annotation(
        self, api_client, block, host, tenant, landlord
    ):
        prop, unit = block
        self.disputed_review(unit, tenant, landlord, offset=0)

        results = get(api_client, f"/api/v1/reviews/properties/{prop.slug}/", host).json()[
            "results"
        ]

        assert results[0]["dispute_annotation"] == "disputed"

    def test_the_batch_and_the_single_path_agree(self, api_client, block, host, tenant, landlord):
        """Two code paths computing the same thing is two chances to disagree,
        so this pins them together.

        **Across every branch, not one.** The earlier version of this asserted
        agreement for a single disputed review, which is one of at least four
        paths through the derivation -- and not the one likely to diverge. The
        batched version computes the noise heuristic per disputer into a set
        while the single version counts per claim, so that branch is where the
        two implementations are furthest apart in shape.
        """
        from reviews.services import dispute_annotations_for, review_dispute_annotation

        _prop, unit = block
        cases = {
            "disputed": self.disputed_review(unit, tenant, landlord, offset=0),
            "not disputed": self.plain_review(unit, offset=40),
            "dispute withdrawn": self.withdrawn_dispute_review(unit, landlord, offset=80),
            "noisy disputer": self.noisy_disputer_review(unit, landlord, offset=120),
        }

        batched = dispute_annotations_for(list(cases.values()))

        for label, review in cases.items():
            assert batched[review.pk] == review_dispute_annotation(review), (
                f"the two paths disagree for a {label} review"
            )

    def test_the_agreement_test_covers_more_than_one_answer(
        self, api_client, block, host, tenant, landlord
    ):
        """Otherwise both paths could return None for everything and agree.

        An agreement test whose cases all share one answer proves the two
        implementations are equally silent, which is exactly what a broken
        pair would look like.
        """
        from reviews.services import review_dispute_annotation

        _prop, unit = block
        answers = {
            review_dispute_annotation(self.disputed_review(unit, tenant, landlord, offset=0)),
            review_dispute_annotation(self.plain_review(unit, offset=40)),
        }

        assert len(answers) > 1

    def test_a_disputed_review_is_not_hidden_or_demoted(
        self, api_client, block, host, tenant, landlord
    ):
        """The annotation is the entire consequence. Not greyed out, not
        collapsed, not excluded from the average."""
        prop, unit = block
        self.disputed_review(unit, tenant, landlord, offset=0)
        recompute_property(prop.pk)

        listed = get(api_client, f"/api/v1/reviews/properties/{prop.slug}/", host).json()
        rating = get(api_client, f"/api/v1/reviews/properties/{prop.slug}/rating/", host).json()[
            "property"
        ]

        assert listed["count"] == 1
        assert rating["review_count"] == 1
        assert rating["average_rating"] is not None


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


class TestWritingAReview:
    def test_a_student_can_review_their_own_stay(
        self, authenticate, block, host, tenancy_factory, tenant
    ):
        _prop, unit = block
        tenancy = a_stay(tenancy_factory, unit, tenant)

        response = post(
            authenticate(tenant),
            "/api/v1/reviews/",
            host,
            {"tenancy": tenancy.pk, "rating": 4, "comment": "Water was reliable."},
        )

        assert response.status_code == 201
        assert response.json()["rating"] == 4

    def test_anonymous_cannot_write(self, api_client, block, host, tenancy_factory, tenant):
        _prop, unit = block
        tenancy = a_stay(tenancy_factory, unit, tenant)

        response = post(api_client, "/api/v1/reviews/", host, {"tenancy": tenancy.pk, "rating": 5})

        assert response.status_code == 401

    def test_you_cannot_review_someone_elses_stay(
        self, authenticate, block, host, tenancy_factory, tenant, student_profile
    ):
        """The core trust hole the draft had. Refused with the same message as
        a missing stay -- confirming somebody else's exists is an enumeration
        oracle."""
        _prop, unit = block
        theirs = a_stay(tenancy_factory, unit, tenant)

        response = post(
            authenticate(student_profile.user),
            "/api/v1/reviews/",
            host,
            {"tenancy": theirs.pk, "rating": 1},
        )

        assert response.status_code == 400
        assert "No such stay" in str(response.json())

    def test_a_short_stay_is_refused_with_its_reason(
        self, authenticate, block, host, tenancy_factory, tenant
    ):
        """The service exception's message reaches the user rather than being
        replaced by a generic one (config/api/errors.py)."""
        _prop, unit = block
        end = dt.date.today() - dt.timedelta(days=1)
        short = tenancy_factory(
            unit=unit, tenant=tenant, start_date=end - dt.timedelta(days=5), end_date=end
        )

        response = post(
            authenticate(tenant), "/api/v1/reviews/", host, {"tenancy": short.pk, "rating": 5}
        )

        assert response.status_code == 409
        body = response.json()["error"]
        assert body["code"] == "not_reviewable"
        assert "30" in body["message"]

    def test_a_second_review_of_one_stay_is_refused(
        self, authenticate, block, host, tenancy_factory, tenant
    ):
        _prop, unit = block
        tenancy = a_stay(tenancy_factory, unit, tenant)
        client = authenticate(tenant)
        post(client, "/api/v1/reviews/", host, {"tenancy": tenancy.pk, "rating": 4})

        response = post(client, "/api/v1/reviews/", host, {"tenancy": tenancy.pk, "rating": 1})

        assert response.status_code == 409


class TestEditingAReview:
    def test_the_author_can_edit_inside_the_window(
        self, authenticate, block, host, tenancy_factory, tenant
    ):
        _prop, unit = block
        review = create_review(a_stay(tenancy_factory, unit, tenant), rating=4)

        with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
            response = authenticate(tenant).patch(
                f"/api/v1/reviews/{review.pk}/", {"rating": 2}, format="json", HTTP_HOST=host
            )

        assert response.status_code == 200
        assert response.json()["rating"] == 2

    def test_a_stranger_cannot_edit(
        self, authenticate, block, host, tenancy_factory, tenant, student_profile
    ):
        _prop, unit = block
        review = create_review(a_stay(tenancy_factory, unit, tenant), rating=4)

        with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
            response = authenticate(student_profile.user).patch(
                f"/api/v1/reviews/{review.pk}/", {"rating": 5}, format="json", HTTP_HOST=host
            )

        assert response.status_code == 404

    def test_a_frozen_review_is_refused(self, authenticate, block, host, tenancy_factory, tenant):
        from reviews.models import Review

        _prop, unit = block
        review = create_review(a_stay(tenancy_factory, unit, tenant), rating=4)
        Review.all_objects.filter(pk=review.pk).update(
            editable_until=dt.datetime.now(dt.UTC) - dt.timedelta(days=1)
        )

        with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
            response = authenticate(tenant).patch(
                f"/api/v1/reviews/{review.pk}/", {"rating": 5}, format="json", HTTP_HOST=host
            )

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "review_frozen"


class TestResponding:
    def test_the_owner_may_respond(self, authenticate, block, host, tenancy_factory, tenant):
        prop, unit = block
        review = create_review(a_stay(tenancy_factory, unit, tenant), rating=2)

        response = post(
            authenticate(prop.landlord.user),
            f"/api/v1/reviews/{review.pk}/response/",
            host,
            {"body": "The gate is fixed."},
        )

        assert response.status_code == 201

    def test_a_caretaker_may_not(
        self,
        authenticate,
        block,
        host,
        tenancy_factory,
        tenant,
        caretaker_assignment_factory,
        landlord,
    ):
        """ADR-003: a caretaker can confirm that somebody lived somewhere, but
        speaking for the business in public is the owner's own act."""
        prop, unit = block
        review = create_review(a_stay(tenancy_factory, unit, tenant), rating=2)
        caretaker = caretaker_assignment_factory(property=prop)

        response = post(
            authenticate(caretaker.user),
            f"/api/v1/reviews/{review.pk}/response/",
            host,
            {"body": "Speaking for the landlord."},
        )

        assert response.status_code == 403

    def test_a_stranger_may_not(
        self, authenticate, block, host, tenancy_factory, tenant, student_profile
    ):
        _prop, unit = block
        review = create_review(a_stay(tenancy_factory, unit, tenant), rating=2)

        response = post(
            authenticate(student_profile.user),
            f"/api/v1/reviews/{review.pk}/response/",
            host,
            {"body": "Not mine."},
        )

        assert response.status_code == 403

    def test_only_one_response_ever(self, authenticate, block, host, tenancy_factory, tenant):
        prop, unit = block
        review = create_review(a_stay(tenancy_factory, unit, tenant), rating=2)
        client = authenticate(prop.landlord.user)
        post(client, f"/api/v1/reviews/{review.pk}/response/", host, {"body": "Fixed."})

        response = post(
            client, f"/api/v1/reviews/{review.pk}/response/", host, {"body": "Actually no."}
        )

        assert response.status_code >= 400


# ---------------------------------------------------------------------------
# Reviews of properties you manage
# ---------------------------------------------------------------------------


class TestTheManagedReviewList:
    """The portal's reply surface.

    Two questions, and the endpoint has to answer both honestly: which reviews
    are mine to see, and which of them still want something from me.
    """

    URL = "/api/v1/reviews/manage/"

    @staticmethod
    def review_of(prop, review_factory, unit_factory, tenancy_factory, **kwargs):
        """A published review of one property, through a real tenancy.

        `ReviewFactory` hangs off a tenancy rather than a property, which is
        the model being honest: a review exists because a stay did (ADR-004).
        """
        unit = unit_factory(property=prop)
        tenancy = tenancy_factory(unit=unit)
        return review_factory(tenancy=tenancy, **kwargs)

    @staticmethod
    def joined(prop, university, campus_factory, campus_distance_factory):
        """Join a property to the tenant.

        Without this the endpoint returns nothing and the test reads as a
        permissions failure. The join is what makes a property exist for a
        university at all (ADR-002) -- it is not a detail of the fixture.
        """
        campus_distance_factory(
            property=prop,
            university=university,
            campus=campus_factory(university=university, is_main=True),
        )
        return prop

    @staticmethod
    def respond_to(review, author):
        from reviews.models import ReviewResponse

        return ReviewResponse.all_objects.create(
            review=review, author=author, body="The tank was replaced in June."
        )

    def test_a_landlord_sees_reviews_of_their_own_properties(
        self,
        authenticate,
        landlord,
        host,
        review_factory,
        property_factory,
        landlord_profile,
        unit_factory,
        tenancy_factory,
        university,
        campus_factory,
        campus_distance_factory,
    ):
        prop = property_factory(landlord=landlord_profile, status=PropertyStatus.PUBLISHED)
        self.joined(prop, university, campus_factory, campus_distance_factory)
        mine = self.review_of(prop, review_factory, unit_factory, tenancy_factory)
        review_factory()

        response = get(authenticate(landlord), self.URL, host)

        assert response.status_code == 200
        assert [row["id"] for row in response.data["results"]] == [mine.pk]

    def test_a_caretaker_sees_the_property_they_are_assigned_to(
        self,
        authenticate,
        host,
        review_factory,
        property_factory,
        caretaker_assignment_factory,
        unit_factory,
        tenancy_factory,
        university,
        campus_factory,
        campus_distance_factory,
    ):
        prop = property_factory(status=PropertyStatus.PUBLISHED)
        self.joined(prop, university, campus_factory, campus_distance_factory)
        theirs = self.review_of(prop, review_factory, unit_factory, tenancy_factory)
        assignment = caretaker_assignment_factory(property=prop)

        response = get(authenticate(assignment.user), self.URL, host)

        assert [row["id"] for row in response.data["results"]] == [theirs.pk]

    def test_a_caretaker_still_cannot_reply(
        self,
        authenticate,
        host,
        review_factory,
        property_factory,
        caretaker_assignment_factory,
        unit_factory,
        tenancy_factory,
        university,
        campus_factory,
        campus_distance_factory,
    ):
        """Reading and replying are different acts. A caretaker can confirm
        somebody lived somewhere; speaking for the business in public is the
        owner's own (ADR-003). Enforced at the endpoint, not by hiding a
        button."""
        prop = property_factory(status=PropertyStatus.PUBLISHED)
        self.joined(prop, university, campus_factory, campus_distance_factory)
        review = self.review_of(prop, review_factory, unit_factory, tenancy_factory)
        assignment = caretaker_assignment_factory(property=prop)

        client = authenticate(assignment.user)
        assert get(client, self.URL, host).status_code == 200

        with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
            reply = client.post(
                f"/api/v1/reviews/{review.pk}/response/",
                {"body": "Not my place to say this."},
                format="json",
                HTTP_HOST=host,
            )

        assert reply.status_code == 403

    def test_a_student_sees_nothing_of_their_own_landlord(
        self, tenant_client, host, review_factory
    ):
        review_factory()

        response = get(tenant_client, self.URL, host)

        assert response.data["results"] == []

    def test_unanswered_is_filterable(
        self,
        authenticate,
        landlord,
        host,
        review_factory,
        property_factory,
        landlord_profile,
        unit_factory,
        tenancy_factory,
        university,
        campus_factory,
        campus_distance_factory,
    ):
        """A landlord with nine properties has one question worth asking of
        this list. Making them page through answered reviews to find it is how
        a reply surface goes unused."""
        prop = property_factory(landlord=landlord_profile, status=PropertyStatus.PUBLISHED)
        self.joined(prop, university, campus_factory, campus_distance_factory)
        unanswered = self.review_of(prop, review_factory, unit_factory, tenancy_factory)
        answered = self.review_of(prop, review_factory, unit_factory, tenancy_factory)
        self.respond_to(answered, landlord)

        client = authenticate(landlord)

        pending = get(client, self.URL, host, answered="false")
        assert [row["id"] for row in pending.data["results"]] == [unanswered.pk]

        done = get(client, self.URL, host, answered="true")
        assert [row["id"] for row in done.data["results"]] == [answered.pk]

    def test_a_disputed_review_is_returned_with_its_plain_annotation(
        self,
        authenticate,
        landlord,
        host,
        review_factory,
        property_factory,
        landlord_profile,
        unit_factory,
        tenancy_factory,
        university,
        campus_factory,
        campus_distance_factory,
    ):
        """The landlord's own view is not an exception to the annotation rule.

        A landlord who sees their disputed reviews greyed out in their portal
        learns that disputing is how you make a review look less credible --
        which is the veto ADR-004 removed, restored as a habit.
        """
        prop = property_factory(landlord=landlord_profile, status=PropertyStatus.PUBLISHED)
        self.joined(prop, university, campus_factory, campus_distance_factory)
        review = self.review_of(prop, review_factory, unit_factory, tenancy_factory)

        response = get(authenticate(landlord), self.URL, host)

        rows = {row["id"]: row for row in response.data["results"]}
        assert review.pk in rows
        # Present as a field either way -- null when undisputed -- so the
        # client renders one shape rather than branching on absence.
        assert "dispute_annotation" in rows[review.pk]

    def test_it_does_not_scale_with_rows(
        self,
        authenticate,
        landlord,
        host,
        review_factory,
        property_factory,
        landlord_profile,
        unit_factory,
        tenancy_factory,
        university,
        campus_factory,
        campus_distance_factory,
    ):
        """One query per review is a portal that works for the landlord with
        two properties and falls over for the one worth keeping."""
        prop = property_factory(landlord=landlord_profile, status=PropertyStatus.PUBLISHED)
        self.joined(prop, university, campus_factory, campus_distance_factory)
        for _ in range(6):
            self.review_of(prop, review_factory, unit_factory, tenancy_factory)

        client = authenticate(landlord)
        with CaptureQueriesContext(connection) as queries:
            get(client, self.URL, host)

        assert len(queries) < 15, [q["sql"][:90] for q in queries]
