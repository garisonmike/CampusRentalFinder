"""
Background jobs for properties (ADR-002, ADR-007).

Three jobs live here. All three fail **silently** if the worker stops — nothing
errors, no request 500s — so all three are monitored on the age of their oldest
unprocessed row rather than on job success. `docs/OPERATIONS.md` states the
thresholds.

No job here invents data. Image variants degrade to serving the original, a
routing failure leaves the walking fields null, and an unsent vacancy prompt
leaves the count where the landlord last put it — ageing visibly, never
silently corrected. `None` and "stale" are supported states, which is the whole
point: an honest gap beats a plausible guess.
"""

from __future__ import annotations

import datetime as dt
import io
from decimal import ROUND_HALF_UP, Decimal

import django_rq
import structlog
from django.core.files.base import ContentFile
from django.core.files.storage import storages
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .constants import PHOTO_VARIANTS, PhotoProcessingStatus
from .models import PropertyCampusDistance, UnitPhoto
from .routing.registry import get_route_provider

logger = structlog.get_logger("campusrental.jobs")


# ---------------------------------------------------------------------------
# Image variants (ADR-007)
# ---------------------------------------------------------------------------


def _variant_key(original_key: str, variant: str) -> str:
    """Derive a variant's object key from the original's.

    Deterministic, so a regenerated variant overwrites its predecessor's slot
    rather than accumulating orphans in the bucket.
    """
    base, _, _extension = original_key.rpartition(".")
    return f"{base or original_key}.{variant}.webp"


def generate_photo_variants(photo_id: int) -> None:
    """Produce thumb/medium/large WebP variants for one photo.

    Enqueued when a photo is uploaded. Until it completes the API serves the
    original, so a slow or failed job costs page weight rather than a broken
    image (ADR-007).
    """
    from PIL import Image

    photo = UnitPhoto.all_objects.filter(pk=photo_id).first()
    if photo is None:
        # Deleted between enqueue and run. Not an error: jobs must be
        # idempotent and tolerate the row being gone.
        logger.info("photo_variants_skipped", photo_id=photo_id, reason="deleted")
        return

    storage = storages["default"]

    try:
        with storage.open(photo.original_key) as handle:
            original = Image.open(handle)
            original.load()

        keys: dict[str, str] = {}
        for variant, longest_edge in PHOTO_VARIANTS.items():
            resized = original.copy()
            resized.thumbnail((longest_edge, longest_edge), Image.Resampling.LANCZOS)

            buffer = io.BytesIO()
            resized.convert("RGB").save(buffer, format="WEBP", quality=82, method=4)

            key = _variant_key(photo.original_key, variant)
            storage.save(key, ContentFile(buffer.getvalue()))
            keys[variant] = key

    except Exception as exc:
        logger.warning("photo_variants_failed", photo_id=photo_id, error=str(exc))
        UnitPhoto.all_objects.filter(pk=photo_id).update(
            processing_status=PhotoProcessingStatus.FAILED,
            processing_error=str(exc)[:255],
        )
        return

    UnitPhoto.all_objects.filter(pk=photo_id).update(
        thumb_key=keys["thumb"],
        medium_key=keys["medium"],
        large_key=keys["large"],
        width=original.width,
        height=original.height,
        processing_status=PhotoProcessingStatus.READY,
        processing_error="",
    )
    logger.info("photo_variants_ready", photo_id=photo_id)


def enqueue_photo_variants(photo_id: int) -> None:
    """Queue variant generation on the media queue."""
    django_rq.get_queue("media").enqueue(generate_photo_variants, photo_id)


# ---------------------------------------------------------------------------
# Campus routing (ADR-002)
# ---------------------------------------------------------------------------


def route_campus_distance(distance_id: int) -> None:
    """Fill in walking distance and time for one property/campus pair.

    **Never falls back to the straight line.** If the provider returns nothing —
    no route, quota exhausted, service down — the walking fields stay null and
    the UI renders an em dash. A fabricated walking time erodes exactly the
    trust the platform is selling (ADR-002).
    """
    join = (
        PropertyCampusDistance.all_objects.select_related("property", "campus")
        .filter(pk=distance_id)
        .first()
    )
    if join is None:
        logger.info("routing_skipped", distance_id=distance_id, reason="deleted")
        return

    if join.property.latitude is None or join.property.longitude is None:
        # Cannot happen through the model, which refuses to save without
        # coordinates, but a job must not assume its caller was careful.
        logger.info("routing_skipped", distance_id=distance_id, reason="no_coordinates")
        return

    provider = get_route_provider()
    result = provider.route(
        (join.property.latitude, join.property.longitude),
        (join.campus.latitude, join.campus.longitude),
    )

    if result is None:
        # Deliberately leaves every routed field untouched. `routed_at` stays
        # null too, so the retry sweep picks this row up again rather than
        # treating it as done.
        logger.info("routing_unavailable", distance_id=distance_id, provider=provider.name)
        return

    with transaction.atomic():
        PropertyCampusDistance.all_objects.filter(pk=distance_id).update(
            walking_distance_km=Decimal(result.distance_km).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ),
            walking_minutes=result.duration_minutes,
            routed_at=timezone.now(),
            route_provider=result.provider,
        )

    logger.info(
        "routing_complete",
        distance_id=distance_id,
        provider=result.provider,
        minutes=result.duration_minutes,
    )


