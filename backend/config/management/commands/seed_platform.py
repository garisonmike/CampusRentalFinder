"""
Generate a plausible platform to develop and test against.

**Not fixtures.** Every fixture in the suite was built to demonstrate one
thing, and that is exactly why the `vacant_count` reconciliation bug survived:
no fixture had history, so no test could see a bug that needs it. A dataset
whose shape nobody chose per-assertion is the only way to find the failures
that come from data being *ordinary* rather than from data being pointed.

What this makes, and why each part is here:

**Two universities**, with different verification policies and genuinely
different brand palettes -- one of them deliberately inside the hostile band,
because the second tenant is where a design that leans on colour breaks and
the first tenant is where nobody notices.

**Properties in every publishable state**, including unpinned ones that cannot
be published, ones with no photos, and one whose only photo failed to process.
Those are not edge cases; they are what a landlord's first hour looks like.

**Tenancies with history.** Past, current, upcoming, open-ended, and early
terminated -- plus a student who moved between two units in the same block,
which is the shape that makes `student_count` and `review_count` legitimately
disagree and the one a per-assertion fixture never produces.

**Claims in every state** the machine can reach, including each typed dispute
reason, so the admin queue has something in it and the transition table is
exercised by data rather than only by tests.

**Vacancy counts at every staleness band**, including never-stated.

Deterministic under `--seed`, so a bug found against it is reproducible and can
be pasted into an issue as one number. Refuses to run when `DEBUG` is False:
the whole point is fake people with fake stays, and there is no version of
"seeded onto production" that is recoverable.
"""

from __future__ import annotations

import datetime as dt
import random

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.capabilities import CaretakerPermission
from accounts.models import CaretakerAssignment, LandlordProfile, StudentProfile, User
from engagement.constants import InquiryStatus
from engagement.models import Inquiry, SavedProperty
from properties.constants import PhotoProcessingStatus, PropertyStatus, PropertyType
from properties.models import Property, PropertyCampusDistance, Unit, UnitPhoto
from properties.services import state_vacancy
from reviews.aggregates import (
    LandlordRatingAggregate,
    PropertyRatingAggregate,
    UnitRatingAggregate,
)
from reviews.models import Review, ReviewResponse
from tenancies.constants import (
    ApplicationStatus,
    ClaimStatus,
    ConfirmationSource,
    DisputeReason,
    EscalationReason,
    TenancyStatus,
)
from tenancies.models import Application, Tenancy, TenancyClaim
from universities.constants import SignupPolicy, VerificationMethod, VerificationStatus
from universities.models import Campus, University

# --- Kenyan flavour, so the data reads as the thing it represents ----------

FIRST_NAMES = [
    "Wanjiku",
    "Otieno",
    "Achieng",
    "Kipchoge",
    "Njeri",
    "Mwangi",
    "Akinyi",
    "Kamau",
    "Chebet",
    "Wafula",
    "Nyambura",
    "Omondi",
    "Wanjiru",
    "Kiprop",
    "Adhiambo",
    "Muthoni",
    "Barasa",
    "Cherono",
    "Onyango",
    "Wairimu",
]
SURNAMES = [
    "Kamau",
    "Ochieng",
    "Mutua",
    "Kiptoo",
    "Njoroge",
    "Wekesa",
    "Auma",
    "Maina",
    "Rotich",
    "Owino",
    "Gitau",
    "Chelimo",
    "Simiyu",
    "Mbugua",
]
ESTATES = [
    "Kahawa Wendani",
    "Kahawa Sukari",
    "Githurai 44",
    "Ruiru",
    "Juja",
    "Kasarani",
    "Roysambu",
    "Zimmerman",
    "Membley",
    "Kimbo",
]
LANDMARKS = [
    "opposite Naivas",
    "behind the Total petrol station",
    "next to the shopping centre",
    "off the Thika superhighway",
    "near the police post",
    "beside the matatu stage",
]
BLOCK_NAMES = [
    "Sunrise",
    "Green Court",
    "Kilimani",
    "Riverside",
    "Baraka",
    "Upendo",
    "Jamii",
    "Tumaini",
    "Amani",
    "Nuru",
    "Faraja",
    "Neema",
]


#: The smallest platform that still contains every shape this command exists to
#: produce. See `Command.role_of` -- each of the first five indices has a job.
MINIMUM_PROPERTIES = 5


