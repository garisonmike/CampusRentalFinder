"""
The landlord and caretaker write surface (ADR-002, ADR-003).

Two things are being asserted here and they are not the same thing.

**The rules hold.** Vacancy is stamped, publication is gated, a partial photo
order is refused. Those live in `properties/services.py` and are tested
directly in `test_occupancy.py` and `test_properties.py`.

**The authority is checked at the URL.** That is what this file adds. A
caretaker prohibition enforced only in a service function says nothing about
what happens when somebody POSTs to the endpoint -- and the endpoint is the
part attached to the internet. Every negative case below is a request, with a
status code, not a call to a Python function.

The caretaker cases matter most. A caretaker is a real person the landlord
hired for a narrower job (ADR-003), and each permission is separately
delegable precisely so that "can upload photos" does not silently become "can
state how many rooms are free".
"""

from __future__ import annotations

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from accounts.capabilities import CaretakerPermission
from properties.constants import PropertyStatus, VacancyFreshness
from properties.models import Property, Unit, UnitPhoto
from properties.services import vacancy_freshness

from .factories import UserFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def host(university):
    return f"{university.subdomain}.example.co.ke"


def request_json(client, method, url, host, data=None, **extra):
    with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
        return getattr(client, method)(url, data or {}, format="json", HTTP_HOST=host, **extra)


def upload(client, url, host, files):
    with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
        return client.post(url, files, format="multipart", HTTP_HOST=host)


