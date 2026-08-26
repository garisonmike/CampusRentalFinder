"""
factory_boy factories.

Identity and capability are separate here, as ADR-003 requires: ``UserFactory``
makes a person, and the profile factories grant what they may do. There is no
role string to set, because there is no role string.

The rental and review factories still target the draft models, which the schema
rewrite replaces in a later phase.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from accounts.capabilities import CaretakerPermission
from accounts.models import (
    CaretakerAssignment,
    LandlordProfile,
    StudentProfile,
    UniversityStaffProfile,
    User,
)
from properties.constants import (
    FurnishingStatus,
    PhotoProcessingStatus,
    PropertyStatus,
    PropertyType,
)
from properties.models import Property, PropertyCampusDistance, Unit, UnitPhoto
from ratings.models import Review
from rentals.models import Rental, RentalFavorite, RentalImage, RentalInquiry
from reviews.models import Review as DraftReview
from tenancies.constants import (
    ApplicationStatus,
    ClaimStatus,
    ConfirmationSource,
    TenancyStatus,
)
from tenancies.models import Application, Tenancy, TenancyClaim
from universities.constants import VerificationMethod, VerificationStatus
from universities.models import Campus, University

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
    #: Both offered by default, so a test about something else does not have
    #: to know that verification methods are per-university configuration.
    #: `UniversityFactory(verification_methods_enabled=[])` is the way to
    #: assert a school that offers neither.
    verification_methods_enabled = factory.LazyFunction(
        lambda: [VerificationMethod.EMAIL_DOMAIN, VerificationMethod.STUDENT_ID_UPLOAD]
    )


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
    """Identity only. Roles come from the profile factories below (ADR-003)."""

    class Meta:
        model = User
        django_get_or_create = ("email",)
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"user{n}@students.example.ac.ke")
    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    is_active = True

    @factory.post_generation
    def password(obj, create, extracted, **kwargs):  # noqa: N805
        if not create:
            return
        obj.set_password(extracted or TEST_PASSWORD)
        obj.save(update_fields=["password"])


class LandlordProfileFactory(DjangoModelFactory):
    class Meta:
        model = LandlordProfile

    user = factory.SubFactory(UserFactory)
    business_name = factory.Sequence(lambda n: f"Landlord Holdings {n}")


class StudentProfileFactory(TenantScopedFactory):
    class Meta:
        model = StudentProfile

    user = factory.SubFactory(UserFactory)
    university = factory.SubFactory(UniversityFactory)
    verification_status = VerificationStatus.UNVERIFIED


class VerifiedStudentProfileFactory(StudentProfileFactory):
    """A student carrying the verification badge.

    The method and timestamp are required by a database constraint: a verified
    profile must say how it was verified, or a later audit cannot tell the
    automated path from the manual one.
    """

    verification_status = VerificationStatus.VERIFIED
    verification_method = VerificationMethod.EMAIL_DOMAIN
    verified_at = factory.LazyFunction(timezone.now)
    student_email = factory.Sequence(lambda n: f"verified{n}@s.example.ac.ke")


class UniversityStaffProfileFactory(TenantScopedFactory):
    class Meta:
        model = UniversityStaffProfile

    user = factory.SubFactory(UserFactory)
    university = factory.SubFactory(UniversityFactory)
    job_title = "Dean of Students"
    is_active = True


class TenantFactory(UserFactory):
    """A student. Named for the housing sense of the word, not the SaaS one."""


class LandlordFactory(UserFactory):
    """A user who owns properties. The profile is what grants the capability."""

    email = factory.Sequence(lambda n: f"landlord{n}@example.co.ke")

    @factory.post_generation
    def profile(obj, create, extracted, **kwargs):  # noqa: N805
        if create:
            LandlordProfile.objects.get_or_create(user=obj)


class PlatformAdminFactory(UserFactory):
    """A user with no platform-staff flag.

    Kept to prove the escalation path is closed: under the draft this user
    would have declared ``user_type='admin'`` at registration and gained edit
    rights over every listing.
    """

    email = factory.Sequence(lambda n: f"selfdeclared{n}@example.co.ke")


class StaffFactory(UserFactory):
    """Platform staff. `is_staff` is the only meaning of the word."""

    email = factory.Sequence(lambda n: f"staff{n}@example.co.ke")
    is_staff = True
    is_superuser = True


# ---------------------------------------------------------------------------
# Properties (ADR-002). Every one of these is tenant-scoped, so they all use
# TenantScopedFactory -- factory_boy reaches for Model.objects, which a scoped
# model refuses.
# ---------------------------------------------------------------------------


class PropertyFactory(TenantScopedFactory):
    class Meta:
        model = Property

    landlord = factory.SubFactory(LandlordProfileFactory)
    name = factory.Sequence(lambda n: f"Wendani Hostel Block {n}")
    slug = factory.Sequence(lambda n: f"wendani-hostel-block-{n}")
    description = "Water tank, backup power, secure compound."
    property_type = PropertyType.HOSTEL_BLOCK
    county = "nairobi"
    town = "Nairobi"
    estate = "Kahawa Wendani"
    landmark = "opposite Naivas"
    # Nairobi. Close enough to the equator that a latitude-correction bug
    # divides by zero -- see ADR-006.
    latitude = -1.286389
    longitude = 36.817223
    has_water_tank = True
    status = PropertyStatus.PUBLISHED
    published_at = factory.LazyFunction(timezone.now)


class DraftPropertyFactory(PropertyFactory):
    """A property that is not yet listed. Has no published_at, by constraint."""

    status = PropertyStatus.DRAFT
    published_at = factory.LazyFunction(lambda: None)


class PropertyCampusDistanceFactory(TenantScopedFactory):
    """The join that makes a property visible to a university (ADR-002).

    ``straight_line_km`` is computed by the model's save(), so it is not set
    here — setting it would hide a bug in the computation.
    """

    class Meta:
        model = PropertyCampusDistance

    property = factory.SubFactory(PropertyFactory)
    university = factory.SubFactory(UniversityFactory)
    campus = factory.SubFactory(CampusFactory)
    is_primary = False


class UnitFactory(TenantScopedFactory):
    class Meta:
        model = Unit

    property = factory.SubFactory(PropertyFactory)
    label = factory.Sequence(lambda n: f"B{n}")
    unit_type = PropertyType.BEDSITTER
    rent_kes = Decimal("9500.00")
    deposit_kes = Decimal("9500.00")
    water_included = True
    furnished = FurnishingStatus.UNFURNISHED
    size_sqm = 20
    bedrooms = 0
    has_private_bathroom = True
    total_count = 1
    vacant_count = 1
    min_stay_months = 4
    is_active = True


class UnitPhotoFactory(TenantScopedFactory):
    """A photo on object storage. Keys only -- never a local file."""

    class Meta:
        model = UnitPhoto

    unit = factory.SubFactory(UnitFactory)
    original_key = factory.Sequence(lambda n: f"properties/units/photo-{n}/original.jpg")
    processing_status = PhotoProcessingStatus.PENDING
    caption = "Front view"
    sort_order = 0
    width = 3024
    height = 4032
    byte_size = 2_400_000


class ReadyUnitPhotoFactory(UnitPhotoFactory):
    """A photo whose variants have been generated."""

    processing_status = PhotoProcessingStatus.READY
    thumb_key = factory.LazyAttribute(lambda o: o.original_key.replace("original", "thumb"))
    medium_key = factory.LazyAttribute(lambda o: o.original_key.replace("original", "medium"))
    large_key = factory.LazyAttribute(lambda o: o.original_key.replace("original", "large"))


class ApplicationFactory(TenantScopedFactory):
    """A student applying for a unit."""

    class Meta:
        model = Application

    unit = factory.SubFactory(UnitFactory)
    applicant = factory.SubFactory(UserFactory)
    status = ApplicationStatus.SUBMITTED
    move_in_date = factory.LazyFunction(lambda: dt.date.today() + dt.timedelta(days=14))
    intended_months = 8
    message = "Is the bedsitter still vacant?"


class TenancyFactory(TenantScopedFactory):
    """A confirmed stay, long enough to be reviewable."""

    class Meta:
        model = Tenancy

    unit = factory.SubFactory(UnitFactory)
    tenant = factory.SubFactory(UserFactory)
    application = factory.SubFactory(ApplicationFactory)
    confirmation_source = ConfirmationSource.APPLICATION
    confirmed_by = factory.SubFactory(UserFactory)
    confirmed_at = factory.LazyFunction(timezone.now)
    start_date = factory.LazyFunction(lambda: dt.date.today() - dt.timedelta(days=200))
    end_date = factory.LazyFunction(lambda: dt.date.today() - dt.timedelta(days=20))
    monthly_rent_kes = Decimal("9500.00")
    status = TenancyStatus.CONFIRMED


class ReviewFactory(TenantScopedFactory):
    class Meta:
        model = Review

    tenancy = factory.SubFactory(TenancyFactory)
    rating = 4
    comment = "Water was reliable, the gate was not."


class TenancyClaimFactory(TenantScopedFactory):
    """A claim for a stay the platform did not witness (ADR-004)."""

    class Meta:
        model = TenancyClaim

    unit = factory.SubFactory(UnitFactory)
    claimant = factory.SubFactory(UserFactory)
    start_date = factory.LazyFunction(lambda: dt.date.today() - dt.timedelta(days=200))
    end_date = factory.LazyFunction(lambda: dt.date.today() - dt.timedelta(days=20))
    monthly_rent_kes = Decimal("9500.00")
    status = ClaimStatus.PENDING
    is_retrospective = False
    confirmation_deadline = factory.LazyFunction(lambda: timezone.now() + dt.timedelta(days=7))


class CaretakerAssignmentFactory(TenantScopedFactory):
    """A caretaker granted management of one property (ADR-003)."""

    class Meta:
        model = CaretakerAssignment

    user = factory.SubFactory(UserFactory)
    property = factory.SubFactory(PropertyFactory)
    granted_by = factory.LazyAttribute(lambda o: o.property.landlord.user)
    permissions = factory.LazyFunction(
        lambda: [
            CaretakerPermission.MANAGE_VACANCY,
            CaretakerPermission.MANAGE_PHOTOS,
            CaretakerPermission.RESPOND_INQUIRIES,
        ]
    )
    is_active = True


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


class DraftReviewFactory(DjangoModelFactory):
    """The pre-rewrite Review. Removed in Phase 7 with the rest of `reviews`."""

    class Meta:
        model = DraftReview

    rental = factory.SubFactory(RentalFactory)
    tenant = factory.SubFactory(TenantFactory)
    rating = 4
    comment = factory.Faker("paragraph", nb_sentences=3)
    is_approved = True