class Command(BaseCommand):
    help = "Seed a coherent, plausible platform for development."

    def add_arguments(self, parser):
        parser.add_argument(
            "--seed",
            type=int,
            default=20260828,
            help="Random seed. The same seed produces the same platform, so a "
            "bug found here can be reproduced with one number.",
        )
        parser.add_argument(
            "--properties",
            type=int,
            default=24,
            help="Properties per university. The default is enough for query "
            "counts to show their real shape; the minimum is what it takes to "
            "contain every shape at all.",
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Delete existing seeded data first.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            # Not a warning and not a prompt. There is no version of "seeded
            # onto production" that anybody recovers from, and a command that
            # can be forced through will eventually be forced through.
            raise CommandError(
                "seed_platform refuses to run with DEBUG=False. It creates "
                "fake people with fake tenancies and fake reviews about real-"
                "sounding places; there is no undo."
            )

        # Seeded and reproducible on purpose. Not a security context:
        # the whole value of this command is that the same number gives
        # the same platform, so a bug found here travels as one integer.
        if options["properties"] < MINIMUM_PROPERTIES:
            # Refused rather than clamped. A seed that quietly produced fewer
            # shapes than it claims is the same defect as everything else this
            # command found: a check whose scope is narrower than the belief
            # attached to it. Five is the number of distinct roles `role_of`
            # has to place, and below it some are structurally impossible --
            # one property cannot be both a pooled block and unpinned.
            raise CommandError(
                f"--properties must be at least {MINIMUM_PROPERTIES}. Below "
                f"that, the seed cannot contain a pooled block, an unpinned "
                f"draft, a property with no units, one with a blank amenity "
                f"form, and a plain draft all at once -- so it would be a "
                f"platform missing shapes it claims to have."
            )

        self.random = random.Random(options["seed"])  # noqa: S311
        #: Whether an open-ended stay exists yet. Tracked rather than left to a
        #: modulus, so the branch is present at every `--properties` size.
        self.seeded_open_ended = False
        #: Cycles the claim states in order. See `make_claims` for why this is
        #: a counter rather than another function of `index`.
        self.claim_state = 0
        #: Same guarantee for the early-terminated stay.
        self.seeded_termination = False
        self.today = timezone.localdate()
        self.now = timezone.now()

        with transaction.atomic():
            if options["flush"]:
                self.flush()

            universities = self.make_universities()
            landlords = self.make_landlords()
            students = {
                university.subdomain: self.make_students(university, count=18)
                for university in universities
            }

            for university in universities:
                self.make_properties(
                    university=university,
                    landlords=landlords,
                    students=students[university.subdomain],
                    count=options["properties"],
                )

        self.build_aggregates()
        self.report()

    # -- teardown ----------------------------------------------------------

    def flush(self) -> None:
        """Remove seeded rows, children first.

        Deliberately not `flush` on the whole database: a developer running
        this has a superuser they do not want to recreate.
        """
        Inquiry.all_objects.all().delete()
        SavedProperty.all_objects.all().delete()
        PropertyRatingAggregate.all_objects.all().delete()
        UnitRatingAggregate.all_objects.all().delete()
        LandlordRatingAggregate.objects.all().delete()
        ReviewResponse.all_objects.all().delete()
        Review.all_objects.all().delete()
        Tenancy.all_objects.all().delete()
        TenancyClaim.all_objects.all().delete()
        Application.all_objects.all().delete()
        UnitPhoto.all_objects.all().delete()
        Unit.all_objects.all().delete()
        PropertyCampusDistance.all_objects.all().delete()
        Property.all_objects.all().delete()
        CaretakerAssignment.all_objects.all().delete()
        StudentProfile.all_objects.all().delete()
        LandlordProfile.objects.all().delete()
        User.objects.filter(is_superuser=False).delete()
        Campus.all_objects.all().delete()
        University.objects.all().delete()

    # -- the tenants -------------------------------------------------------

    def make_universities(self) -> list[University]:
        """Two schools that differ in the two ways that matter.

        Different **policies**, because gating is per-student and frozen at
        registration (ADR-003) -- one school requiring verification and one not
        is the only way to see that a policy change widens rather than breaks.

        Different **palettes**, one of them a low-chroma grey from the hostile
        band. A design tested only against the stock green is a design that
        works for the first tenant.
        """
        kenyatta = University.objects.create(
            name="Kenyatta University",
            display_name="KyU",
            slug="kenyatta",
            subdomain="kyu",
            domain="students.ku.ac.ke",
            county="nairobi",
            town="Kahawa",
            primary_hsl="142 71% 45%",
            secondary_hsl="30 50% 40%",
            accent_hsl="142 71% 95%",
            signup_policy=SignupPolicy.OPEN,
            verification_required_to_review=False,
        )
        Campus.all_objects.create(
            university=kenyatta,
            name="Main Campus",
            town="Kahawa",
            county="nairobi",
            latitude=-1.1806,
            longitude=36.9300,
            is_main=True,
        )

        # The awkward tenant on purpose: verification required, and a brand
        # colour with almost no chroma -- the palette that breaks "the primary
        # colour draws the eye".
        jkuat = University.objects.create(
            name="Jomo Kenyatta University of Agriculture and Technology",
            display_name="JKUAT",
            slug="jkuat",
            subdomain="jkuat",
            domain="students.jkuat.ac.ke",
            county="kiambu",
            town="Juja",
            primary_hsl="210 4% 46%",
            secondary_hsl="210 3% 62%",
            accent_hsl="210 5% 92%",
            signup_policy=SignupPolicy.REQUIRED,
            verification_required_to_review=True,
            verification_grace_period_days=14,
        )
        Campus.all_objects.create(
            university=jkuat,
            name="Juja Campus",
            town="Juja",
            county="kiambu",
            latitude=-1.0921,
            longitude=37.0144,
            is_main=True,
        )
        Campus.all_objects.create(
            university=jkuat,
            name="Karen Campus",
            town="Karen",
            county="nairobi",
            latitude=-1.3190,
            longitude=36.7100,
            is_main=False,
        )

        return [kenyatta, jkuat]

    # -- people ------------------------------------------------------------

    def name(self) -> tuple[str, str]:
        return self.random.choice(FIRST_NAMES), self.random.choice(SURNAMES)

    def make_user(self, prefix: str, index: int) -> User:
        first, last = self.name()
        return User.objects.create_user(
            email=f"{prefix}{index}@seed.test",
            # A fixed, obviously-fake password. Fine because the command
            # refuses to run outside DEBUG, and useful because a developer
            # needs to log in as these people.
            password="seed-password-not-a-secret",  # noqa: S106
            first_name=first,
            last_name=last,
        )

    def make_landlords(self, count: int = 8) -> list[LandlordProfile]:
        landlords = []
        for index in range(count):
            user = self.make_user("landlord", index)
            landlords.append(
                LandlordProfile.objects.create(
                    user=user,
                    business_name=f"{self.random.choice(BLOCK_NAMES)} Properties",
                )
            )
        return landlords

    def make_students(self, university: University, *, count: int) -> list[User]:
        """Students at one school, in every verification state it allows.

        A school requiring verification still has unverified students -- they
        are inside the grace period, which is the state the whole gating
        design exists to handle.
        """
        students = []
        for index in range(count):
            user = self.make_user(f"student-{university.subdomain}-", index)

            if university.signup_policy == SignupPolicy.REQUIRED:
                # Two thirds verified, the rest inside the grace window.
                verified = index % 3 != 0
            else:
                verified = index % 4 == 0

            StudentProfile.all_objects.create(
                user=user,
                university=university,
                student_email=f"{user.first_name.lower()}{index}@{university.domain}",
                verification_status=(
                    VerificationStatus.VERIFIED if verified else VerificationStatus.UNVERIFIED
                ),
                # A check constraint requires the method alongside the status:
                # "verified" with no record of how is a badge nobody can
                # justify later. Both routes appear, because a school with only
                # email-domain verification never exercises the reviewer queue.
                verification_method=(
                    (
                        VerificationMethod.EMAIL_DOMAIN
                        if index % 2 == 0
                        else VerificationMethod.STUDENT_ID_UPLOAD
                    )
                    if verified
                    else ""
                ),
                verified_at=self.now - dt.timedelta(days=index * 3) if verified else None,
                grace_period_ends_at=(
                    None
                    if verified
                    else self.now + dt.timedelta(days=university.verification_grace_period_days)
                ),
            )
            students.append(user)
        return students

    # -- properties --------------------------------------------------------

    def make_properties(self, *, university: University, landlords, students, count: int) -> None:
        campuses = list(Campus.all_objects.filter(university=university))
        main = campuses[0]

        for index in range(count):
            landlord = landlords[index % len(landlords)]
            role = self.role_of(index, count)
            prop = self.make_property(university, main, landlord, index, role=role)

            if prop is None:
                continue

            # A property published with no units yet: the landlord created the
            # building and stopped. The detail page has a state for it, and
            # nothing else in the seed reaches that state.
            units = [] if "no_units" in role else self.make_units(prop, index)
            self.make_photos(units, index)
            self.make_vacancy(units, landlord, index)
            self.make_history(prop, units, students, landlord, index)
            self.make_engagement(prop, units, students, index)

        self.make_caretakers(university, landlords)

    @staticmethod
    def role_of(index: int, count: int) -> set[str]:
        """What this property exists to demonstrate.

        **Assigned, not sampled.** Every rare shape in this file started as
        `index % n` and every one of them was unreachable at some
        `--properties` size -- a modulus that collides with another, or one
        whose period is longer than the run. The audit that found the missing
        empty states found three more of them.

        So the first few indices have fixed jobs, and the rest are ordinary.
        A developer running `--properties 3` gets every shape; a developer
        running the default gets those plus variety.
        """
        roles: set[str] = set()

        if index == 0:
            # A pooled hostel block: the only unit shape that holds two people
            # at once, and therefore the only one an open-ended stay can share.
            roles.add("pool")
        if index == 1 and count > 1:
            # Nothing ticked. Not "has no amenities" -- the form is blank,
            # which is a different claim and worded differently.
            roles.add("blank_form")
        if index == 2 and count > 2:
            roles.add("unpinned")
        if index == 3 and count > 3:
            roles.add("no_units")
        if index == 4 and count > 4:
            roles.add("draft")

        # Beyond the fixed roles, spread the same shapes thinly so a larger
        # seed has more than one of each.
        if index > 4:
            if index % 8 == 3:
                roles.add("unpinned")
            if index % 6 == 4:
                roles.add("draft")
            if index % 9 == 5:
                roles.add("blank_form")
            if index % 11 == 7:
                roles.add("no_units")

        return roles

    def make_property(
        self, university, campus, landlord, index: int, *, role: set[str]
    ) -> Property | None:
        """One property, in a state some fraction of real ones are in.

        The shapes here are chosen from what actually happens, not from what is
        convenient to render:

        - **one in eight is unpinned**, so it cannot be published at all --
          which is a landlord's first hour, and the state the publish gate
          exists for;
        - **one in six is a draft** that could publish but has not;
        - the rest are live.
        """
        name = f"{self.random.choice(BLOCK_NAMES)} {self.random.choice(['Court', 'Apartments', 'Hostel', 'Villas', 'Place'])}"
        slug = f"{name.lower().replace(' ', '-')}-{university.subdomain}-{index}"

        unpinned = "unpinned" in role
        draft = "draft" in role
        blank_form = "blank_form" in role

        latitude = None if unpinned else campus.latitude + self.random.uniform(-0.03, 0.03)
        longitude = None if unpinned else campus.longitude + self.random.uniform(-0.03, 0.03)

        prop = Property.all_objects.create(
            landlord=landlord,
            name=name,
            slug=slug,
            description=self.random.choice(
                [
                    "Quiet block behind the shopping centre. Water is on a tank, "
                    "so it holds through the county's dry days.",
                    "Five minutes' walk to the stage. Gate is locked from 10pm.",
                    "Newly painted. The landlord lives on the compound.",
                    "",
                ]
            ),
            # See `role_of`: index 0 is always a pooled hostel block.
            property_type=(
                PropertyType.HOSTEL_BLOCK
                if "pool" in role
                else self.random.choice(
                    [PropertyType.BEDSITTER, PropertyType.HOSTEL_BLOCK, PropertyType.ONE_BEDROOM]
                )
            ),
            county=university.county,
            town=university.town,
            estate=self.random.choice(ESTATES),
            landmark=self.random.choice(LANDMARKS),
            latitude=latitude,
            longitude=longitude,
            # One property in nine has nothing ticked at all. Not "has no
            # amenities" -- the landlord has not filled the form in, which is
            # a different claim and worded differently on the page. Random
            # booleans essentially never produce it, so an empty-state audit
            # against the seed found that branch unreachable.
            has_water_tank=blank_form is False and self.random.random() < 0.7,
            has_borehole=blank_form is False and self.random.random() < 0.2,
            has_backup_power=blank_form is False and self.random.random() < 0.3,
            has_perimeter_wall=blank_form is False and self.random.random() < 0.8,
            has_security_guard=blank_form is False and self.random.random() < 0.5,
            has_wifi=blank_form is False and self.random.random() < 0.4,
            has_parking=blank_form is False and self.random.random() < 0.3,
            caretaker_on_site=blank_form is False and self.random.random() < 0.6,
            status=PropertyStatus.DRAFT if (unpinned or draft) else PropertyStatus.PUBLISHED,
            published_at=None if (unpinned or draft) else self.now - dt.timedelta(days=index * 5),
        )

        if not unpinned:
            # The join is what makes a property visible to a university
            # (ADR-002). An unpinned one gets none, which is precisely why it
            # cannot be published.
            PropertyCampusDistance.all_objects.create(
                property=prop,
                university=university,
                campus=campus,
                walking_minutes=(None if index % 5 == 0 else self.random.randint(8, 45)),
                walking_distance_km=(
                    None if index % 5 == 0 else round(self.random.uniform(0.6, 3.5), 2)
                ),
                routed_at=None if index % 5 == 0 else self.now,
                route_provider="" if index % 5 == 0 else "seed",
                is_primary=True,
            )

        return prop

    def make_units(self, prop: Property, index: int) -> list[Unit]:
        """Units, including pooled blocks at realistic sizes.

        A hostel block is **one** row with a `total_count` of forty, not forty
        rows. That distinction is where "is it available" stops being a
        boolean, and a seed made of single rooms would never exercise it.
        """
        units = []

        if prop.property_type == PropertyType.HOSTEL_BLOCK:
            for label, unit_type, rent, total in [
                ("Bedsitters", PropertyType.BEDSITTER, 8500, self.random.randint(20, 60)),
                ("Single rooms", PropertyType.SINGLE_ROOM, 5500, self.random.randint(15, 40)),
            ]:
                units.append(
                    Unit.all_objects.create(
                        property=prop,
                        label=label,
                        unit_type=unit_type,
                        rent_kes=rent + self.random.randrange(0, 2000, 500),
                        deposit_kes=rent,
                        total_count=total,
                        vacant_count=0,
                        furnished="unfurnished",
                        bedrooms=0,
                        min_stay_months=4,
                        water_included=self.random.random() < 0.6,
                        has_private_bathroom=self.random.random() < 0.4,
                        size_sqm=self.random.choice([None, 14, 16, 18, 20]),
                    )
                )
        else:
            for number in range(self.random.randint(1, 4)):
                bedrooms = 0 if prop.property_type == PropertyType.BEDSITTER else 1
                units.append(
                    Unit.all_objects.create(
                        property=prop,
                        label=f"{chr(65 + number)}{self.random.randint(1, 12)}",
                        unit_type=prop.property_type,
                        rent_kes=self.random.randrange(6000, 22000, 500),
                        deposit_kes=self.random.randrange(6000, 22000, 500),
                        total_count=1,
                        vacant_count=0,
                        furnished=self.random.choice(["unfurnished", "semi_furnished"]),
                        bedrooms=bedrooms,
                        min_stay_months=self.random.choice([4, 6, 12]),
                        water_included=self.random.random() < 0.5,
                        has_private_bathroom=self.random.random() < 0.7,
                        size_sqm=self.random.choice([None, 20, 25, 32]),
                    )
                )

        return units

    def make_photos(self, units: list[Unit], index: int) -> None:
        """Photos, including the two states a listing page has to survive.

        **No photos at all** for one property in four, and **one photo that
        failed to process** for one in four of the rest. Neither is an edge case: the
        first is every listing on its first day, and the second is what a
        broken upload actually leaves behind.
        """
        if index % 4 == 0:
            return

        for unit in units:
            for position in range(self.random.randint(1, 4)):
                # Strided off the no-photos case above rather than sharing a
                # modulus with it. `index % 7 == 0` overlapped `index % 4 == 0`
                # at index 0 and only there, so the failed-photo branch was
                # unreachable at small sizes -- the second time in this file
                # that two independent-looking moduli picked the same rows.
                # Anything shaped `index % n` here wants checking against the
                # conditions above it.
                failed = index % 4 == 1 and position == 0
                UnitPhoto.all_objects.create(
                    unit=unit,
                    original_key=f"seed/units/{unit.pk}/{position}.jpg",
                    thumb_key="" if failed else f"seed/units/{unit.pk}/{position}.thumb.webp",
                    medium_key="" if failed else f"seed/units/{unit.pk}/{position}.medium.webp",
                    large_key="" if failed else f"seed/units/{unit.pk}/{position}.large.webp",
                    processing_status=(
                        PhotoProcessingStatus.FAILED if failed else PhotoProcessingStatus.READY
                    ),
                    processing_error="Decoder rejected the file." if failed else "",
                    caption=self.random.choice(
                        ["", "The shared kitchen", "Looking towards the gate", "The room itself"]
                    ),
                    is_primary=position == 0,
                    sort_order=position,
                )

    def make_vacancy(self, units: list[Unit], landlord, index: int) -> None:
        """Counts across every freshness band, including never-stated.

        One unit in five is left **unstated** -- `vacancy_freshness: unknown`,
        which is a different fact from a stale count and is worded differently
        in the UI. A seed where everything had been stated would let that
        branch go unrendered.
        """
        for offset, unit in enumerate(units):
            band = (index + offset) % 5

            if band == 0:
                continue  # never stated

            free = self.random.randint(0, min(unit.total_count, 12))
            state_vacancy(unit, vacant_count=free, stated_by=landlord.user)

            age = {1: 2, 2: 15, 3: 45, 4: 200}[band]
            Unit.all_objects.filter(pk=unit.pk).update(
                vacant_count_updated_at=self.now - dt.timedelta(days=age)
            )

    def make_engagement(self, prop: Property, units, students, index: int) -> None:
        """Inquiries and saved listings.

        Added because an empty-state audit against the seeded platform found
        the *populated* states of both screens unreachable: there were no
        inquiries and no saved properties anywhere, so the portal's reply box
        and the student's saved list could only ever be seen empty. An empty
        state nobody can leave is as untested as one nobody can reach.
        """
        if not units or prop.status != PropertyStatus.PUBLISHED:
            return

        unit = units[0]

        for offset in range(self.random.randint(0, 3)):
            sender = students[(index + offset) % len(students)]
            answered = (index + offset) % 3 == 0

            Inquiry.all_objects.create(
                unit=unit,
                sender=sender,
                message=self.random.choice(
                    [
                        "Is the bedsitter still free for the September intake?",
                        "Does the rent include water?",
                        "How far is the stage, walking?",
                    ]
                ),
                preferred_move_in_date=(None if offset % 2 else self.today + dt.timedelta(days=30)),
                status=InquiryStatus.ANSWERED if answered else InquiryStatus.SENT,
                response="Yes, two rooms are free. Come and see it any afternoon."
                if answered
                else "",
                responded_by=prop.landlord.user if answered else None,
                responded_at=self.now - dt.timedelta(days=1) if answered else None,
            )

        for offset in range(self.random.randint(0, 2)):
            student = students[(index * 2 + offset) % len(students)]
            SavedProperty.all_objects.get_or_create(
                user=student,
                property_saved=prop,
                defaults={
                    "note": self.random.choice(
                        ["", "Cheapest one near the stage", "Ask about the water"]
                    )
                },
            )

    def make_caretakers(self, university: University, landlords) -> None:
        """Caretakers with deliberately different permission subsets.

        A seed where every caretaker held every permission would make the whole
        delegation model untestable by hand: the interesting caretaker is the
        one who may upload photos and may not state vacancy, because that is
        where a UI that shows the wrong buttons becomes visible.
        """
        properties = list(
            Property.all_objects.filter(campus_distances__university=university).distinct()[:6]
        )

        subsets = [
            [CaretakerPermission.MANAGE_PHOTOS],
            [CaretakerPermission.MANAGE_VACANCY, CaretakerPermission.RESPOND_INQUIRIES],
            [
                CaretakerPermission.MANAGE_UNITS,
                CaretakerPermission.MANAGE_VACANCY,
                CaretakerPermission.MANAGE_PHOTOS,
                CaretakerPermission.SET_AVAILABILITY,
                CaretakerPermission.RESOLVE_TENANCY_CLAIMS,
                CaretakerPermission.RESPOND_INQUIRIES,
            ],
        ]

        for index, prop in enumerate(properties):
            user = self.make_user(f"caretaker-{university.subdomain}-", index)
            CaretakerAssignment.all_objects.create(
                user=user,
                property=prop,
                granted_by=prop.landlord.user,
                permissions=list(subsets[index % len(subsets)]),
                # One revoked, because revocation is a flag rather than a
                # delete and the difference has to be visible in real data.
                is_active=index != 0,
                revoked_at=self.now if index == 0 else None,
                revoked_by=prop.landlord.user if index == 0 else None,
            )

    # -- history -----------------------------------------------------------

    def make_history(self, prop: Property, units, students, landlord, index: int) -> None:
        """Tenancies, claims and reviews with the shapes only history produces.

        The point of this method is the cases a per-assertion fixture never
        creates:

        - a stay that **ended**, so currency has to be derived rather than read;
        - an **open-ended** stay with a null `end_date`, which means running
          and not unknown;
        - an **upcoming** stay, which is confirmed but not yet current;
        - one **early termination**, where `end_date` was rewritten;
        - a student who **moved between two units in the same block**, which is
          what makes `student_count` and `review_count` legitimately disagree.
        """
        if prop.status != PropertyStatus.PUBLISHED or not units:
            return

        unit = units[0]
        pool = self.random.sample(students, min(len(students), 6))

        # Concurrent stays go in a unit that can hold them.
        #
        # The first version ignored capacity and put a current stay and an
        # open-ended stay in the same one-room unit -- two people in a room
        # that holds one. **The occupancy cross-check found it**, which is the
        # first thing that check has ever had enough data to say. Seeded
        # impossibilities are still impossibilities, and a seed that produces
        # them teaches the wrong thing about every screen built against it.

        # 1. A finished stay, long enough to review.
        past_tenant = pool[0]
        past = self.tenancy(
            unit,
            past_tenant,
            start=self.today - dt.timedelta(days=420),
            end=self.today - dt.timedelta(days=60),
            # Older stays predate the platform more often than newer ones, so
            # they arrive as claims.
            via_claim=True,
        )

        # 2. A current stay with an agreed end.
        self.tenancy(
            unit,
            pool[1],
            start=self.today - dt.timedelta(days=120),
            end=self.today + dt.timedelta(days=120),
        )

        # 3. An open-ended stay. `end_date` null means running, not unknown --
        #    the single most likely misread in the whole contract.
        #
        #    **Guaranteed, not sampled.** It needs a unit that can hold another
        #    body at the same time, and gating that on a modulus meant a small
        #    `--properties` produced no open-ended stay at all: the branch went
        #    unrendered in exactly the runs a developer does most often. So the
        #    first roomy unit always gets one, and the modulus only adds more.
        roomy = next((candidate for candidate in units if candidate.total_count > 1), None)
        if roomy is not None and (not self.seeded_open_ended or index % 3 == 0):
            self.seeded_open_ended = True
            self.tenancy(
                roomy,
                pool[2],
                start=self.today - dt.timedelta(days=200),
                end=None,
                via_claim=True,
            )

        # 4. An upcoming stay: confirmed, not yet started. Safe in a single
        #    room too -- it starts after the current stay's agreed end.
        if index % 4 == 1:
            self.tenancy(
                unit,
                pool[3],
                start=self.today + dt.timedelta(days=150),
                end=self.today + dt.timedelta(days=400),
            )

        # 5. An early termination: the end date was rewritten to the actual
        #    move-out day and stays authoritative for currency. Its dates are
        #    entirely in the past, so it never competes for a room.
        #
        #    Guaranteed once, then varied -- like the open-ended stay, and for
        #    the same reason: a modulus makes a rare shape a function of how
        #    many properties somebody asked for.
        if not self.seeded_termination or index % 5 == 2:
            self.seeded_termination = True
            terminated = self.tenancy(
                unit,
                pool[4],
                start=self.today - dt.timedelta(days=300),
                end=self.today - dt.timedelta(days=95),
                via_claim=True,
            )
            terminated.terminated_early = True
            terminated.termination_reason = "Moved closer to campus."
            terminated.save(update_fields=["terminated_early", "termination_reason", "updated_at"])

        # 6. A student who moved between two units in the same block. Two
        #    genuine reviews, one voice -- the divergence the de-duplication
        #    exists for, and the shape no single-purpose fixture produces.
        if len(units) > 1 and index % 3 == 1:
            mover = pool[5]
            first = self.tenancy(
                units[0],
                mover,
                start=self.today - dt.timedelta(days=500),
                end=self.today - dt.timedelta(days=260),
                via_claim=True,
            )
            second = self.tenancy(
                units[1],
                mover,
                start=self.today - dt.timedelta(days=250),
                end=self.today - dt.timedelta(days=30),
            )
            # `always`, because the mover's two reviews are the entire point
            # of this branch: one student, two stays, one voice. Left to the
            # ordinary skip rule, `index + 1` landed on it every time -- the
            # third place in this file where two independent-looking moduli
            # picked the same rows, and the one that silently made
            # `student_count < review_count` unreachable in the whole seed.
            self.review(first, landlord, index, always=True)
            self.review(second, landlord, index + 1, always=True)

        self.review(past, landlord, index)

        # Claimants come from students who do NOT already have a stay in this
        # unit. `tenancy_no_overlapping_stay` is unconditional and covers unit
        # AND tenant, so seeding a claim for somebody already tenanted here is
        # a double-booking -- which is the constraint working, and a shape no
        # fixture produces because no fixture puts one person in one unit
        # twice by accident.
        available = [student for student in students if student not in pool]
        self.make_claims(unit, available or students, landlord, index)

    def tenancy(self, unit: Unit, tenant: User, *, start, end, via_claim: bool = False) -> Tenancy:
        """One confirmed stay, **with the origin it must have**.

        A tenancy either came from an application the platform witnessed or
        from a claim somebody made -- never both and never neither, which is a
        check constraint rather than a convention (ADR-004 §1.1). The first
        version of this helper created tenancies with no origin at all, and
        the constraint refused every one of them: a shape no test fixture had
        ever produced, because every fixture builds the origin it needs.

        Both paths appear here on purpose. An application-sourced stay has no
        confirmation window and no dispute surface; a claim-sourced one has
        both, and the difference is most of the tenancy state machine.
        """
        review_eligible = start + dt.timedelta(days=settings.REVIEW_MINIMUM_STAY_DAYS)

        if via_claim:
            claim = TenancyClaim.all_objects.create(
                unit=unit,
                claimant=tenant,
                start_date=start,
                end_date=end,
                monthly_rent_kes=unit.rent_kes,
                status=ClaimStatus.CONFIRMED,
                confirmation_deadline=self.now - dt.timedelta(days=3),
                resolved_at=self.now - dt.timedelta(days=3),
                resolved_by=unit.property.landlord.user,
            )
            return Tenancy.all_objects.create(
                unit=unit,
                tenant=tenant,
                claim=claim,
                confirmation_source=ConfirmationSource.LANDLORD,
                confirmed_by=unit.property.landlord.user,
                confirmed_at=self.now - dt.timedelta(days=3),
                start_date=start,
                end_date=end,
                monthly_rent_kes=unit.rent_kes,
                status=TenancyStatus.CONFIRMED,
                review_eligible_at=review_eligible,
            )

        application = Application.all_objects.create(
            unit=unit,
            applicant=tenant,
            status=ApplicationStatus.ACCEPTED,
            move_in_date=start,
            intended_months=max(1, ((end or self.today) - start).days // 30),
            message="Starting the new semester.",
            decided_by=unit.property.landlord.user,
            decided_at=self.now - dt.timedelta(days=2),
            decision_note="See you on move-in day.",
        )
        return Tenancy.all_objects.create(
            unit=unit,
            tenant=tenant,
            application=application,
            confirmation_source=ConfirmationSource.APPLICATION,
            confirmed_by=unit.property.landlord.user,
            confirmed_at=self.now - dt.timedelta(days=2),
            start_date=start,
            end_date=end,
            monthly_rent_kes=unit.rent_kes,
            status=TenancyStatus.CONFIRMED,
            review_eligible_at=review_eligible,
        )

    def review(
        self, tenancy: Tenancy, landlord, index: int, *, always: bool = False
    ) -> Review | None:
        """A review, sometimes with a reply, sometimes on a disputed stay.

        Ratings are spread rather than clustered: an all-fives seed makes the
        distribution bars, the average, and every "is this good?" judgement in
        the UI untestable by eye.
        """
        if index % 3 == 2 and not always:
            return None  # some properties have none, which is a real state

        rating = self.random.choice([2, 3, 3, 4, 4, 4, 5, 5, 1])
        review = Review.all_objects.create(
            tenancy=tenancy,
            rating=rating,
            cleanliness_rating=self.random.choice([None, rating, max(1, rating - 1)]),
            security_rating=self.random.choice([None, rating, min(5, rating + 1)]),
            water_reliability_rating=self.random.choice([None, 2, 3, 4]),
            landlord_rating=self.random.choice([None, rating]),
            value_rating=self.random.choice([None, rating]),
            comment=self.random.choice(
                [
                    "Water goes off most Thursdays but the caretaker is quick about it.",
                    "Gate is locked by 10pm which I appreciated. Rent went up after a year.",
                    "Landlord fixed the shower the same week I asked.",
                    "Noisy, and the county water is unreliable. The tank helps.",
                    "",
                ]
            ),
            would_recommend=rating >= 3,
        )

        if index % 4 == 0:
            ReviewResponse.all_objects.create(
                review=review,
                author=landlord.user,
                body="Thank you. The tank was replaced in June and the pressure is better now.",
            )

        return review

    def make_claims(self, unit: Unit, students, landlord, index: int) -> None:
        """Claims in every state the machine can reach.

        Including each typed dispute reason, so the admin queue has real rows
        and the transition table is exercised by data rather than only by
        tests. A queue that is empty in development is a queue nobody notices
        is badly sorted.
        """
        claimant = students[(index * 3) % len(students)]
        base = {
            "unit": unit,
            "monthly_rent_kes": unit.rent_kes,
            "start_date": self.today - dt.timedelta(days=400),
            "end_date": self.today - dt.timedelta(days=100),
        }

        # A counter, not a modulus of `index`.
        #
        # This file derived five independent variations from one index --
        # draft, unpinned, no-photos, failed-photo, claim state -- and every
        # one of them collided with another at some `--properties` size. A
        # stride fixed each collision and produced the next: the claim states
        # only cycled over properties that *reached* this method, so a draft
        # in the wrong place skipped a state entirely and the seeded queue
        # silently lost a dispute reason.
        #
        # Counting the calls removes the interference by construction. Every
        # state is reached in order, whatever the caller skipped on the way.
        state = self.claim_state % 6
        self.claim_state += 1

        if state == 0:
            TenancyClaim.all_objects.create(
                claimant=claimant,
                status=ClaimStatus.PENDING,
                confirmation_deadline=self.now + dt.timedelta(days=5),
                **base,
            )
        elif state == 1:
            # Auto-confirmed by silence. `resolved_by` is null on purpose:
            # silence has no author.
            claim = TenancyClaim.all_objects.create(
                claimant=claimant,
                status=ClaimStatus.CONFIRMED,
                confirmation_deadline=self.now - dt.timedelta(days=2),
                resolved_at=self.now - dt.timedelta(days=2),
                **base,
            )
            Tenancy.all_objects.create(
                unit=unit,
                tenant=claimant,
                claim=claim,
                confirmation_source=ConfirmationSource.AUTO,
                confirmed_at=self.now - dt.timedelta(days=2),
                start_date=claim.start_date,
                end_date=claim.end_date,
                monthly_rent_kes=claim.monthly_rent_kes,
                status=TenancyStatus.CONFIRMED,
            )
        elif state in (2, 3, 4):
            reason = [
                DisputeReason.DATES_INCORRECT,
                DisputeReason.NEVER_TENANTED,
                DisputeReason.DUPLICATE,
            ][state - 2]
            TenancyClaim.all_objects.create(
                claimant=claimant,
                status=ClaimStatus.DISPUTED,
                confirmation_deadline=self.now + dt.timedelta(days=3),
                dispute_reason=reason,
                dispute_note="This does not match my records.",
                disputed_by=landlord.user,
                disputed_at=self.now - dt.timedelta(days=1),
                proposed_start_date=(
                    self.today - dt.timedelta(days=380)
                    if reason == DisputeReason.DATES_INCORRECT
                    else None
                ),
                proposed_end_date=(
                    self.today - dt.timedelta(days=120)
                    if reason == DisputeReason.DATES_INCORRECT
                    else None
                ),
                **base,
            )
        else:
            # Escalated, sitting in the admin queue with a deadline.
            TenancyClaim.all_objects.create(
                claimant=claimant,
                status=ClaimStatus.ESCALATED,
                confirmation_deadline=self.now - dt.timedelta(days=1),
                dispute_reason=DisputeReason.NEVER_TENANTED,
                dispute_note="I have never had a tenant by this name.",
                disputed_by=landlord.user,
                disputed_at=self.now - dt.timedelta(days=6),
                escalation_reason=EscalationReason.IDENTITY_DISPUTED,
                escalated_at=self.now - dt.timedelta(days=5),
                escalation_deadline=self.now + dt.timedelta(days=8),
                **base,
            )

    def build_aggregates(self) -> None:
        """Compute the rating caches the seeded reviews imply.

        Reviews are written straight to the model here rather than through
        `create_review`, because the gate correctly refuses most seeded
        tenancies -- so the enqueue that normally follows a review never runs.
        Without this the platform has 33 reviews and no aggregates, which is a
        state a real deployment can also reach (a queue that was down, a
        restore that brought reviews without their caches) and which the
        reconciler now reports as `missing` rather than as health.

        Built explicitly rather than left broken: a seed whose every rating
        endpoint returns "no reviews yet" would make the whole review surface
        untestable by hand.
        """
        from reviews.recompute import recompute_landlord, recompute_property, recompute_unit

        for review in Review.all_objects.select_related("tenancy__unit__property").all():
            unit = review.tenancy.unit
            recompute_unit(unit.pk)
            recompute_property(unit.property_id)
            recompute_landlord(unit.property.landlord_id)

    # -- report ------------------------------------------------------------

    def report(self) -> None:
        """What was made, and the one figure worth reading.

        Cross-check coverage is printed because "no contradictions" over a
        catalogue with no tenancy records means "nothing was checked", and a
        seed command is exactly where somebody would first mistake the two.
        """
        from properties.services import cross_check_coverage

        rows = [
            ("universities", University.objects.count()),
            ("properties", Property.all_objects.count()),
            ("  published", Property.all_objects.filter(status=PropertyStatus.PUBLISHED).count()),
            ("  unpinned drafts", Property.all_objects.filter(latitude=None).count()),
            ("units", Unit.all_objects.count()),
            ("photos", UnitPhoto.all_objects.count()),
            ("students", StudentProfile.all_objects.count()),
            ("landlords", LandlordProfile.objects.count()),
            ("caretaker assignments", CaretakerAssignment.all_objects.count()),
            ("tenancies", Tenancy.all_objects.count()),
            ("  current", Tenancy.all_objects.current().count()),
            ("  past", Tenancy.all_objects.past().count()),
            ("  upcoming", Tenancy.all_objects.upcoming().count()),
            ("  open-ended", Tenancy.all_objects.filter(end_date=None).count()),
            ("claims", TenancyClaim.all_objects.count()),
            ("  pending", TenancyClaim.all_objects.filter(status=ClaimStatus.PENDING).count()),
            ("  disputed", TenancyClaim.all_objects.filter(status=ClaimStatus.DISPUTED).count()),
            ("  escalated", TenancyClaim.all_objects.filter(status=ClaimStatus.ESCALATED).count()),
            ("reviews", Review.all_objects.count()),
            ("review responses", ReviewResponse.all_objects.count()),
            ("rating aggregates", PropertyRatingAggregate.all_objects.count()),
            ("inquiries", Inquiry.all_objects.count()),
            ("  answered", Inquiry.all_objects.filter(status=InquiryStatus.ANSWERED).count()),
            ("saved properties", SavedProperty.all_objects.count()),
        ]

        for label, value in rows:
            self.stdout.write(f"{label:>24}  {value}")

        coverage = cross_check_coverage()
        self.stdout.write("")
        self.stdout.write(
            self.style.SUCCESS(
                f"occupancy cross-check: {coverage['informative']} of {coverage['units']} units "
                f"have enough tenancy records to say anything "
                f"({coverage['contradictions']} contradictions)"
            )
        )