def png_bytes() -> bytes:
    """A real 1x1 PNG, because `ImageField` opens what it is given."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (1, 1), "white").save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def owned(property_factory, landlord_profile, unit_factory):
    """A draft property belonging to `landlord`, with one unit.

    Unpinned, because that is the state a property is actually created in and
    the one the publish gate exists for. The factory pins coordinates by
    default -- convenient for read tests, and exactly the fixture shape that
    would let the gate go untested here.
    """
    prop = property_factory(
        landlord=landlord_profile,
        status=PropertyStatus.DRAFT,
        latitude=None,
        longitude=None,
    )
    unit = unit_factory(property=prop, total_count=40, vacant_count=0)
    return prop, unit


@pytest.fixture
def caretaker_for(caretaker_assignment_factory):
    """A caretaker on a property, holding exactly the permissions named."""

    def build(prop, *permissions):
        assignment = caretaker_assignment_factory(
            property=prop, permissions=list(permissions), user=UserFactory()
        )
        return assignment.user

    return build


# ---------------------------------------------------------------------------
# Creating and editing a property
# ---------------------------------------------------------------------------


class TestCreatingAProperty:
    def test_a_landlord_can_create_one(self, landlord_client, host):
        response = request_json(
            landlord_client,
            "post",
            "/api/v1/properties/manage/new/",
            host,
            {
                "name": "Wendani Court",
                "property_type": "bedsitter",
                "county": "nairobi",
                "town": "Kahawa",
                "estate": "Kahawa Wendani",
            },
        )

        assert response.status_code == 201
        assert response.data["slug"] == "wendani-court"

    def test_it_is_always_a_draft(self, landlord_client, host):
        """Publishing has a gate. A property created straight into PUBLISHED
        would either bypass it or fail half way through a create."""
        response = request_json(
            landlord_client,
            "post",
            "/api/v1/properties/manage/new/",
            host,
            {
                "name": "Sunrise Apartments",
                "property_type": "bedsitter",
                "county": "nairobi",
                "town": "Kahawa",
                "estate": "Kahawa Sukari",
                "status": PropertyStatus.PUBLISHED,
                "published_at": "2020-01-01T00:00:00Z",
            },
        )

        assert response.status_code == 201
        from properties.models import Property

        created = Property.all_objects.get(slug="sunrise-apartments")
        assert created.status == PropertyStatus.DRAFT
        assert created.published_at is None

    def test_a_student_cannot(self, tenant_client, host):
        response = request_json(
            tenant_client,
            "post",
            "/api/v1/properties/manage/new/",
            host,
            {"name": "Nice try", "property_type": "bedsitter", "county": "nairobi"},
        )

        assert response.status_code == 403

    def test_two_landlords_may_both_have_a_sunrise_apartments(
        self, landlord_client, host, property_factory
    ):
        """Suffixed, not refused. Refusing the second would be the platform
        telling a landlord their building has the wrong name."""
        property_factory(name="Sunrise Apartments", slug="sunrise-apartments")

        response = request_json(
            landlord_client,
            "post",
            "/api/v1/properties/manage/new/",
            host,
            {
                "name": "Sunrise Apartments",
                "property_type": "bedsitter",
                "county": "nairobi",
                "town": "Kahawa",
                "estate": "Kahawa Sukari",
            },
        )

        assert response.status_code == 201
        assert response.data["slug"] == "sunrise-apartments-2"


class TestEditingAProperty:
    def test_the_owner_may(self, landlord_client, host, owned):
        prop, _unit = owned

        response = request_json(
            landlord_client,
            "patch",
            f"/api/v1/properties/manage/{prop.slug}/",
            host,
            {"town": "Ruiru"},
        )

        assert response.status_code == 200
        assert response.data["town"] == "Ruiru"

    def test_another_landlord_may_not(self, authenticate, host, owned, landlord_profile_factory):
        prop, _unit = owned
        other = UserFactory()
        landlord_profile_factory(user=other)

        response = request_json(
            authenticate(other),
            "patch",
            f"/api/v1/properties/manage/{prop.slug}/",
            host,
            {"town": "Ruiru"},
        )

        assert response.status_code == 403

    def test_a_caretaker_may_not_edit_the_property_itself(
        self, authenticate, host, owned, caretaker_for
    ):
        """Not in `CaretakerPermission` at all. A caretaker manages rooms, not
        the building's identity."""
        prop, _unit = owned
        caretaker = caretaker_for(prop, *CaretakerPermission.values)

        response = request_json(
            authenticate(caretaker),
            "patch",
            f"/api/v1/properties/manage/{prop.slug}/",
            host,
            {"town": "Ruiru"},
        )

        assert response.status_code == 403

    def test_a_published_slug_does_not_move(
        self, landlord_client, host, owned, campus_distance_factory, university
    ):
        """The URL is in somebody's saved list and in messages the landlord
        already sent. Renaming changes what it is called, not where it lives."""
        prop, _unit = owned
        prop.latitude, prop.longitude = -1.18, 36.93
        prop.save()
        campus_distance_factory(property=prop, university=university)
        request_json(
            landlord_client, "post", f"/api/v1/properties/manage/{prop.slug}/publication/", host
        )

        response = request_json(
            landlord_client,
            "patch",
            f"/api/v1/properties/manage/{prop.slug}/",
            host,
            {"name": "Completely Different Name"},
        )

        assert response.status_code == 200
        assert response.data["slug"] == prop.slug
        assert response.data["name"] == "Completely Different Name"


# ---------------------------------------------------------------------------
# Publication
# ---------------------------------------------------------------------------


