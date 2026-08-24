"""
Contract tests for the API.

Written before the schema rewrite to record what the endpoints actually did, so
that every change to the contract would be a deliberate one. Several of them
pinned genuine defects, asserting the broken behaviour on purpose.

**Those assertions are being inverted as the rewrite fixes them.** Each such
test keeps its name and its history, and its docstring now records what the
defect was and which change closed it — deleting them would lose the evidence
that the bug existed and that it is gone.

Still pinned as broken, awaiting their phase of the rewrite:
  - the rental detail endpoint raises on an unresolved F() expression
  - review reporting is unreachable by anybody
  - the rentals list URL the old frontend called is the router root
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from rest_framework import status

from accounts.models import StudentProfile, User
from rentals.models import Rental, RentalFavorite, RentalInquiry
from reviews.models import Review, ReviewHelpfulness, ReviewReport
from tests.factories import (
    TEST_PASSWORD,
    LandlordFactory,
    RentalFactory,
    RentalImageFactory,
    ReviewFactory,
    TenantFactory,
)

pytestmark = pytest.mark.django_db


# ===========================================================================
# Authentication
# ===========================================================================


class TestRegistration:
    url = "/api/v1/auth/register/"

    def test_registers_a_tenant_and_returns_tokens(self, api_client):
        response = api_client.post(
            self.url,
            {
                "email": "wanjiku@students.ku.ac.ke",
                "password": "a-strong-password-42",
                "password_confirm": "a-strong-password-42",
                "first_name": "Wanjiku",
                "last_name": "Kamau",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()
        assert body["user"]["email"] == "wanjiku@students.ku.ac.ke"
        assert body["tokens"]["access"]
        assert body["tokens"]["refresh"]

    def test_rejects_mismatched_password_confirmation(self, api_client):
        response = api_client.post(
            self.url,
            {
                "email": "x@students.ku.ac.ke",
                "password": "a-strong-password-42",
                "password_confirm": "something-else-entirely",
                "first_name": "A",
                "last_name": "B",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rejects_a_duplicate_email(self, api_client, tenant):
        response = api_client.post(
            self.url,
            {
                "email": tenant.email,
                "password": "a-strong-password-42",
                "password_confirm": "a-strong-password-42",
                "first_name": "A",
                "last_name": "B",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_role_cannot_be_self_assigned_at_registration(self, api_client):
        """FIXED. Was: anyone could register as a landlord, or as an admin.

        The draft took ``user_type`` straight from the request body and never
        validated it, while the object-permission checks in rentals and reviews
        trusted that same field — so a self-declared admin could edit or delete
        any listing or review on the platform (docs/AUDIT.md §4.4).

        ADR-003 removed the field. Capability now comes from a profile another
        party creates, so there is nothing in a registration payload that can
        grant anything. A stray role field is ignored rather than honoured.
        """
        response = api_client.post(
            self.url,
            {
                "email": "self.declared@example.co.ke",
                "password": "a-strong-password-42",
                "password_confirm": "a-strong-password-42",
                "first_name": "Self",
                "last_name": "Declared",
                "user_type": "admin",
                "is_staff": True,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        body = response.json()["user"]

        assert "user_type" not in body
        assert body["capabilities"]["is_staff"] is False
        assert body["capabilities"]["is_landlord"] is False

        created = User.objects.get(email="self.declared@example.co.ke")
        assert created.is_staff is False
        assert not hasattr(created, "landlord_profile")

    def test_registering_on_a_tenant_host_creates_a_student_profile(self, api_client, university):
        """Signing up on a university's own subdomain says you are its student.

        Replaces the draft's UserProfile signal. That model was a grab-bag of
        notification preferences, social links and landlord business fields,
        created on every user whether or not any of it applied
        (docs/AUDIT.md §7).
        """
        api_client.post(
            self.url,
            {
                "email": "signal@students.ku.ac.ke",
                "password": "a-strong-password-42",
                "password_confirm": "a-strong-password-42",
                "first_name": "Sig",
                "last_name": "Nal",
            },
            format="json",
            HTTP_HOST=f"{university.subdomain}.example.co.ke",
        )

        profile = StudentProfile.all_objects.get(user__email="signal@students.ku.ac.ke")

        assert profile.university == university
        # Verification is off by default; the profile exists unverified.
        assert not profile.is_verified

    def test_registering_without_a_tenant_creates_no_student_profile(self, api_client):
        """The neutral host has no university to attach a student to."""
        api_client.post(
            self.url,
            {
                "email": "neutral@example.co.ke",
                "password": "a-strong-password-42",
                "password_confirm": "a-strong-password-42",
                "first_name": "Neu",
                "last_name": "Tral",
            },
            format="json",
            HTTP_HOST="www.example.co.ke",
        )

        assert not StudentProfile.all_objects.filter(user__email="neutral@example.co.ke").exists()


class TestLogin:
    url = "/api/v1/auth/login/"

    def test_logs_in_with_email_and_password(self, api_client, tenant):
        response = api_client.post(
            self.url,
            {"email": tenant.email, "password": TEST_PASSWORD},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["tokens"]["access"]

    def test_rejects_a_wrong_password(self, api_client, tenant):
        response = api_client.post(
            self.url,
            {"email": tenant.email, "password": "not-the-password"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rejects_an_unknown_email(self, api_client):
        response = api_client.post(
            self.url,
            {"email": "nobody@example.co.ke", "password": TEST_PASSWORD},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rejects_an_inactive_account(self, api_client, tenant):
        tenant.is_active = False
        tenant.save(update_fields=["is_active"])

        response = api_client.post(
            self.url,
            {"email": tenant.email, "password": TEST_PASSWORD},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestLogout:
    url = "/api/v1/auth/logout/"

    def test_blacklists_the_refresh_token(self, api_client, tenant):
        login = api_client.post(
            "/api/v1/auth/login/",
            {"email": tenant.email, "password": TEST_PASSWORD},
            format="json",
        ).json()
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login['tokens']['access']}")

        response = api_client.post(self.url, {"refresh": login["tokens"]["refresh"]}, format="json")

        assert response.status_code == status.HTTP_200_OK

    def test_reusing_a_blacklisted_refresh_token_fails(self, api_client, tenant):
        """This is the behaviour the missing token_blacklist app used to break.

        Before that app was added to INSTALLED_APPS, ``token.blacklist()``
        raised and logout silently reported "Invalid token" while the refresh
        token stayed usable forever.
        """
        login = api_client.post(
            "/api/v1/auth/login/",
            {"email": tenant.email, "password": TEST_PASSWORD},
            format="json",
        ).json()
        refresh = login["tokens"]["refresh"]
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login['tokens']['access']}")
        api_client.post(self.url, {"refresh": refresh}, format="json")

        retry = api_client.post("/api/v1/auth/token/refresh/", {"refresh": refresh}, format="json")
        assert retry.status_code == status.HTTP_401_UNAUTHORIZED

    def test_requires_a_refresh_token(self, tenant_client):
        response = tenant_client.post(self.url, {}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rejects_a_malformed_token(self, tenant_client):
        response = tenant_client.post(self.url, {"refresh": "nonsense"}, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_requires_authentication(self, api_client):
        response = api_client.post(self.url, {"refresh": "x"}, format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestCurrentUser:
    url = "/api/v1/auth/me/"

    def test_returns_the_authenticated_user(self, tenant_client, tenant):
        response = tenant_client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["email"] == tenant.email

    def test_rejects_anonymous_access(self, api_client):
        assert api_client.get(self.url).status_code == status.HTTP_401_UNAUTHORIZED


class TestProfile:
    url = "/api/v1/auth/profile/"

    def test_returns_the_profile(self, tenant_client, tenant):
        response = tenant_client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["email"] == tenant.email

    def test_patches_editable_fields(self, tenant_client, tenant):
        response = tenant_client.patch(self.url, {"first_name": "Renamed"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        tenant.refresh_from_db()
        assert tenant.first_name == "Renamed"

    def test_put_is_supported(self, tenant_client, tenant):
        """FIXED. Was: 405, because the view implemented only GET and PATCH.

        The previous frontend called PUT here, so every profile save failed
        silently (docs/AUDIT.md §5).
        """
        response = tenant_client.put(
            self.url, {"first_name": "Replaced", "last_name": "Wholly"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        tenant.refresh_from_db()
        assert tenant.first_name == "Replaced"

    def test_rejects_an_invalid_phone_number(self, tenant_client):
        response = tenant_client.patch(
            self.url, {"phone_number": "not a phone number"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


class TestProfilePreferencesIsGone:
    """The /auth/profile/preferences/ endpoint went with UserProfile.

    That model held notification preferences, social links and landlord
    business fields on every user row regardless of whether any of it applied
    (docs/AUDIT.md §7). ADR-003 splits capability into the profile models;
    preferences get their own small model if they are still wanted.
    """

    def test_the_endpoint_no_longer_exists(self, tenant_client):
        response = tenant_client.get("/api/v1/auth/profile/preferences/")

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestPasswordChange:
    url = "/api/v1/auth/password/change/"

    def test_changes_the_password(self, tenant_client, tenant):
        response = tenant_client.post(
            self.url,
            {
                "current_password": TEST_PASSWORD,
                "new_password": "a-brand-new-password-9",
                "new_password_confirm": "a-brand-new-password-9",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        tenant.refresh_from_db()
        assert tenant.check_password("a-brand-new-password-9")

    def test_rejects_a_wrong_current_password(self, tenant_client):
        response = tenant_client.post(
            self.url,
            {
                "current_password": "wrong",
                "new_password": "a-brand-new-password-9",
                "new_password_confirm": "a-brand-new-password-9",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_rejects_mismatched_confirmation(self, tenant_client):
        response = tenant_client.post(
            self.url,
            {
                "current_password": TEST_PASSWORD,
                "new_password": "a-brand-new-password-9",
                "new_password_confirm": "different-entirely-1",
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST


# ===========================================================================
# Admin surface -- guarded by IsAdminUser, i.e. is_staff, NOT user_type
# ===========================================================================


class TestAdminAccess:
    def test_platform_admin_user_type_does_not_grant_admin_api_access(
        self, authenticate, platform_admin
    ):
        """user_type='admin' is not is_staff, so DRF rejects it.

        Two parallel notions of "admin" exist in the draft. ADR-003 collapses
        them; recorded here because it is a live foot-gun.
        """
        client = authenticate(platform_admin)
        response = client.get("/api/v1/auth/admin/statistics/")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_staff_user_reaches_user_statistics(self, staff_client):
        response = staff_client.get("/api/v1/auth/admin/statistics/")

        assert response.status_code == status.HTTP_200_OK
        assert "total_users" in response.json()

    def test_staff_user_lists_users(self, staff_client, tenant):
        response = staff_client.get("/api/v1/auth/admin/users/")
        assert response.status_code == status.HTTP_200_OK

    def test_staff_user_retrieves_a_user(self, staff_client, tenant):
        response = staff_client.get(f"/api/v1/auth/admin/users/{tenant.pk}/")
        assert response.status_code == status.HTTP_200_OK

    def test_staff_user_toggles_active(self, staff_client, tenant):
        response = staff_client.post(f"/api/v1/auth/admin/users/{tenant.pk}/toggle_active/")

        assert response.status_code == status.HTTP_200_OK
        tenant.refresh_from_db()
        assert tenant.is_active is False

    def test_staff_user_verifies_a_landlord(self, staff_client, landlord):
        """Landlord verification is a platform-staff action.

        Student verification is a different flow entirely, run per-university
        (ADR-003), which is why this endpoint refuses a user with no landlord
        profile rather than setting a flag on User.
        """
        response = staff_client.post(f"/api/v1/auth/admin/verify/{landlord.pk}/")

        assert response.status_code == status.HTTP_200_OK
        landlord.landlord_profile.refresh_from_db()
        assert landlord.landlord_profile.verification_status == "verified"
        assert landlord.landlord_profile.verified_at is not None

    def test_verifying_a_user_with_no_landlord_profile_is_refused(self, staff_client, tenant):
        response = staff_client.post(f"/api/v1/auth/admin/verify/{tenant.pk}/")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_verifying_an_unknown_user_is_404(self, staff_client):
        response = staff_client.post("/api/v1/auth/admin/verify/999999/")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_anonymous_users_are_rejected(self, api_client):
        response = api_client.get("/api/v1/auth/admin/statistics/")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ===========================================================================
# Rentals
# ===========================================================================


class TestRentalListing:
    url = "/api/v1/rentals/properties/"

    def test_the_list_endpoint_lives_under_properties_not_the_app_root(
        self, api_client, tenant_client, rental
    ):
        """The frontend calls /rentals/, which is the router root, not a list.

        Anonymously it is a 401 (the router root inherits the default
        IsAuthenticated); authenticated it returns a directory of route names.
        Either way it never returns rentals, which is why RentalsPage and
        DashboardPage show nothing.
        """
        assert api_client.get("/api/v1/rentals/").status_code == status.HTTP_401_UNAUTHORIZED

        root = tenant_client.get("/api/v1/rentals/")
        assert root.status_code == status.HTTP_200_OK
        assert "properties" in root.json()

        assert api_client.get(self.url).status_code == status.HTTP_200_OK

    def test_listing_is_public(self, api_client, rental):
        response = api_client.get(self.url)

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["count"] == 1

    def test_the_list_response_is_paginated_not_a_bare_array(self, api_client, rental):
        """The frontend types this as Rental[] and calls .slice()/.map() on it."""
        body = api_client.get(self.url).json()

        assert isinstance(body, dict)
        assert set(body) >= {"count", "next", "previous", "results"}

    def test_filters_by_city(self, api_client, landlord):
        RentalFactory(landlord=landlord, city="Nairobi")
        RentalFactory(landlord=landlord, city="Eldoret")

        body = api_client.get(self.url, {"city": "eldoret"}).json()

        assert body["count"] == 1

    def test_filters_by_price_range(self, api_client, landlord):
        RentalFactory(landlord=landlord, price=Decimal("8000"))
        RentalFactory(landlord=landlord, price=Decimal("30000"))

        body = api_client.get(self.url, {"min_price": 10000, "max_price": 40000}).json()

        assert body["count"] == 1

    def test_filters_by_bedrooms_and_property_type(self, api_client, landlord):
        RentalFactory(landlord=landlord, bedrooms=1, property_type="studio")
        RentalFactory(landlord=landlord, bedrooms=3, property_type="house")

        body = api_client.get(self.url, {"bedrooms": 3, "property_type": "house"}).json()

        assert body["count"] == 1

    def test_filters_by_amenity_flags(self, api_client, landlord):
        RentalFactory(landlord=landlord, pets_allowed=True, parking_available=True)
        RentalFactory(landlord=landlord, pets_allowed=False, parking_available=False)

        body = api_client.get(
            self.url, {"pets_allowed": "true", "parking_available": "true"}
        ).json()

        assert body["count"] == 1

    def test_free_text_query_searches_title_and_description(self, api_client, landlord):
        RentalFactory(landlord=landlord, title="Bedsitter by the gate")
        RentalFactory(landlord=landlord, title="Two bedroom in town")

        body = api_client.get(self.url, {"query": "bedsitter"}).json()

        assert body["count"] == 1

    def test_ordering_by_price(self, api_client, landlord):
        RentalFactory(landlord=landlord, price=Decimal("30000"))
        RentalFactory(landlord=landlord, price=Decimal("8000"))

        results = api_client.get(self.url, {"ordering": "price"}).json()["results"]

        assert [r["price"] for r in results] == ["8000.00", "30000.00"]

    def test_bounding_box_search_accepts_lat_lng_radius(self, api_client, rental):
        body = api_client.get(self.url, {"latitude": -1.28, "longitude": 36.81, "radius": 5}).json()

        assert body["count"] == 1

    def test_detail_crashes_for_every_visitor_who_is_not_the_owner(self, api_client, rental):
        """The rental detail endpoint is broken -- a hard 500 on every request.

        ``Rental.increment_views()`` assigns an ``F()`` expression to
        ``self.views_count`` and never refreshes the instance, so the very next
        line -- serializing that same instance -- hits an unresolved
        CombinedExpression and raises TypeError.

        This is the most-visited endpoint on the site. See docs/AUDIT.md.
        """
        with pytest.raises(TypeError, match="CombinedExpression"):
            api_client.get(f"{self.url}{rental.pk}/")

    def test_detail_works_for_the_owning_landlord(self, landlord_client, rental):
        """The owner path skips increment_views(), so it is the only one that works."""
        response = landlord_client.get(f"{self.url}{rental.pk}/")

        assert response.status_code == status.HTTP_200_OK
        rental.refresh_from_db()
        assert rental.views_count == 0

    def test_detail_of_a_missing_rental_is_404(self, api_client):
        assert api_client.get(f"{self.url}999999/").status_code == status.HTTP_404_NOT_FOUND


class TestRentalWrites:
    url = "/api/v1/rentals/properties/"

    def _payload(self, **overrides):
        payload = {
            "title": "Clean bedsitter, 10 minutes from the gate",
            "description": "Water and electricity included. Secure compound.",
            "property_type": "studio",
            "price": "9500.00",
            "address": "Kenyatta Road",
            "city": "Nairobi",
            "state": "Nairobi County",
            "zip_code": "00100",
            "bedrooms": 1,
            "bathrooms": 1,
            "available_from": str(dt.date.today() + dt.timedelta(days=7)),
        }
        payload.update(overrides)
        return payload

    def test_a_landlord_can_create_a_listing(self, landlord_client, landlord):
        response = landlord_client.post(self.url, self._payload(), format="json")

        assert response.status_code == status.HTTP_201_CREATED
        assert Rental.objects.filter(landlord=landlord).count() == 1

    def test_a_tenant_cannot_create_a_listing(self, tenant_client):
        response = tenant_client.post(self.url, self._payload(), format="json")
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_an_anonymous_user_cannot_create_a_listing(self, api_client):
        response = api_client.post(self.url, self._payload(), format="json")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_a_past_available_from_date_is_rejected(self, landlord_client):
        response = landlord_client.post(
            self.url,
            self._payload(available_from=str(dt.date.today() - dt.timedelta(days=1))),
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_landlord_can_update_their_own_listing(self, landlord_client, rental):
        response = landlord_client.patch(
            f"{self.url}{rental.pk}/", {"title": "Updated title"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        rental.refresh_from_db()
        assert rental.title == "Updated title"

    def test_a_landlord_cannot_update_someone_elses_listing(self, authenticate, rental):
        intruder = LandlordFactory()
        client = authenticate(intruder)

        response = client.patch(f"{self.url}{rental.pk}/", {"title": "Hijacked"}, format="json")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_landlord_can_delete_their_own_listing(self, landlord_client, rental):
        response = landlord_client.delete(f"{self.url}{rental.pk}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Rental.objects.filter(pk=rental.pk).exists()

    def test_my_properties_returns_only_the_callers_listings(self, landlord_client, rental):
        RentalFactory(landlord=LandlordFactory())

        response = landlord_client.get(f"{self.url}my_properties/")

        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()) == 1


class TestFavourites:
    url = "/api/v1/rentals/properties/"

    def test_a_tenant_can_favourite_and_unfavourite(self, tenant_client, tenant, rental):
        added = tenant_client.post(f"{self.url}{rental.pk}/toggle_favorite/")
        assert added.status_code == status.HTTP_201_CREATED
        assert RentalFavorite.objects.filter(user=tenant, rental=rental).exists()

        removed = tenant_client.post(f"{self.url}{rental.pk}/toggle_favorite/")
        assert removed.status_code == status.HTTP_200_OK
        assert not RentalFavorite.objects.filter(user=tenant, rental=rental).exists()

    def test_favourites_list_returns_the_saved_rentals(self, tenant_client, tenant, rental):
        tenant_client.post(f"{self.url}{rental.pk}/toggle_favorite/")

        response = tenant_client.get(f"{self.url}favorites/")

        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()) == 1

    def test_favouriting_requires_authentication(self, api_client, rental):
        response = api_client.post(f"{self.url}{rental.pk}/toggle_favorite/")
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


class TestRentalImages:
    url = "/api/v1/rentals/images/"

    def test_images_are_listed_and_filterable_by_rental(self, tenant_client, rental):
        RentalImageFactory(rental=rental)

        response = tenant_client.get(self.url, {"rental_id": rental.pk})

        assert response.status_code == status.HTTP_200_OK

    def test_setting_a_primary_image_demotes_the_others(self, landlord_client, rental):
        first = RentalImageFactory(rental=rental, is_primary=True, order=0)
        second = RentalImageFactory(rental=rental, is_primary=False, order=1)

        response = landlord_client.post(f"{self.url}{second.pk}/set_primary/")

        assert response.status_code == status.HTTP_200_OK
        first.refresh_from_db()
        second.refresh_from_db()
        assert second.is_primary is True
        assert first.is_primary is False


class TestInquiries:
    url = "/api/v1/rentals/inquiries/"

    def test_a_tenant_can_send_an_inquiry(self, tenant_client, tenant, rental):
        response = tenant_client.post(
            self.url,
            {"rental": rental.pk, "message": "Is the bedsitter still vacant?"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert RentalInquiry.objects.filter(tenant=tenant, rental=rental).exists()

    def test_a_tenant_sees_only_their_own_inquiries(self, tenant_client, tenant, rental):
        RentalInquiry.objects.create(rental=rental, tenant=tenant, message="mine")
        RentalInquiry.objects.create(
            rental=rental, tenant=TenantFactory(), message="someone else's"
        )

        body = tenant_client.get(self.url).json()

        assert body["count"] == 1

    def test_a_landlord_sees_inquiries_on_their_own_listings(self, landlord_client, rental, tenant):
        RentalInquiry.objects.create(rental=rental, tenant=tenant, message="hello")

        body = landlord_client.get(self.url).json()

        assert body["count"] == 1

    def test_a_landlord_can_reply_once(self, landlord_client, rental, tenant):
        inquiry = RentalInquiry.objects.create(rental=rental, tenant=tenant, message="hello")

        response = landlord_client.post(
            f"{self.url}{inquiry.pk}/reply/",
            {"landlord_reply": "Yes, it is still available."},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        inquiry.refresh_from_db()
        assert inquiry.status == "replied"
        assert inquiry.replied_at is not None

    def test_a_tenant_cannot_reply_to_an_inquiry(self, tenant_client, rental, tenant):
        inquiry = RentalInquiry.objects.create(rental=rental, tenant=tenant, message="hello")

        response = tenant_client.post(
            f"{self.url}{inquiry.pk}/reply/", {"landlord_reply": "no"}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestPublicRentalEndpoints:
    def test_featured_returns_a_bare_array_of_featured_available_rentals(
        self, api_client, landlord
    ):
        RentalFactory(landlord=landlord, is_featured=True, status="available")
        RentalFactory(landlord=landlord, is_featured=False)

        response = api_client.get("/api/v1/rentals/featured/")

        assert response.status_code == status.HTTP_200_OK
        assert isinstance(response.json(), list)
        assert len(response.json()) == 1

    def test_recent_returns_a_bare_array(self, api_client, rental):
        response = api_client.get("/api/v1/rentals/recent/")

        assert response.status_code == status.HTTP_200_OK
        assert isinstance(response.json(), list)

    def test_there_is_no_top_rated_rentals_endpoint(self, api_client):
        """rentalsApi.getTopRated() in the frontend points at nothing."""
        response = api_client.get("/api/v1/rentals/top-rated/")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_rental_statistics_require_staff(self, staff_client, rental):
        response = staff_client.get("/api/v1/rentals/admin/statistics/")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["total_rentals"] == 1


class TestAdminRentals:
    url = "/api/v1/rentals/admin/properties/"

    def test_staff_can_list_all_rentals(self, staff_client, rental):
        response = staff_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK

    def test_staff_can_feature_a_rental(self, staff_client, rental):
        response = staff_client.post(f"{self.url}{rental.pk}/toggle_featured/")

        assert response.status_code == status.HTTP_200_OK
        rental.refresh_from_db()
        assert rental.is_featured is True

    def test_staff_can_change_the_status(self, staff_client, rental):
        response = staff_client.patch(
            f"{self.url}{rental.pk}/update_status/", {"status": "rented"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        rental.refresh_from_db()
        assert rental.status == "rented"

    def test_a_landlord_cannot_reach_the_admin_viewset(self, landlord_client):
        assert landlord_client.get(self.url).status_code == status.HTTP_403_FORBIDDEN


# ===========================================================================
# Reviews
# ===========================================================================


class TestReviewWrites:
    url = "/api/v1/reviews/"

    def test_any_tenant_can_review_any_rental_without_ever_living_there(
        self, tenant_client, tenant, rental
    ):
        """The core trust hole ADR-004 exists to close.

        No tenancy, no stay, no proof of any relationship to the property is
        required. This assertion is expected to be INVERTED by the rewrite.
        """
        response = tenant_client.post(
            self.url,
            {"rental": rental.pk, "rating": 5, "comment": "Great place, allegedly."},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert Review.objects.filter(tenant=tenant, rental=rental).exists()

    def test_a_landlord_cannot_write_a_review(self, landlord_client, rental):
        response = landlord_client.post(
            self.url,
            {"rating": 5, "rental": rental.pk, "comment": "My own place is superb."},
            format="json",
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_second_review_of_the_same_rental_is_rejected(self, tenant_client, tenant, rental):
        ReviewFactory(rental=rental, tenant=tenant)

        response = tenant_client.post(
            self.url,
            {"rental": rental.pk, "rating": 3, "comment": "Changed my mind about this."},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_future_move_in_date_is_rejected(self, tenant_client, rental):
        response = tenant_client.post(
            self.url,
            {
                "rental": rental.pk,
                "rating": 4,
                "comment": "A perfectly ordinary review comment.",
                "move_in_date": str(dt.date.today() + dt.timedelta(days=30)),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_move_out_before_move_in_is_rejected(self, tenant_client, rental):
        response = tenant_client.post(
            self.url,
            {
                "rental": rental.pk,
                "rating": 4,
                "comment": "A perfectly ordinary review comment.",
                "move_in_date": str(dt.date.today() - dt.timedelta(days=10)),
                "move_out_date": str(dt.date.today() - dt.timedelta(days=40)),
            },
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_an_author_can_edit_their_review_with_no_time_limit(self, tenant_client, review):
        """ADR-004 introduces an edit window; today there is none."""
        response = tenant_client.patch(
            f"{self.url}{review.pk}/",
            {"comment": "Edited long after the fact, with no restriction."},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK

    def test_a_stranger_cannot_edit_a_review(self, authenticate, review):
        client = authenticate(TenantFactory())

        response = client.patch(
            f"{self.url}{review.pk}/", {"comment": "Not mine to edit."}, format="json"
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_an_author_can_delete_their_review(self, tenant_client, review):
        response = tenant_client.delete(f"{self.url}{review.pk}/")

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not Review.objects.filter(pk=review.pk).exists()


class TestReviewReads:
    url = "/api/v1/reviews/"

    def test_listing_reviews_is_public(self, api_client, review):
        response = api_client.get(self.url)
        assert response.status_code == status.HTTP_200_OK

    def test_filters_by_rental(self, api_client, review, landlord):
        ReviewFactory(rental=RentalFactory(landlord=landlord))

        body = api_client.get(self.url, {"rental_id": review.rental_id}).json()

        assert body["count"] == 1

    def test_filters_by_minimum_rating(self, api_client, rental):
        ReviewFactory(rental=rental, tenant=TenantFactory(), rating=2)
        ReviewFactory(rental=rental, tenant=TenantFactory(), rating=5)

        body = api_client.get(self.url, {"min_rating": 4}).json()

        assert body["count"] == 1

    def test_my_reviews_returns_only_the_callers_reviews(self, tenant_client, review, rental):
        ReviewFactory(rental=rental, tenant=TenantFactory())

        response = tenant_client.get(f"{self.url}my_reviews/")

        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()) == 1

    def test_reviews_for_a_rental_are_public(self, api_client, review):
        response = api_client.get(f"/api/v1/reviews/rental/{review.rental_id}/")
        assert response.status_code == status.HTTP_200_OK

    def test_rental_review_statistics_are_public(self, api_client, review):
        response = api_client.get(f"/api/v1/reviews/rental/{review.rental_id}/statistics/")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["total_reviews"] == 1

    def test_the_frontends_statistics_path_does_not_exist(self, api_client, review):
        """reviewsApi.getRentalStatistics() calls /reviews/statistics/<id>/."""
        response = api_client.get(f"/api/v1/reviews/statistics/{review.rental_id}/")
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_recent_reviews_are_public(self, api_client, review):
        response = api_client.get("/api/v1/reviews/recent/")
        assert response.status_code == status.HTTP_200_OK

    def test_top_rated_reviews_are_public(self, api_client, review):
        response = api_client.get("/api/v1/reviews/top-rated/")
        assert response.status_code == status.HTTP_200_OK


class TestReviewInteractions:
    url = "/api/v1/reviews/"

    def test_a_helpfulness_vote_updates_the_cached_counters(self, authenticate, review):
        client = authenticate(TenantFactory())

        response = client.post(
            f"{self.url}{review.pk}/vote_helpfulness/", {"is_helpful": True}, format="json"
        )

        assert response.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED)
        review.refresh_from_db()
        assert review.helpful_votes == 1
        assert review.total_votes == 1

    def test_deleting_a_vote_recomputes_the_counters(self, review):
        voter = TenantFactory()
        vote = ReviewHelpfulness.objects.create(review=review, user=voter, is_helpful=True)
        review.refresh_from_db()
        assert review.total_votes == 1

        vote.delete()

        review.refresh_from_db()
        assert review.total_votes == 0

    def test_reporting_a_review_is_unreachable_for_everyone(
        self, authenticate, tenant_client, review
    ):
        """The report action cannot be invoked by anybody at all.

        The viewset\'s IsTenantOrReadOnly.has_object_permission only admits the
        review\'s own author for unsafe methods, and the action itself rejects
        the author with 400 "You cannot report your own review". The two rules
        together leave no caller who can report anything.
        """
        stranger = authenticate(TenantFactory())
        blocked = stranger.post(
            f"{self.url}{review.pk}/report/",
            {"reason": "spam", "description": "Reads like a fake review."},
            format="json",
        )
        assert blocked.status_code == status.HTTP_403_FORBIDDEN

        own = tenant_client.post(
            f"{self.url}{review.pk}/report/",
            {"reason": "spam", "description": "Reporting my own."},
            format="json",
        )
        assert own.status_code == status.HTTP_400_BAD_REQUEST

        assert ReviewReport.objects.count() == 0

    def test_the_owning_landlord_can_respond_once(self, landlord_client, review):
        first = landlord_client.post(
            f"{self.url}{review.pk}/response/",
            {"landlord_response": "Thank you for staying with us."},
            format="json",
        )
        assert first.status_code == status.HTTP_200_OK

        second = landlord_client.post(
            f"{self.url}{review.pk}/response/",
            {"landlord_response": "One more thing."},
            format="json",
        )
        assert second.status_code == status.HTTP_400_BAD_REQUEST

    def test_a_different_landlord_cannot_respond(self, authenticate, review):
        client = authenticate(LandlordFactory())

        response = client.post(
            f"{self.url}{review.pk}/response/",
            {"landlord_response": "Not my property."},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN


class TestAdminReviews:
    def test_staff_can_list_reviews(self, staff_client, review):
        response = staff_client.get("/api/v1/reviews/admin/reviews/")
        assert response.status_code == status.HTTP_200_OK

    def test_staff_can_toggle_approval(self, staff_client, review):
        response = staff_client.post(f"/api/v1/reviews/admin/reviews/{review.pk}/toggle_approval/")

        assert response.status_code == status.HTTP_200_OK
        review.refresh_from_db()
        assert review.is_approved is False

    def test_staff_can_toggle_verification(self, staff_client, review):
        response = staff_client.post(
            f"/api/v1/reviews/admin/reviews/{review.pk}/toggle_verification/"
        )

        assert response.status_code == status.HTTP_200_OK
        review.refresh_from_db()
        assert review.is_verified is True

    def test_staff_can_add_moderation_notes(self, staff_client, review):
        response = staff_client.patch(
            f"/api/v1/reviews/admin/reviews/{review.pk}/add_moderation_notes/",
            {"moderation_notes": "Checked against the tenancy records."},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        review.refresh_from_db()
        assert review.moderation_notes

    def test_staff_can_resolve_a_report(self, staff_client, review, staff_user):
        report = ReviewReport.objects.create(review=review, reporter=TenantFactory(), reason="spam")

        response = staff_client.post(
            f"/api/v1/reviews/admin/reports/{report.pk}/resolve/",
            {"admin_action": "Review removed."},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        report.refresh_from_db()
        assert report.is_resolved is True

    def test_staff_can_dismiss_a_report(self, staff_client, review):
        report = ReviewReport.objects.create(
            review=review, reporter=TenantFactory(), reason="other"
        )

        response = staff_client.post(f"/api/v1/reviews/admin/reports/{report.pk}/dismiss/")

        assert response.status_code == status.HTTP_200_OK

    def test_review_statistics_require_staff(self, staff_client, review):
        response = staff_client.get("/api/v1/reviews/admin/statistics/")

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["total_reviews"] == 1


# ===========================================================================
# Model-level behaviour worth pinning before the rewrite
# ===========================================================================


class TestModelBehaviour:
    def test_email_is_lowercased_on_save(self, db):
        user = User(email="MiXeD@Example.co.ke", first_name="A", last_name="B")
        user.set_password(TEST_PASSWORD)
        user.save()

        assert user.email == "mixed@example.co.ke"

    def test_create_user_needs_only_an_email(self, db):
        """FIXED. Was: create_user() demanded a vestigial username.

        USERNAME_FIELD was already email, but the model inherited
        AbstractUser's manager, so createsuperuser and every
        objects.create_user() call still revolved around a column nobody read
        (docs/AUDIT.md §7 item 11). User is on AbstractBaseUser now and the
        column is gone.
        """
        user = User.objects.create_user(
            email="x@example.co.ke", password=TEST_PASSWORD, first_name="X", last_name="Y"
        )

        assert user.pk is not None
        assert not hasattr(user, "username")

    def test_create_user_rejects_a_missing_email(self, db):
        with pytest.raises(ValueError, match="email"):
            User.objects.create_user(email="", password=TEST_PASSWORD)

    def test_rental_str_renders_a_dollar_sign(self, rental):
        """Currency is hard-coded to USD in __str__; KES is the launch market."""
        assert "$" in str(rental)

    def test_rental_average_rating_is_zero_without_reviews(self, rental):
        assert rental.average_rating == 0
        assert rental.review_count == 0

    def test_rental_average_rating_aggregates_reviews(self, rental):
        ReviewFactory(rental=rental, tenant=TenantFactory(), rating=2)
        ReviewFactory(rental=rental, tenant=TenantFactory(), rating=4)

        assert rental.average_rating == pytest.approx(3.0)

    def test_full_address_uses_a_us_shape(self, rental):
        """'address, city, state zip' -- not how a Kenyan address is written."""
        assert rental.full_address == (
            f"{rental.address}, {rental.city}, {rental.state} {rental.zip_code}"
        )

    def test_saving_a_rental_backfills_the_contact_email(self, landlord):
        created = RentalFactory(landlord=landlord, contact_email="")
        assert created.contact_email == landlord.email

    def test_an_inconsistent_lease_range_raises_value_error_not_validation_error(self, landlord):
        """Rental.save() raises ValueError, which surfaces as a 500, not a 400."""
        with pytest.raises(ValueError):
            RentalFactory(landlord=landlord, lease_duration_min=12, lease_duration_max=6)

    def test_only_one_image_stays_primary(self, rental):
        first = RentalImageFactory(rental=rental, is_primary=True)
        RentalImageFactory(rental=rental, is_primary=True)

        first.refresh_from_db()
        assert first.is_primary is False

    def test_a_review_title_is_generated_when_blank(self, rental, tenant):
        review = ReviewFactory(rental=rental, tenant=tenant, rating=5, title="")
        assert review.title == "5 - Excellent experience"

    def test_stay_duration_is_reported_in_months(self, rental, tenant):
        review = ReviewFactory(
            rental=rental,
            tenant=tenant,
            move_in_date=dt.date(2025, 1, 1),
            move_out_date=dt.date(2025, 7, 1),
        )

        assert review.stay_duration_months == 6

    def test_helpfulness_percentage_handles_zero_votes(self, review):
        assert review.helpfulness_percentage == 0
