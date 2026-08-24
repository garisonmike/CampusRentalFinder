"""
factory_boy factories for the current (draft) domain model.

These will need reworking once the schema rewrite lands -- in particular
``user_type`` disappears in favour of the profile models described in ADR-003.
They are written against what exists today so the test suite has something to
build on immediately.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import factory
from django.contrib.auth import get_user_model
from factory.django import DjangoModelFactory

from rentals.models import Rental, RentalFavorite, RentalImage, RentalInquiry
from reviews.models import Review
from universities.models import Campus, University

User = get_user_model()

TEST_PASSWORD = "test-password-123"


class TenantScopedFactory(DjangoModelFactory):
    """Base for factories over tenant-scoped models.

    factory_boy reaches for ``Model.objects``, which on a scoped model raises
    rather than returning every tenant's rows (ADR-001). Building a fixture is
    exactly the case where unscoped access is correct, so factories use
    ``all_objects`` explicitly.
    """

    class Meta:
        abstract = True

    @classmethod
    def _get_manager(cls, model_class):
        return model_class.all_objects


class UniversityFactory(DjangoModelFactory):
    """A tenant.

    No ``django_get_or_create``: a test that passes a duplicate subdomain is
    asserting that the database refuses it, and get-or-create would quietly
    return the existing row instead.
    """

    class Meta:
        model = University

    name = factory.Sequence(lambda n: f"Test University {n}")
    display_name = factory.Sequence(lambda n: f"TU{n}")
    slug = factory.Sequence(lambda n: f"test-university-{n}")
    subdomain = factory.Sequence(lambda n: f"tu{n}")
    domain = factory.Sequence(lambda n: f"tu{n}.ac.ke")
    county = "nairobi"
    town = "Nairobi"
    primary_hsl = "142 71% 45%"
    secondary_hsl = "30 50% 40%"
    accent_hsl = "142 71% 95%"
    is_active = True


class CampusFactory(TenantScopedFactory):
    class Meta:
        model = Campus

    university = factory.SubFactory(UniversityFactory)
    name = factory.Sequence(lambda n: f"Campus {n}")
    town = "Nairobi"
    county = "nairobi"
    # Nairobi, which is close enough to the equator that a latitude-correction
    # bug divides by zero -- see ADR-006.
    latitude = -1.286389
    longitude = 36.817223
    is_main = False


class UserFactory(DjangoModelFactory):
    class Meta:
        model = User
        django_get_or_create = ("email",)
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"user{n}@students.example.ac.ke")
    username = factory.LazyAttribute(lambda o: o.email)
    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    user_type = "tenant"
    is_verified = True
    is_active = True

    @factory.post_generation
    def password(obj, create, extracted, **kwargs):  # noqa: N805
        if not create:
            return
        obj.set_password(extracted or TEST_PASSWORD)
        obj.save(update_fields=["password"])


class TenantFactory(UserFactory):
    """A student/tenant account."""

    user_type = "tenant"


class LandlordFactory(UserFactory):
    """A landlord account."""

    email = factory.Sequence(lambda n: f"landlord{n}@example.co.ke")
    user_type = "landlord"


class PlatformAdminFactory(UserFactory):
    """A platform administrator (``user_type='admin'``, not Django staff)."""

    email = factory.Sequence(lambda n: f"admin{n}@example.co.ke")
    user_type = "admin"


class StaffFactory(UserFactory):
    """A Django staff user -- what DRF's ``IsAdminUser`` actually checks."""

    email = factory.Sequence(lambda n: f"staff{n}@example.co.ke")
    user_type = "admin"
    is_staff = True
    is_superuser = True


class RentalFactory(DjangoModelFactory):
    class Meta:
        model = Rental

    title = factory.Sequence(lambda n: f"Bedsitter near campus #{n}")
    description = factory.Faker("paragraph", nb_sentences=4)
    property_type = "studio"
    landlord = factory.SubFactory(LandlordFactory)
    price = Decimal("12000.00")
    security_deposit = Decimal("12000.00")
    address = factory.Faker("street_address")
    city = "Nairobi"
    state = "Nairobi County"
    zip_code = "00100"
    country = "Kenya"
    latitude = -1.286389
    longitude = 36.817223
    bedrooms = 1
    bathrooms = 1
    square_footage = 300
    available_from = factory.LazyFunction(lambda: dt.date.today() - dt.timedelta(days=1))
    status = "available"


class RentalImageFactory(DjangoModelFactory):
    class Meta:
        model = RentalImage

    rental = factory.SubFactory(RentalFactory)
    image = factory.django.ImageField(filename="unit.jpg", width=64, height=64)
    caption = "Front view"
    is_primary = True
    order = 0


class RentalFavoriteFactory(DjangoModelFactory):
    class Meta:
        model = RentalFavorite

    user = factory.SubFactory(TenantFactory)
    rental = factory.SubFactory(RentalFactory)


class RentalInquiryFactory(DjangoModelFactory):
    class Meta:
        model = RentalInquiry

    rental = factory.SubFactory(RentalFactory)
    tenant = factory.SubFactory(TenantFactory)
    message = factory.Faker("sentence")
    status = "new"


class ReviewFactory(DjangoModelFactory):
    class Meta:
        model = Review

    rental = factory.SubFactory(RentalFactory)
    tenant = factory.SubFactory(TenantFactory)
    rating = 4
    comment = factory.Faker("paragraph", nb_sentences=3)
    is_approved = True