class TestPublishing:
    def test_the_coordinates_gate_runs_at_the_boundary(self, landlord_client, host, owned):
        """Not only in the service. An unpinned property cannot join a campus,
        and the join is what makes a listing visible -- so publishing one
        produces a listing the landlord can see and nobody else can, which
        looks exactly like low demand."""
        prop, _unit = owned
        assert prop.latitude is None

        response = request_json(
            landlord_client, "post", f"/api/v1/properties/manage/{prop.slug}/publication/", host
        )

        assert response.status_code == 400
        assert "latitude" in str(response.data).lower()
        prop.refresh_from_db()
        assert prop.status == PropertyStatus.DRAFT

    def test_a_pinned_property_with_a_campus_publishes(
        self, landlord_client, host, owned, campus_distance_factory, university
    ):
        prop, _unit = owned
        prop.latitude, prop.longitude = -1.18, 36.93
        prop.save()
        campus_distance_factory(property=prop, university=university)

        response = request_json(
            landlord_client, "post", f"/api/v1/properties/manage/{prop.slug}/publication/", host
        )

        assert response.status_code == 200
        prop.refresh_from_db()
        assert prop.status == PropertyStatus.PUBLISHED
        assert prop.published_at is not None

    def test_a_caretaker_may_not_publish(self, authenticate, host, owned, caretaker_for):
        """Publishing puts a building on the internet under the owner's name."""
        prop, _unit = owned
        caretaker = caretaker_for(prop, *CaretakerPermission.values)

        response = request_json(
            authenticate(caretaker),
            "post",
            f"/api/v1/properties/manage/{prop.slug}/publication/",
            host,
        )

        assert response.status_code == 403

    def test_unpublishing_is_a_status_change_not_a_delete(
        self, landlord_client, host, owned, campus_distance_factory, university
    ):
        prop, _unit = owned
        prop.latitude, prop.longitude = -1.18, 36.93
        prop.save()
        campus_distance_factory(property=prop, university=university)
        request_json(
            landlord_client, "post", f"/api/v1/properties/manage/{prop.slug}/publication/", host
        )

        with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
            response = landlord_client.delete(
                f"/api/v1/properties/manage/{prop.slug}/publication/", HTTP_HOST=host
            )

        assert response.status_code == 200
        prop.refresh_from_db()
        assert prop.status == PropertyStatus.DRAFT


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------


class TestUnits:
    def test_a_caretaker_with_manage_units_may_add_one(
        self, authenticate, host, owned, caretaker_for
    ):
        prop, _unit = owned
        caretaker = caretaker_for(prop, CaretakerPermission.MANAGE_UNITS)

        response = request_json(
            authenticate(caretaker),
            "post",
            f"/api/v1/properties/manage/{prop.slug}/units/",
            host,
            {
                "label": "Block C",
                "unit_type": "bedsitter",
                "rent_kes": "8500.00",
                "total_count": 12,
                "furnished": "unfurnished",
                "bedrooms": 0,
                "min_stay_months": 4,
            },
        )

        assert response.status_code == 201
        assert response.data["vacant_count"] == 0
        # Nobody has stated anything yet, and the API says so rather than
        # implying a fresh zero.
        assert response.data["vacancy_freshness"] == VacancyFreshness.UNKNOWN

    def test_a_caretaker_without_it_may_not(self, authenticate, host, owned, caretaker_for):
        prop, _unit = owned
        caretaker = caretaker_for(prop, CaretakerPermission.MANAGE_PHOTOS)

        response = request_json(
            authenticate(caretaker),
            "post",
            f"/api/v1/properties/manage/{prop.slug}/units/",
            host,
            {"label": "Block C", "unit_type": "bedsitter", "rent_kes": "8500.00"},
        )

        assert response.status_code == 403

    def test_a_unit_edit_cannot_set_the_vacancy_count(self, landlord_client, host, owned):
        """Refused by name rather than dropped. A silently ignored field is how
        somebody comes to believe they set something they did not, and this is
        the field where that belief becomes a listing that lies."""
        prop, unit = owned

        response = request_json(
            landlord_client,
            "patch",
            f"/api/v1/properties/manage/{prop.slug}/units/{unit.pk}/",
            host,
            {"vacant_count": 9},
        )

        assert response.status_code == 400
        assert "vacant_count" in str(response.data)
        unit.refresh_from_db()
        assert unit.vacant_count == 0
        assert unit.vacant_count_updated_at is None


# ---------------------------------------------------------------------------
# Vacancy — the endpoint the held job was waiting for
# ---------------------------------------------------------------------------