def enqueue_campus_routing(distance_id: int) -> None:
    """Queue routing for one property/campus pair."""
    django_rq.get_queue("default").enqueue(route_campus_distance, distance_id)


def route_stale_distances(limit: int = 100) -> int:
    """Route the rows that have waited longest.

    Scheduled. Takes never-routed rows first, then the oldest ``routed_at`` —
    a new footbridge changes the answer, so routing results age too.

    Returns how many were enqueued, which is what the "routing stalled" alert
    in docs/OPERATIONS.md reads against the backlog.
    """
    # nulls_first is load-bearing. PostgreSQL sorts NULLs LAST in an ascending
    # order, so a plain order_by("routed_at") would hand back the rows that
    # have already been routed and leave the never-routed ones for ever.
    stale = PropertyCampusDistance.all_objects.order_by(
        F("routed_at").asc(nulls_first=True)
    ).values_list("pk", flat=True)[:limit]

    enqueued = 0
    for distance_id in stale:
        enqueue_campus_routing(distance_id)
        enqueued += 1

    logger.info("routing_sweep", enqueued=enqueued)
    return enqueued


# ---------------------------------------------------------------------------
# Vacancy staleness prompts (ADR-002)
# ---------------------------------------------------------------------------


def prompt_stale_vacancies(limit: int = 200, now: dt.datetime | None = None) -> int:
    """Ask landlords whose vacancy counts have aged out to restate them.

    `vacant_count` is landlord-stated and never derived, which makes the
    landlord the only person who can refresh it. If nobody asks them, nobody
    does -- and the listing goes on advertising last term's vacancies.

    Grouped by property rather than sent per unit: a landlord with forty units
    should get one message, not forty. A prompt that arrives as a flood is a
    prompt that gets filtered.

    Returns the number of landlords prompted, which is what the alert reads.
    """
    from collections import defaultdict

    from .services import units_with_stale_vacancy

    now = now or timezone.now()
    stale = (
        units_with_stale_vacancy(now)
        .select_related("property__landlord__user")
        .order_by("vacant_count_updated_at")[:limit]
    )

    by_landlord: dict[int, list] = defaultdict(list)
    for unit in stale:
        by_landlord[unit.property.landlord_id].append(unit)

    for units in by_landlord.values():
        _send_vacancy_prompt(units)

    logger.info(
        "vacancy_prompt_sweep", landlords=len(by_landlord), units=len(by_landlord and stale)
    )
    return len(by_landlord)


def _vacancy_link(landlord) -> str:
    """Where the prompt sends them.

    The tenant subdomain is taken from a property's own campus join rather
    than guessed: a landlord may list near more than one university, and the
    portal lives on a tenant host (ADR-001). Empty rather than wrong when
    there is no join -- a broken link is worse than none.
    """
    from config.hosts import FrontendPath, frontend_url

    distance = (
        PropertyCampusDistance.all_objects.filter(property__landlord=landlord)
        .select_related("university")
        .order_by("-is_primary", "straight_line_km")
        .first()
    )
    if distance is None:
        return ""

    url = frontend_url(FrontendPath.VACANCY_REVIEW, subdomain=distance.university.subdomain)
    return f"Update them here: {url}"


def _send_vacancy_prompt(units) -> None:
    """One message per landlord, listing their stale units."""
    from django.core.mail import send_mail

    landlord = units[0].property.landlord
    if landlord.user.erased_at is not None:
        # A dormant listing needs no prompt, and mailing a tombstoned address
        # is at best pointless (ADR-008).
        return

    lines = "\n".join(
        f"  - {unit.property.name}: {unit.label} "
        f"(says {unit.vacant_count} free"
        + (
            ", never updated)"
            if unit.vacant_count_updated_at is None
            else f", last updated {unit.vacant_count_updated_at:%d %B %Y})"
        )
        for unit in units
    )

    # One click to the screen that does the thing. An email asking somebody to
    # update something, which lands them on a home page, is an email that
    # teaches them not to open the next one -- and this is the only channel the
    # freshness mechanism has.
    link = _vacancy_link(landlord)

    send_mail(
        subject="Are these still available?",
        message=(
            "Students see these vacancy counts when they search, and we show "
            "how old each one is. Updating them takes a moment and makes your "
            "listings rank as current:\n\n"
            f"{lines}\n\n"
            "If nothing has changed, confirming that is enough.\n\n"
            f"{link}"
        ),
        from_email=None,
        recipient_list=[landlord.user.email],
        fail_silently=True,
    )
