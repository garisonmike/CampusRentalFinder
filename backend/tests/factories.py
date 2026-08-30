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
from django.conf import settings
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
from reviews.models import Review
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
    # **Each campus somewhere different.**
    #
    # Every campus used to sit at one coordinate, and so did every property --
    # the same one. Every distance the suite could compute was therefore
    # 0.0 km, which means every assertion along the geographic dimension was
    # being made against a fixture that had collapsed that dimension. They
    # passed. `docs/OPERATIONS.md` records the shape.
    #
    # About 1.1 km apart per campus, all of them inside
    # `CAMPUS_JOIN_RADIUS_KM` of the properties below.
    #
    # A first attempt spread them half a degree apart -- 55 km, deliberately
    # beyond the join radius so that "another university's campus" would be
    # genuinely out of range. That broke 70 tests and 79 errors, because the
    # sequence counter runs across the whole session: by the thirtieth test
    # the campus is fifteen degrees south of Nairobi and nothing is near
    # anything. The lesson is that tenant isolation in this suite comes from
    # the **join row**, not from distance, and the fixtures were right to
    # assume proximity.
    #
    # What was wrong was sharing one point with `PropertyFactory`, which made
    # every distance exactly 0.0 km. Distances are now non-zero, distinct and
    # orderable, which is what the geographic assertions need in order to mean
    # anything.
    #
    # Nairobi: close enough to the equator that a latitude-correction bug
    # divides by zero, which is why the base point is here (ADR-006).
    #
    # **A grid, not a line.** A linear sequence runs away: the counter is
    # session-wide, so `n * 0.01` puts the two-thousandth campus 2200 km from
    # the first, and a wide enough spread stops testing the product and starts
    # testing `campus_latitude_range` and `pcd_distance_sane` -- the schema
    # correctly rejecting coordinates that are not on Earth. That is a fixture
    # generating nonsense, not a suite depending on proximity, and telling the
    # two apart cost a wrong answer once already.
    #
    # 30 x 30 at 0.05 degrees: every coordinate distinct for the first 900
    # campuses, and the box stays about 165 km on a side -- Kenyan-country
    # scale, not planetary.
    #
    # Offset off the shared base point, and westward while `PropertyFactory`
    # goes east, so a campus can never land exactly on a property. Both
    # factories start from the same Nairobi origin and each has its own
    # counter, so without this the first campus and the first property sit on
    # top of each other and the distance is 0.0 -- the original bug, surviving
    # in miniature for one pair.
    latitude = factory.Sequence(lambda n: -1.286389 + 0.02 + (n % 30) * 0.05)
    longitude = factory.Sequence(lambda n: 36.817223 - 0.02 - (n // 30) * 0.05)
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
    # Near campus 0, and not exactly on it.
    #
    # A fixed point shared with `CampusFactory` made every property-to-campus
    # distance exactly zero, so "nearest campus" ordering, the distance
    # filters and the km rendering were all being asserted against a single
    # value. The jitter is ~100 m per property: enough that distances differ
    # and order is meaningful, small enough that every property stays inside
    # any radius a test would set.
    #
    # Nairobi. Close enough to the equator that a latitude-correction bug
    # divides by zero -- see ADR-006.
    latitude = factory.Sequence(lambda n: -1.286389 + n * 0.001)
    longitude = factory.Sequence(lambda n: 36.817223 + n * 0.001)
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
    """A confirmed stay that is **finished**, and long enough to be reviewable.

    **Past by default, deliberately.** The default fixture shape decides which
    bugs the suite can see, and this one was chosen after a bug it could not:

    > `Unit.vacant_count` reconciliation counted tenancies by `status='active'`,
    > which at the time was stamped on every stay regardless of its dates. A
    > pooled unit's occupancy therefore included every historical tenancy it had
    > ever had, so a block with three years of turnover reported itself full and
    > vanished from search. **No test caught it, because no fixture had any
    > history** — every stay the suite created was currently running, so the
    > count was accidentally right in every case the suite could construct.

    A currently-running stay is the *easy* case: almost any implementation gets
    it right. A finished stay is where "current" and "exists" stop being the
    same question, so that is what a test gets unless it asks otherwise.

    Ask otherwise with a trait, at the call site, where a reader can see it::

        TenancyFactory()                  # finished last month
        TenancyFactory(current=True)      # running now, open-ended
        TenancyFactory(upcoming=True)     # starts next month
    """

    class Meta:
        model = Tenancy

    class Params:
        #: Running now. `end_date=None` means open-ended, which is a real case
        #: and is NOT the same as a stay that has ended (ADR-004).
        current = factory.Trait(
            start_date=factory.LazyFunction(lambda: dt.date.today() - dt.timedelta(days=60)),
            end_date=None,
        )
        #: Running now, with an agreed end still in the future.
        current_fixed_term = factory.Trait(
            start_date=factory.LazyFunction(lambda: dt.date.today() - dt.timedelta(days=60)),
            end_date=factory.LazyFunction(lambda: dt.date.today() + dt.timedelta(days=120)),
        )
        #: Agreed, not started. Blocks landlord erasure just as a running stay
        #: does -- the student has a counterparty they have not needed yet.
        upcoming = factory.Trait(
            start_date=factory.LazyFunction(lambda: dt.date.today() + dt.timedelta(days=30)),
            end_date=factory.LazyFunction(lambda: dt.date.today() + dt.timedelta(days=300)),
        )

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
    #: Read from settings, not written as 7.
    #:
    #: It was a literal, and `create_claim` derives the same deadline from
    #: `TENANCY_CONFIRMATION_WINDOW_DAYS`. Raising the setting would have left
    #: every factory-made claim on the old window while the service test --
    #: which reads the setting -- went on passing: the check and the checked
    #: thing in two places, with the fixture quietly winning for most of the
    #: suite (`docs/OPERATIONS.md`).
    confirmation_deadline = factory.LazyFunction(
        lambda: timezone.now() + dt.timedelta(days=settings.TENANCY_CONFIRMATION_WINDOW_DAYS)
    )


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