class TestVacancy:
    def test_stating_it_stamps_the_author(self, landlord_client, host, owned, landlord):
        prop, unit = owned

        response = request_json(
            landlord_client,
            "patch",
            f"/api/v1/properties/manage/{prop.slug}/units/{unit.pk}/vacancy/",
            host,
            {"vacant_count": 6},
        )

        assert response.status_code == 200
        assert response.data["vacant_count"] == 6
        assert response.data["vacancy_freshness"] == VacancyFreshness.FRESH
        assert response.data["vacancy_age_days"] == 0
        unit.refresh_from_db()
        assert unit.vacant_count_updated_by == landlord

    def test_a_caretaker_with_manage_vacancy_may_state_it(
        self, authenticate, host, owned, caretaker_for
    ):
        """And the stamp records that it was them. A caretaker walking the
        block and a landlord in an office are different kinds of evidence."""
        prop, unit = owned
        caretaker = caretaker_for(prop, CaretakerPermission.MANAGE_VACANCY)

        response = request_json(
            authenticate(caretaker),
            "patch",
            f"/api/v1/properties/manage/{prop.slug}/units/{unit.pk}/vacancy/",
            host,
            {"vacant_count": 3},
        )

        assert response.status_code == 200
        unit.refresh_from_db()
        assert unit.vacant_count_updated_by == caretaker

    def test_a_caretaker_with_photos_only_may_not(self, authenticate, host, owned, caretaker_for):
        """The whole reason it is a separate permission: uploading photos is
        not the same trust as stating the number people travel on."""
        prop, unit = owned
        caretaker = caretaker_for(prop, CaretakerPermission.MANAGE_PHOTOS)

        response = request_json(
            authenticate(caretaker),
            "patch",
            f"/api/v1/properties/manage/{prop.slug}/units/{unit.pk}/vacancy/",
            host,
            {"vacant_count": 40},
        )

        assert response.status_code == 403
        unit.refresh_from_db()
        assert unit.vacant_count == 0

    def test_more_free_than_exist_is_refused(self, landlord_client, host, owned):
        prop, unit = owned

        response = request_json(
            landlord_client,
            "patch",
            f"/api/v1/properties/manage/{prop.slug}/units/{unit.pk}/vacancy/",
            host,
            {"vacant_count": 41},
        )

        assert response.status_code == 400

    def test_restating_refreshes_a_stale_count(self, landlord_client, host, owned):
        """The endpoint the held job exists to point at."""
        prop, unit = owned
        Unit.all_objects.filter(pk=unit.pk).update(
            vacant_count=6, vacant_count_updated_at="2020-01-01T00:00:00Z"
        )
        unit.refresh_from_db()
        assert vacancy_freshness(unit) == VacancyFreshness.STALE

        response = request_json(
            landlord_client,
            "patch",
            f"/api/v1/properties/manage/{prop.slug}/units/{unit.pk}/vacancy/",
            host,
            {"vacant_count": 2},
        )

        assert response.status_code == 200
        unit.refresh_from_db()
        assert vacancy_freshness(unit) == VacancyFreshness.FRESH


class TestAvailability:
    def test_a_caretaker_with_set_availability_may_take_a_unit_off_the_market(
        self, authenticate, host, owned, caretaker_for
    ):
        prop, unit = owned
        caretaker = caretaker_for(prop, CaretakerPermission.SET_AVAILABILITY)

        response = request_json(
            authenticate(caretaker),
            "patch",
            f"/api/v1/properties/manage/{prop.slug}/units/{unit.pk}/availability/",
            host,
            {"is_active": False},
        )

        assert response.status_code == 200
        unit.refresh_from_db()
        assert unit.is_active is False

    def test_that_caretaker_still_may_not_change_the_rent(
        self, authenticate, host, owned, caretaker_for
    ):
        """The distinction the two permissions exist to draw."""
        prop, unit = owned
        caretaker = caretaker_for(prop, CaretakerPermission.SET_AVAILABILITY)

        response = request_json(
            authenticate(caretaker),
            "patch",
            f"/api/v1/properties/manage/{prop.slug}/units/{unit.pk}/",
            host,
            {"rent_kes": "1.00"},
        )

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Photos
# ---------------------------------------------------------------------------


