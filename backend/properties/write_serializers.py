"""
Write serializers for the landlord and caretaker surface (ADR-002, ADR-003).

**These validate shape; the service functions enforce rules.** The split is not
cosmetic. Everything that has to hold whoever is writing -- the publish gate,
the vacancy stamp, the cover-photo rule -- lives in `services.py`, so the
admin, a management command and a future job go through the same door. What is
here is the part that is genuinely about the request: which fields the caller
may name, and what a well-formed value looks like.

The most important thing in this file is a field that is absent.
`vacant_count` appears in no writable serializer anywhere. It has one write
path, `state_vacancy`, which stamps the count with who said it and when, and
`docs/OPERATIONS.md` records what happened the one time a second door existed.
"""

from __future__ import annotations

from rest_framework import serializers

from config.api.contract import VACANCY_AGE_DAYS, VACANCY_FRESHNESS

from .models import Property, Unit, UnitPhoto


class PropertyWriteSerializer(serializers.ModelSerializer):
    """Creating and editing a property's own details.

    `status`, `published_at`, `slug` and `landlord` are all absent. Status
    moves through `publish()` and `unpublish()` because publishing has a gate;
    the slug is derived from the name and frozen once published, since a
    published URL is in somebody's saved list; and the landlord is the caller.
    """

    class Meta:
        model = Property
        fields = (
            "name",
            "description",
            "property_type",
            "county",
            "town",
            "estate",
            "street",
            "landmark",
            "postal_address",
            "latitude",
            "longitude",
            "has_water_tank",
            "has_borehole",
            "has_backup_power",
            "has_perimeter_wall",
            "has_security_guard",
            "has_cctv",
            "has_wifi",
            "has_parking",
            "caretaker_on_site",
        )
        extra_kwargs = {
            "latitude": {
                "help_text": (
                    "Decimal degrees. **Required before publishing**: the "
                    "campus join is computed from coordinates, and a property "
                    "with none is invisible to every university (ADR-002). "
                    "Saving without them is fine -- the draft simply cannot go "
                    "live yet."
                )
            },
            "longitude": {"help_text": "Decimal degrees. See `latitude`."},
        }


class UnitWriteSerializer(serializers.ModelSerializer):
    """Creating and editing a unit.

    `vacant_count` is **not here and will not be added.** It is stated through
    `PATCH .../vacancy/`, which stamps the time and the author with it. A unit
    edit that could also set the count would let a fresh number keep an old
    date, which is worse than a stale count because the staleness signal would
    then claim currency.

    `available_from` and `is_active` are not here either, for a different
    reason: they are separately delegable. A caretaker may be trusted to say a
    room is off the market without being trusted to change its rent.
    """

    class Meta:
        model = Unit
        fields = (
            "label",
            "unit_type",
            "rent_kes",
            "deposit_kes",
            "water_included",
            "electricity_included",
            "wifi_included",
            "furnished",
            "size_sqm",
            "bedrooms",
            "has_private_bathroom",
            "has_kitchenette",
            "floor",
            "total_count",
            "min_stay_months",
        )
        extra_kwargs = {
            "total_count": {
                "help_text": (
                    "How many identical rooms this row represents. A hostel "
                    "block is one Unit with a total_count of forty, not forty "
                    "Units."
                )
            },
        }


class VacancySerializer(serializers.Serializer):
    """Stating how many rooms are free.

    One field, its own endpoint, and its own delegable permission -- because
    this is the number a student crosses a city on. Every write through it is
    stamped with who said it and when, and that stamp is what the listing
    shows beside the count.
    """

    vacant_count = serializers.IntegerField(
        min_value=0,
        help_text=(
            "How many rooms are free right now, as you know it. Never derived "
            "from our tenancy records: you know about the room let "
            "off-platform last week and we do not. Must not exceed the unit's "
            "total_count."
        ),
    )


class VacancyResultSerializer(serializers.Serializer):
    """What restating a vacancy gives back: the count, and its provenance."""

    id = serializers.IntegerField(read_only=True)
    vacant_count = serializers.IntegerField(read_only=True)
    total_count = serializers.IntegerField(read_only=True)
    vacancy_freshness = serializers.CharField(read_only=True, help_text=VACANCY_FRESHNESS)
    vacancy_age_days = serializers.IntegerField(
        read_only=True, allow_null=True, help_text=VACANCY_AGE_DAYS
    )
    vacant_count_updated_at = serializers.DateTimeField(read_only=True, allow_null=True)
    vacant_count_updated_by_name = serializers.CharField(
        read_only=True,
        help_text=(
            "Who stated it. A caretaker walking the block and a landlord "
            "updating from an office are different kinds of evidence, and an "
            "operator chasing a stale listing needs to know which."
        ),
    )


class AvailabilitySerializer(serializers.Serializer):
    """When a unit is free from, and whether it is listed at all."""

    available_from = serializers.DateField(
        required=False,
        allow_null=True,
        help_text="Null means no date given, which is not the same as available today.",
    )
    is_active = serializers.BooleanField(
        required=False,
        help_text=(
            "False hides the unit from listings without deleting it. Use this "
            "rather than deleting a unit somebody has stayed in -- the tenancy "
            "records point at it."
        ),
    )


class PhotoUploadSerializer(serializers.Serializer):
    """One photo.

    The file's content type is checked in the service rather than here,
    because the same rule has to hold for a management command bulk-importing
    a landlord's existing photos.
    """

    image = serializers.ImageField(help_text="JPEG, PNG or WebP.")
    caption = serializers.CharField(
        max_length=200,
        required=False,
        allow_blank=True,
        help_text=(
            "Optional. Used as the image's alt text, so it should say what "
            "the photo shows -- 'the shared kitchen', not 'IMG_2831'."
        ),
    )


class PhotoOrderSerializer(serializers.Serializer):
    """The whole order, not a move.

    Two people each nudging one photo produce an order neither chose. Sending
    the full list makes it last-write-wins on something the writer could see,
    and a stale list is rejected rather than silently applied.
    """

    photo_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False,
        help_text=(
            "Every photo id on this unit, in the order you want them. The first becomes the cover."
        ),
    )


class PhotoSerializer(serializers.ModelSerializer):
    """A photo as its manager sees it, including why it may not be ready."""

    url = serializers.SerializerMethodField()

    class Meta:
        model = UnitPhoto
        fields = (
            "id",
            "caption",
            "sort_order",
            "is_primary",
            "url",
            "processing_status",
            "processing_error",
        )
        read_only_fields = fields

    @staticmethod
    def get_url(photo: UnitPhoto) -> str:
        return photo.best_url()
