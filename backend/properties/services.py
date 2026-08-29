"""
Rules the database cannot express for properties.

Everything that can be a constraint is one. What lands here spans tables or
depends on related rows, which a PostgreSQL check constraint cannot see.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from .constants import PhotoProcessingStatus, PropertyStatus, VacancyFreshness
from .jobs import enqueue_photo_variants
from .models import Property, PropertyCampusDistance, Unit, UnitPhoto


class PropertyNotPublishableError(ValidationError):
    """Publishing this property would produce a listing nobody can see."""


def assert_property_is_publishable(property_obj: Property) -> None:
    """Refuse to publish a property that no tenant could reach.

    Two rules, both of which span rows and so cannot be constraints:

    **Coordinates.** ``PropertyCampusDistance`` computes ``straight_line_km`` by
    haversine on save and the column is NOT NULL, so an unpinned property
    cannot join a campus at all. Since the join is what makes a property visible
    to a university (ADR-002), publishing without coordinates produces a listing
    the landlord can see and nobody else can — a silent failure that looks like
    low demand.

    **At least one campus join.** Same consequence, arrived at differently: a
    property with coordinates but no join rows is still invisible. No constraint
    can express "at least one related row".
    """
    missing: dict[str, Any] = {}

    if property_obj.latitude is None or property_obj.longitude is None:
        missing["latitude"] = _(
            "Set the property's coordinates before publishing. Without them it "
            "cannot be matched to a campus, so no student would ever see it."
        )

    if not property_obj.campus_distances.exists():
        missing["campus_distances"] = _(
            "This property is not within reach of any campus we serve, so no "
            "student would see it. Check the coordinates -- the widest "
            "catchment any campus has is %(radius).0f km."
        ) % {"radius": _widest_join_radius()}

    if missing:
        raise PropertyNotPublishableError(missing)


def publish(property_obj: Property) -> Property:
    """Publish a property, or refuse with a named reason.

    The single write path. ``published_at`` is set here because the model
    constraint requires it and nothing else should be choosing that timestamp.

    The campus joins are created here first. The gate below requires at least
    one, and until this line existed nothing in the product ever made one --
    so the gate was unsatisfiable by any available action, and every landlord
    who pinned a property correctly was refused with a message telling them to
    do something they could not do.
    """
    backfill_property_joins(property_obj)

    assert_property_is_publishable(property_obj)

    property_obj.status = PropertyStatus.PUBLISHED
    property_obj.published_at = property_obj.published_at or timezone.now()
    property_obj.save(update_fields=["status", "published_at", "updated_at"])
    return property_obj


# ---------------------------------------------------------------------------
# Vacancy provenance (ADR-002)
# ---------------------------------------------------------------------------


def vacancy_age_days(unit: Unit, *, now: dt.datetime | None = None) -> int | None:
    """How long since the landlord last stated this unit's vacancy.

    ``None`` when they never have. Distinct from a large number: "nobody has
    ever said" and "somebody said, long ago" are different facts and the UI
    words them differently.
    """
    if unit.vacant_count_updated_at is None:
        return None
    return ((now or timezone.now()) - unit.vacant_count_updated_at).days


def vacancy_freshness(unit: Unit, *, now: dt.datetime | None = None) -> str:
    """Which band this unit's vacancy count falls into.

    **Computed here and only here.** The API sends both this band and the raw
    age, and the client renders the band rather than re-deriving it -- a
    threshold in the client and a threshold in settings is two places for one
    rule, and `docs/OPERATIONS.md` records five occasions where the wrong copy
    won.
    """
    age = vacancy_age_days(unit, now=now)

    if age is None:
        return VacancyFreshness.UNKNOWN
    if age <= settings.VACANCY_FRESH_DAYS:
        return VacancyFreshness.FRESH
    if age <= settings.VACANCY_STALE_DAYS:
        return VacancyFreshness.AGEING
    return VacancyFreshness.STALE


@transaction.atomic
def state_vacancy(
    unit: Unit, *, vacant_count: int, stated_by, now: dt.datetime | None = None
) -> Unit:
    """The single write path for ``vacant_count``.

    Every write stamps the timestamp and the author together, so the three can
    never disagree. A bare ``unit.vacant_count = n; unit.save()`` elsewhere
    would leave a fresh number wearing an old date -- worse than a stale
    number, because the staleness signal would say it is current.
    """
    if vacant_count > unit.total_count:
        raise ValidationError(
            {
                "vacant_count": _("This unit has %(total)d rooms, so %(vacant)d cannot be free.")
                % {"total": unit.total_count, "vacant": vacant_count}
            }
        )
    if vacant_count < 0:
        raise ValidationError({"vacant_count": _("A vacancy count cannot be negative.")})

    unit.vacant_count = vacant_count
    unit.vacant_count_updated_at = now or timezone.now()
    unit.vacant_count_updated_by = stated_by
    unit.save(
        update_fields=[
            "vacant_count",
            "vacant_count_updated_at",
            "vacant_count_updated_by",
            "updated_at",
        ]
    )
    return unit


def units_with_stale_vacancy(now: dt.datetime | None = None):
    """Active units on published properties whose count has aged out.

    Includes units that have **never** been stated: a listing that has never
    said how many rooms are free is at least as misleading as one that said so
    two months ago, and the prompt is the same either way.
    """
    now = now or timezone.now()
    cutoff = now - dt.timedelta(days=settings.VACANCY_STALE_DAYS)

    return Unit.all_objects.filter(
        is_active=True, property__status=PropertyStatus.PUBLISHED
    ).filter(Q(vacant_count_updated_at__isnull=True) | Q(vacant_count_updated_at__lt=cutoff))


# ---------------------------------------------------------------------------
# Occupancy cross-check (ADR-002)
# ---------------------------------------------------------------------------
#
# A derived count, computed FOR COMPARISON ONLY. It never writes to
# `vacant_count`, never appears as an availability figure, and is not an
# alternative source of truth -- it is a way of noticing that the stated number
# and the tenancy records contradict each other.
#
# The asymmetry below is the whole design. Derived ABOVE stated is impossible
# and therefore a real signal. Derived BELOW stated is the normal case for the
# entire seeding period, and for ever after wherever a landlord lets rooms
# off-platform -- alerting on it would train everyone to ignore the alert,
# which costs more than the alert was ever worth.


@dataclass(frozen=True)
class OccupancyComparison:
    """What the tenancy records say about one unit, beside what it claims."""

    unit_id: int
    total_count: int
    stated_vacant: int
    #: Confirmed tenancies currently running in this unit.
    derived_occupied: int

    @property
    def stated_occupied(self) -> int:
        return self.total_count - self.stated_vacant

    @property
    def is_contradiction(self) -> bool:
        """More people confirmed in residence than the unit has rooms.

        Not "more than are stated occupied" -- that is merely the landlord
        being behind. This is a physical impossibility, and physical
        impossibilities are worth someone's attention.
        """
        return self.derived_occupied > self.total_count

    @property
    def is_informative(self) -> bool:
        """Whether the cross-check can say anything at all about this unit.

        A unit with no confirmed current tenancies tells us nothing: the rooms
        may be empty, or fully let off-platform, and the derived figure cannot
        distinguish those. Reported separately so "no contradictions found"
        is never mistaken for "everything checks out".
        """
        return self.derived_occupied > 0


def compare_occupancy(units=None, *, today: dt.date | None = None) -> list[OccupancyComparison]:
    """Derived-versus-stated occupancy, for every unit.

    One grouped query rather than one per unit: this runs over the whole
    catalogue and a per-unit count would make it quadratic in listings.
    """
    from django.db.models import Count

    from tenancies.models import Tenancy

    queryset = units if units is not None else Unit.all_objects.filter(is_active=True)
    unit_ids = list(queryset.values_list("pk", flat=True))

    occupied = dict(
        Tenancy.all_objects.filter(unit_id__in=unit_ids)
        .current(today=today)
        .values("unit_id")
        .annotate(total=Count("pk"))
        .values_list("unit_id", "total")
    )

    return [
        OccupancyComparison(
            unit_id=unit.pk,
            total_count=unit.total_count,
            stated_vacant=unit.vacant_count,
            derived_occupied=occupied.get(unit.pk, 0),
        )
        for unit in queryset
    ]


def occupancy_contradictions(units=None, *, today: dt.date | None = None):
    """Units where confirmed current tenancies exceed stated capacity.

    The only direction worth flagging. It means one of three things and all of
    them are worth a person looking: the capacity is wrong, a tenancy that
    ended was never closed, or somebody is letting more rooms than they have.
    """
    return [row for row in compare_occupancy(units, today=today) if row.is_contradiction]


def cross_check_coverage(units=None, *, today: dt.date | None = None) -> dict[str, int]:
    """How much of the catalogue the cross-check can speak to at all.

    **Reported rather than assumed.** Early on this is close to zero: almost
    no unit has a confirmed on-platform tenancy, so "no contradictions found"
    means "nothing was checked", and reporting the first as though it were a
    clean bill of health is exactly the shape `docs/OPERATIONS.md` catalogues.
    """
    rows = compare_occupancy(units, today=today)
    informative = [row for row in rows if row.is_informative]

    return {
        "units": len(rows),
        "informative": len(informative),
        "contradictions": len([row for row in informative if row.is_contradiction]),
    }


# ---------------------------------------------------------------------------
# The landlord and caretaker write surface (ADR-002, ADR-003)
# ---------------------------------------------------------------------------
#
# Every write below is a named function rather than a serializer `.save()`,
# for the reason the rest of this module exists: the rules that span rows
# cannot be constraints, and a rule enforced only in a serializer is a rule the
# admin, a management command and a future job all miss.
#
# `vacant_count` in particular never appears in a writable serializer. It has
# exactly one write path -- `state_vacancy` -- because a bare field write
# leaves a fresh number wearing an old timestamp, and `docs/OPERATIONS.md`
# records the admin arriving at that door once already.


def _unique_slug(name: str, *, exclude_pk: int | None = None) -> str:
    """A slug nobody else holds.

    Suffixed rather than rejected: two landlords near the same campus
    genuinely do both call a block "Sunrise Apartments", and refusing the
    second would be the platform telling a landlord their building has the
    wrong name.
    """
    base = slugify(name)[:200] or "property"
    candidate = base

    existing = Property.all_objects.exclude(pk=exclude_pk) if exclude_pk else Property.all_objects
    suffix = 2
    while existing.filter(slug=candidate).exists():
        candidate = f"{base}-{suffix}"
        suffix += 1

    return candidate


@transaction.atomic
def create_property(*, landlord, **fields) -> Property:
    """Create a property, always as a draft.

    **Never published on create**, whatever the payload says. Publication has
    a gate (`assert_property_is_publishable`) and a property created straight
    into `PUBLISHED` would either bypass it or fail confusingly half way
    through a create. Two steps, one of which can refuse.
    """
    return Property.all_objects.create(
        landlord=landlord,
        slug=_unique_slug(fields.get("name", "")),
        status=PropertyStatus.DRAFT,
        **fields,
    )


@transaction.atomic
def update_property(property_obj: Property, **fields) -> Property:
    """Edit a property's own details.

    The slug follows the name only while the property is a draft. Once
    published the URL is in somebody's saved list and in a message a landlord
    sent a student, and silently moving it turns both into a 404.
    """
    for name, value in fields.items():
        setattr(property_obj, name, value)

    if "name" in fields and property_obj.status == PropertyStatus.DRAFT:
        property_obj.slug = _unique_slug(fields["name"], exclude_pk=property_obj.pk)

    property_obj.full_clean(exclude=["landlord"])
    property_obj.save()
    return property_obj


def unpublish(property_obj: Property) -> Property:
    """Take a listing down without deleting it.

    A property with tenancies against it is a record other people rely on, so
    the way off the site is a status change rather than a delete. Draft is
    also the state the publish gate can be re-run from.
    """
    property_obj.status = PropertyStatus.DRAFT
    property_obj.save(update_fields=["status", "updated_at"])
    return property_obj


@transaction.atomic
def create_unit(*, property_obj: Property, **fields) -> Unit:
    """Add a unit to a property.

    `vacant_count` is deliberately not settable here. A new unit starts at
    zero free rooms with no provenance -- `vacancy_freshness` reads `unknown`,
    which is the truth: nobody has stated anything yet. Accepting a count on
    create would let it be stated without being stamped.
    """
    unit = Unit(property=property_obj, **fields)
    unit.full_clean(exclude=["property"])
    unit.save()
    return unit


@transaction.atomic
def update_unit(unit: Unit, **fields) -> Unit:
    """Edit a unit's details.

    Refuses `vacant_count` loudly rather than ignoring it. A silently dropped
    field is how a caller comes to believe they set something they did not,
    and this is the one field where that belief becomes a listing that lies.
    """
    if "vacant_count" in fields:
        raise ValidationError(
            {
                "vacant_count": _(
                    "Vacancy is set through its own endpoint, so the count is "
                    "always stamped with who said it and when."
                )
            }
        )

    for name, value in fields.items():
        setattr(unit, name, value)

    unit.full_clean(exclude=["property"])
    unit.save()
    return unit


def set_availability(unit: Unit, *, available_from=None, is_active: bool | None = None) -> Unit:
    """When a unit becomes available, and whether it is listed at all.

    Separate from `update_unit` because it is separately delegable: a
    caretaker may be trusted to say a room is off the market without being
    trusted to change its rent.
    """
    if available_from is not None:
        unit.available_from = available_from
    if is_active is not None:
        unit.is_active = is_active

    unit.save(update_fields=["available_from", "is_active", "updated_at"])
    return unit


# ---------------------------------------------------------------------------
# Photos (ADR-007)
# ---------------------------------------------------------------------------


#: What a landlord may upload.
#:
#: Checked against the file's **leading bytes**, never against what the client
#: says it sent. `Content-Type` on a multipart part is supplied by the client
#: exactly as the filename is, so trusting it is trusting the uploader -- and
#: the earlier version of this did, while its comment claimed the opposite.
ALLOWED_PHOTO_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


@transaction.atomic
def add_photo(*, unit: Unit, upload, caption: str = "", uploaded_by=None) -> UnitPhoto:
    """Store one photo and queue its variants.

    The row is created `pending` with no variants, which the API already
    models honestly: `url` falls back to the original, so a slow queue costs
    page weight rather than a broken image.

    The first photo on a unit becomes primary. Not "the lowest sort_order" --
    a unit whose only photo is not primary has no cover, and the listing card
    then shows "No photos yet" beside a unit that plainly has one.
    """
    # Sniffed, not asked. `upload.content_type` is a header the client wrote;
    # a PDF announcing itself as `image/jpeg` passed the previous check and was
    # stored under a `.jpg` key, to be served from the public bucket to
    # whoever opened the listing. The identity-document path has sniffed since
    # ADR-003 for the same reason -- this one only said it did.
    from accounts.documents import sniff_content_type

    head = upload.read(32)
    upload.seek(0)

    content_type = sniff_content_type(head) or ""
    extension = ALLOWED_PHOTO_TYPES.get(content_type)

    if extension is None:
        claimed = getattr(upload, "content_type", "") or ""
        raise ValidationError(
            {
                "image": _(
                    "Upload a JPEG, PNG or WebP image. This file's contents "
                    "are %(actual)s, whatever it is named, and the resize "
                    "step would fail later rather than here."
                )
                % {"actual": content_type or claimed or _("not a recognised image")}
            }
        )

    if upload.size > settings.MAX_PHOTO_BYTES:
        raise ValidationError(
            {
                "image": _(
                    "That photo is %(size).1f MB. The limit is %(limit).1f MB "
                    "-- a phone photo is usually well under it."
                )
                % {
                    "size": upload.size / 1_000_000,
                    "limit": settings.MAX_PHOTO_BYTES / 1_000_000,
                }
            }
        )

    # Metadata comes off before anything is stored.
    #
    # A landlord's phone photo carries GPS, a device make and model, and a
    # timestamp. The variants drop all of it as a side effect of being
    # re-encoded -- but `best_url` falls back to the **original** until the
    # resize job has run, and permanently if it fails, and the original lives
    # in the *public* bucket. So every listing photo was served with its
    # EXIF intact for at least the length of the queue, and for ever for the
    # ones that failed to resize.
    #
    # The identity-document path has stripped since ADR-003. This is the same
    # tool, now cheap enough to use here: 0.28s on a 4 MB photo rather than
    # the 15.7s it cost before 85dfab8.
    from accounts.documents import strip_image_metadata

    upload.seek(0)
    try:
        clean = strip_image_metadata(upload.read(), content_type)
    except OSError as error:
        # A file with a valid header and missing body -- what a dropped upload
        # leaves behind. The sniff above cannot catch it, because the header is
        # genuinely a PNG's.
        #
        # Refused here rather than stored. Before the strip existed this was
        # accepted, stored, and failed in the resize job: a photo the landlord
        # believed they had uploaded, sitting `failed` in a queue they cannot
        # see, with `best_url` serving the broken original. Failing at the
        # point of upload is the difference between "try again" and "why is my
        # listing like this".
        raise ValidationError(
            {
                "image": _(
                    "That image could not be read -- it looks incomplete. If "
                    "the upload was interrupted, try it again."
                )
            }
        ) from error
    upload.seek(0)

    key = f"units/{unit.pk}/{uuid4().hex}.{extension}"
    storages["default"].save(key, ContentFile(clean))

    last = UnitPhoto.all_objects.filter(unit=unit).order_by("-sort_order").first()

    photo = UnitPhoto.all_objects.create(
        unit=unit,
        original_key=key,
        caption=caption,
        sort_order=(last.sort_order + 1) if last else 0,
        is_primary=last is None,
        processing_status=PhotoProcessingStatus.PENDING,
        # The stripped size, which is what is actually stored. Reporting the
        # upload's size would describe a file that no longer exists.
        byte_size=len(clean),
    )

    # After commit: enqueuing inside the transaction races the worker, which
    # can pick the job up and find no row.
    transaction.on_commit(lambda: enqueue_photo_variants(photo.pk))

    return photo


@transaction.atomic
def reorder_photos(*, unit: Unit, ordered_ids: list[int]) -> list[UnitPhoto]:
    """Set the order of a unit's photos, and with it the cover.

    The whole order is sent rather than a move-one-up operation, because two
    tabs each nudging a photo produce an order neither of them chose. A full
    list is last-write-wins on something the writer could see.

    Rejects a partial list: a caller sending three of five ids has a stale
    view of the unit, and applying it would silently discard the other two.
    """
    photos = {photo.pk: photo for photo in UnitPhoto.all_objects.filter(unit=unit)}

    if set(ordered_ids) != set(photos):
        raise ValidationError(
            {
                "photo_ids": _(
                    "Send every photo on this unit, in the order you want "
                    "them. Your list has %(given)d of %(actual)d -- someone "
                    "may have added or removed one since this page loaded."
                )
                % {"given": len(set(ordered_ids)), "actual": len(photos)}
            }
        )

    # Clear every primary flag before setting the new one. `UnitPhoto` has a
    # partial unique constraint of one primary per unit and it is not
    # deferrable, so setting the new cover while the old one still holds the
    # flag is an IntegrityError mid-transaction -- which surfaces as a 409 on
    # an operation the caller did nothing wrong in.
    UnitPhoto.all_objects.filter(unit=unit, is_primary=True).update(is_primary=False)

    for position, photo_id in enumerate(ordered_ids):
        photo = photos[photo_id]
        photo.sort_order = position
        photo.is_primary = False
        photo.save(update_fields=["sort_order", "is_primary", "updated_at"])

    # First is the cover. Set last, and stated once, so "primary" and "first"
    # cannot come to disagree.
    cover = photos[ordered_ids[0]]
    cover.is_primary = True
    cover.save(update_fields=["is_primary", "updated_at"])

    return [photos[photo_id] for photo_id in ordered_ids]


@transaction.atomic
def delete_photo(photo: UnitPhoto) -> None:
    """Remove a photo, and promote a new cover if this was it.

    The object itself is left in the bucket. A delete that also removes the
    file cannot be undone by an operator, and a landlord deleting the wrong
    photo of a room they no longer have access to has lost it for good;
    retention sweeps object storage separately.
    """
    unit = photo.unit
    was_primary = photo.is_primary
    photo.delete()

    if was_primary:
        replacement = UnitPhoto.all_objects.filter(unit=unit).order_by("sort_order").first()
        if replacement is not None:
            replacement.is_primary = True
            replacement.save(update_fields=["is_primary", "updated_at"])


# ---------------------------------------------------------------------------
# Campus joins (ADR-002)
# ---------------------------------------------------------------------------
#
# The join row is what makes a property visible to a university. Without one
# the property is not "far away" -- it does not exist for that tenant.
#
# `route_stale_distances` walks the rows that exist, so a campus created after
# a property was published never gets one: no row, no routing, and the listing
# is invisible to that campus permanently, with nothing erroring. That is the
# absence-blindness `docs/OPERATIONS.md` describes, and the same
# silent-invisibility failure the publish gate exists to prevent, arriving
# through a different door.


def join_radius_for(campus, radius_km: float | None = None) -> float:
    """The radius that applies to one campus.

    Explicit argument, then the campus's own value, then the platform
    default. **Per campus** because a city campus with dense housing next door
    and a rural one where students commute from the nearest town are not the
    same question, and a single number for both is a decision nobody made --
    the global 15 was a proposal with no basis behind it.
    """
    if radius_km is not None:
        return radius_km
    if getattr(campus, "join_radius_km", None) is not None:
        return campus.join_radius_km
    return settings.CAMPUS_JOIN_RADIUS_KM


def properties_in_range_of(campus, radius_km: float | None = None):
    """Published, pinned properties within the join radius of one campus.

    Filtered by bounding box first and haversine second, which is what
    `PropertyFilter` already does -- a box is an index-friendly prefilter and
    the circle is the answer.
    """
    from .distances import bounding_box, haversine_km

    radius = join_radius_for(campus, radius_km)
    min_lat, max_lat, min_lon, max_lon = bounding_box(campus.latitude, campus.longitude, radius)

    candidates = Property.all_objects.filter(
        status=PropertyStatus.PUBLISHED,
        latitude__isnull=False,
        longitude__isnull=False,
        latitude__gte=min_lat,
        latitude__lte=max_lat,
        longitude__gte=min_lon,
        longitude__lte=max_lon,
    )

    # The queryset already excludes null coordinates; narrowed here so the
    # type checker knows it too, rather than being told to ignore it.
    return [
        prop
        for prop in candidates
        if prop.latitude is not None
        and prop.longitude is not None
        and haversine_km(prop.latitude, prop.longitude, campus.latitude, campus.longitude) <= radius
    ]


def properties_missing_a_join_to(campus, radius_km: float | None = None) -> list[int]:
    """Published properties in range of this campus with **no row at all**.

    Absence, not staleness. `straight_line_km` is computed by haversine on
    save and is NOT NULL, so a row always has a distance -- a property with no
    row has never been joined, which is a different fact from a row whose
    routing is old. Counting the two together would let a permanent
    invisibility hide inside a routing backlog.

    Reported as its own number with its own alert (`docs/OPERATIONS.md`).
    """
    joined = set(
        PropertyCampusDistance.all_objects.filter(campus=campus).values_list(
            "property_id", flat=True
        )
    )

    return [prop.pk for prop in properties_in_range_of(campus, radius_km) if prop.pk not in joined]


def _widest_join_radius() -> float:
    """The largest radius any campus claims, for the bounding-box prefilter."""
    from django.db.models import Max

    from universities.models import Campus

    widest = Campus.all_objects.aggregate(widest=Max("join_radius_km"))["widest"]
    return max(widest or 0.0, settings.CAMPUS_JOIN_RADIUS_KM)


@transaction.atomic
def backfill_property_joins(property_obj: Property, radius_km: float | None = None) -> int:
    """Join one property to every campus in range of it.

    The other direction, and the one that was missing entirely. `publish()`
    refuses a property with no campus join -- correctly, since it would be
    invisible -- and **nothing in the product created one**. The seed wrote
    them directly and the tests built them by hand, so a landlord using the
    write surface could pin a property, satisfy every other rule, and be
    refused for ever by a gate no available action could satisfy.

    Called from `publish()` before the gate runs, so the gate keeps its
    meaning: it now refuses only a property that is genuinely near no campus
    this platform serves, which is a real refusal with a real explanation.
    """
    from universities.models import Campus

    from .distances import bounding_box, haversine_km

    if property_obj.latitude is None or property_obj.longitude is None:
        return 0

    # The property side searches out to the **widest** radius any campus
    # claims, then filters each candidate against that campus's own -- a
    # campus that reaches 40 km must be found from 40 km away, and one that
    # reaches 5 km must not pick up a property 12 km out just because another
    # campus is generous.
    radius = radius_km if radius_km is not None else _widest_join_radius()
    min_lat, max_lat, min_lon, max_lon = bounding_box(
        property_obj.latitude, property_obj.longitude, radius
    )

    joined = set(
        PropertyCampusDistance.all_objects.filter(property=property_obj).values_list(
            "campus_id", flat=True
        )
    )

    created = 0
    for campus in Campus.all_objects.filter(
        latitude__gte=min_lat,
        latitude__lte=max_lat,
        longitude__gte=min_lon,
        longitude__lte=max_lon,
    ).select_related("university"):
        if campus.pk in joined:
            continue
        if haversine_km(
            property_obj.latitude, property_obj.longitude, campus.latitude, campus.longitude
        ) > join_radius_for(campus, radius_km):
            continue

        PropertyCampusDistance.all_objects.create(
            property=property_obj,
            university=campus.university,
            campus=campus,
            is_primary=not joined and created == 0,
        )
        created += 1

    return created


@transaction.atomic
def backfill_campus_joins(campus, radius_km: float | None = None) -> int:
    """Create the missing join rows for one campus.

    **`walking_minutes` is left null.** Only the routing job may fill it
    (ADR-002): a walking time the platform invented is the thing that erodes
    exactly the trust the platform sells, and a straight line quietly promoted
    into a walk is the specific way that happens. The row carries
    `straight_line_km` -- which is computed, not guessed -- and the routing
    sweep picks it up on its next pass because a null `routed_at` sorts first.
    """
    created = 0

    for property_id in properties_missing_a_join_to(campus, radius_km):
        prop = Property.all_objects.get(pk=property_id)
        PropertyCampusDistance.all_objects.create(
            property=prop,
            university=campus.university,
            campus=campus,
            # is_primary only if this property has no other join yet: the
            # first campus a property is joined to is its primary one, and a
            # backfill must not demote an existing choice.
            is_primary=not PropertyCampusDistance.all_objects.filter(property=prop).exists(),
        )
        created += 1

    return created