class TestPhotos:
    def upload_one(self, client, host, prop, unit, name="room.png"):
        return upload(
            client,
            f"/api/v1/properties/manage/{prop.slug}/units/{unit.pk}/photos/",
            host,
            {
                "image": SimpleUploadedFile(name, png_bytes(), content_type="image/png"),
                "caption": "The shared kitchen",
            },
        )

    def test_uploading_stores_it_pending(self, landlord_client, host, owned):
        """Variants come from a background job (ADR-007), so a fresh photo has
        none and the API serves the original."""
        prop, unit = owned

        response = self.upload_one(landlord_client, host, prop, unit)

        assert response.status_code == 201
        assert response.data["processing_status"] == "pending"
        assert response.data["url"]

    def test_the_first_photo_becomes_the_cover(self, landlord_client, host, owned):
        """A unit whose only photo is not primary has no cover, and the card
        then says 'No photos yet' beside a unit that plainly has one."""
        prop, unit = owned

        response = self.upload_one(landlord_client, host, prop, unit)

        assert response.data["is_primary"] is True

    def test_a_caretaker_with_manage_photos_may_upload(
        self, authenticate, host, owned, caretaker_for
    ):
        prop, unit = owned
        caretaker = caretaker_for(prop, CaretakerPermission.MANAGE_PHOTOS)

        response = self.upload_one(authenticate(caretaker), host, prop, unit)

        assert response.status_code == 201

    def test_a_caretaker_without_it_may_not(self, authenticate, host, owned, caretaker_for):
        prop, unit = owned
        caretaker = caretaker_for(prop, CaretakerPermission.MANAGE_VACANCY)

        response = self.upload_one(authenticate(caretaker), host, prop, unit)

        assert response.status_code == 403

    def test_a_pdf_is_refused_by_content_type(self, landlord_client, host, owned):
        """An extension is whatever the client typed. The resize step is a
        decoder pointed at whatever arrives."""
        prop, unit = owned

        response = upload(
            landlord_client,
            f"/api/v1/properties/manage/{prop.slug}/units/{unit.pk}/photos/",
            host,
            {"image": SimpleUploadedFile("room.png", b"%PDF-1.4 not an image", "application/pdf")},
        )

        assert response.status_code == 400

    def test_a_partial_reorder_is_refused(self, landlord_client, host, owned):
        """A caller sending three of five ids has a stale view of the unit, and
        applying it would silently discard the other two."""
        prop, unit = owned
        first = self.upload_one(landlord_client, host, prop, unit, "a.png").data
        self.upload_one(landlord_client, host, prop, unit, "b.png")

        response = request_json(
            landlord_client,
            "put",
            f"/api/v1/properties/manage/{prop.slug}/units/{unit.pk}/photos/order/",
            host,
            {"photo_ids": [first["id"]]},
        )

        assert response.status_code == 400

    def test_reordering_moves_the_cover(self, landlord_client, host, owned):
        prop, unit = owned
        first = self.upload_one(landlord_client, host, prop, unit, "a.png").data
        second = self.upload_one(landlord_client, host, prop, unit, "b.png").data

        response = request_json(
            landlord_client,
            "put",
            f"/api/v1/properties/manage/{prop.slug}/units/{unit.pk}/photos/order/",
            host,
            {"photo_ids": [second["id"], first["id"]]},
        )

        assert response.status_code == 200
        assert response.data[0]["id"] == second["id"]
        assert response.data[0]["is_primary"] is True
        assert response.data[1]["is_primary"] is False

    def test_deleting_the_cover_promotes_the_next(self, landlord_client, host, owned):
        prop, unit = owned
        first = self.upload_one(landlord_client, host, prop, unit, "a.png").data
        second = self.upload_one(landlord_client, host, prop, unit, "b.png").data

        with override_settings(ALLOWED_HOSTS=["*"], SITE_DOMAIN="example.co.ke"):
            response = landlord_client.delete(
                f"/api/v1/properties/manage/{prop.slug}/units/{unit.pk}/photos/{first['id']}/",
                HTTP_HOST=host,
            )

        assert response.status_code == 204
        assert UnitPhoto.all_objects.get(pk=second["id"]).is_primary is True


# ---------------------------------------------------------------------------
# What a manager can see
# ---------------------------------------------------------------------------


class TestTheManagedList:
    def test_a_landlord_sees_their_own_drafts(self, landlord_client, host, owned):
        """The difference from the public endpoint. A draft has no campus join
        yet, so it is invisible to every tenant-scoped read -- and this is
        where it lives until it is pinned."""
        prop, _unit = owned

        response = request_json(landlord_client, "get", "/api/v1/properties/manage/", host)

        assert response.status_code == 200
        assert [row["slug"] for row in response.data] == [prop.slug]

    def test_a_caretaker_sees_the_property_they_are_assigned_to(
        self, authenticate, host, owned, caretaker_for
    ):
        prop, _unit = owned
        caretaker = caretaker_for(prop, CaretakerPermission.MANAGE_PHOTOS)

        response = request_json(authenticate(caretaker), "get", "/api/v1/properties/manage/", host)

        assert [row["slug"] for row in response.data] == [prop.slug]

    def test_a_revoked_caretaker_sees_nothing(
        self, authenticate, host, owned, caretaker_assignment_factory
    ):
        """Revocation is a flag, not a delete, and it has to be immediate."""
        prop, _unit = owned
        user = UserFactory()
        # `revoked_at` is required by a check constraint when inactive: a
        # revocation with no timestamp is an audit trail that cannot say when.
        caretaker_assignment_factory(
            property=prop, user=user, is_active=False, revoked_at=timezone.now()
        )

        response = request_json(authenticate(user), "get", "/api/v1/properties/manage/", host)

        assert response.data == []

    def test_a_student_sees_nothing(self, tenant_client, host, owned):
        response = request_json(tenant_client, "get", "/api/v1/properties/manage/", host)

        assert response.data == []


# ---------------------------------------------------------------------------
# Query counts, at a size fixtures do not reach
# ---------------------------------------------------------------------------


class TestItDoesNotScaleWithRows:
    """One query per row renders correctly in a test and falls over live.

    These assertions exist because the seeded platform showed a real one:
    `/properties/manage/` issued **eighteen queries for six properties**, two
    per campus-distance row, because `CampusDistanceSerializer` renders
    `campus_name` and `university_name` and the view prefetched the distances
    without their relations. No fixture could show it -- they have one campus
    each, so the N+1 was worth two queries and looked like a constant.
    """

    def test_the_managed_list_is_flat_in_portfolio_size(
        self,
        landlord_client,
        host,
        property_factory,
        landlord_profile,
        unit_factory,
        campus_distance_factory,
        university,
        campus_factory,
    ):
        def measure(count: int) -> int:
            Property.all_objects.filter(landlord=landlord_profile).delete()
            campus = campus_factory(university=university)
            for index in range(count):
                prop = property_factory(landlord=landlord_profile, name=f"Block {index}")
                campus_distance_factory(property=prop, university=university, campus=campus)
                unit_factory(property=prop)

            with CaptureQueriesContext(connection) as queries:
                request_json(landlord_client, "get", "/api/v1/properties/manage/", host)
            return len(queries)

        two = measure(2)
        eight = measure(8)

        assert eight <= two + 1, (
            f"{two} queries for two properties, {eight} for eight. "
            f"That is {eight - two} extra queries for six extra rows."
        )

    def test_a_second_campus_costs_nothing(
        self,
        landlord_client,
        host,
        property_factory,
        landlord_profile,
        campus_distance_factory,
        university,
        campus_factory,
        unit_factory,
    ):
        """The exact shape the seed exposed. A university with two campuses is
        ordinary -- JKUAT has Juja and Karen -- and every property near both
        gets two distance rows."""
        prop = property_factory(landlord=landlord_profile)
        unit_factory(property=prop)
        for name in ("Main", "Second", "Third"):
            campus_distance_factory(
                property=prop,
                university=university,
                campus=campus_factory(university=university, name=name),
            )

        with CaptureQueriesContext(connection) as queries:
            response = request_json(landlord_client, "get", "/api/v1/properties/manage/", host)

        assert len(response.data[0]["campus_distances"]) == 3
        assert len(queries) < 12, [q["sql"][:80] for q in queries]
