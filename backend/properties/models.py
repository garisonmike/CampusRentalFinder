"""
Properties and units (ADR-002).

``Property`` is a building or compound; ``Unit`` is the lettable thing, and it
is where vacancy lives. The draft had neither distinction: one ``Rental`` row
was simultaneously the building and the thing you rented, so a hostel block with
forty bedsitters could not express how many were free (docs/AUDIT.md §2).

Both are tenant-scoped through ``PropertyCampusDistance``, which is the join
that makes a property visible to a university at all.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from accounts.models import LandlordProfile
from config.tenancy import TenantScopedModel
from universities.constants import KENYAN_COUNTIES
from universities.models import Campus, University

from .constants import (
    MAX_PHOTOS_PER_UNIT,
    FurnishingStatus,
    PhotoProcessingStatus,
    PropertyStatus,
    PropertyType,
)
from .distances import straight_line_km


class Property(TenantScopedModel):
    """A building or compound, owned by one landlord.

    Reachable by a university only through ``PropertyCampusDistance``, so a
    property with no join rows is invisible to every tenant — enforced at the
    serializer, since no constraint can express "at least one related row"
    (ADR-002).
    """

    # Through the join model: a property serves every university it has a
    # campus distance for, which is the whole point of ADR-002.
    tenant_lookup = "campus_distances__university"

    landlord = models.ForeignKey(
        LandlordProfile,
        on_delete=models.PROTECT,
        related_name="properties",
        verbose_name=_("landlord"),
    )
    name = models.CharField(_("name"), max_length=200)
    slug = models.SlugField(_("slug"), max_length=220, unique=True)
    description = models.TextField(_("description"), blank=True)
    property_type = models.CharField(
        _("property type"), max_length=20, choices=PropertyType.choices
    )

    # -- Address, Kenyan shape --------------------------------------------
    # county/town/estate, not state/ZIP. A landmark matters more than a street
    # name here: "opposite Naivas" is how people actually navigate.
    county = models.CharField(_("county"), max_length=50, choices=KENYAN_COUNTIES)
    town = models.CharField(_("town"), max_length=100)
    estate = models.CharField(_("estate"), max_length=100, help_text=_('e.g. "Kahawa Wendani"'))
    street = models.CharField(_("street"), max_length=200, blank=True)
    landmark = models.CharField(
        _("landmark"), max_length=200, blank=True, help_text=_('e.g. "opposite Naivas"')
    )
    postal_address = models.CharField(
        _("postal address"), max_length=50, blank=True, help_text=_("P.O. Box 43844-00100")
    )

    latitude = models.FloatField(
        _("latitude"),
        null=True,
        blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
        help_text=_("Map pins and distance computation only. Never queried directly (ADR-006)."),
    )
    longitude = models.FloatField(
        _("longitude"),
        null=True,
        blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )

    # -- Amenities that are actually asked about here ----------------------
    # The draft offered gym_access and pool_access. Water reliability and
    # backup power are what a student near a Kenyan campus wants to know.
    has_water_tank = models.BooleanField(_("water tank"), default=False)
    has_borehole = models.BooleanField(_("borehole"), default=False)
    has_backup_power = models.BooleanField(_("backup power"), default=False)
    has_perimeter_wall = models.BooleanField(_("perimeter wall"), default=False)
    has_security_guard = models.BooleanField(_("security guard"), default=False)
    has_cctv = models.BooleanField(_("CCTV"), default=False)
    has_wifi = models.BooleanField(_("wifi"), default=False)
    has_parking = models.BooleanField(_("parking"), default=False)
    caretaker_on_site = models.BooleanField(_("caretaker on site"), default=False)

    status = models.CharField(
        _("status"), max_length=20, choices=PropertyStatus.choices, default=PropertyStatus.DRAFT
    )
    published_at = models.DateTimeField(_("published at"), null=True, blank=True)
    view_count = models.PositiveIntegerField(_("view count"), default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Property")
        verbose_name_plural = _("Properties")
        ordering = ["-published_at", "-created_at"]
        base_manager_name = "all_objects"
        default_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["status", "-published_at"], name="property_status_idx"),
            models.Index(fields=["county", "town"], name="property_location_idx"),
            models.Index(fields=["landlord", "status"], name="property_landlord_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(latitude__isnull=True) | (Q(latitude__gte=-90) & Q(latitude__lte=90)),
                name="property_latitude_range",
            ),
            models.CheckConstraint(
                condition=Q(longitude__isnull=True)
                | (Q(longitude__gte=-180) & Q(longitude__lte=180)),
                name="property_longitude_range",
            ),
            # A published property without a timestamp cannot be ordered, and
            # the listing page orders by it.
            models.CheckConstraint(
                condition=~Q(status=PropertyStatus.PUBLISHED) | Q(published_at__isnull=False),
                name="property_published_has_timestamp",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def register_view(self) -> None:
        """Increment the view counter and refresh the in-memory value.

        The refresh is the point. The draft assigned an ``F()`` expression and
        then serialised the same instance, so the rental detail endpoint raised
        TypeError for every visitor who was not the owner (docs/AUDIT.md §4.1).
        """
        type(self).all_objects.filter(pk=self.pk).update(view_count=F("view_count") + 1)
        self.refresh_from_db(fields=["view_count"])


class Unit(TenantScopedModel):
    """The lettable thing. Vacancy lives here.

    A ``Unit`` row can represent a pool of identical units — forty bedsitters in
    a hostel block are one row with ``total_count=40`` — which is how vacancy
    counts work at all. The draft had no way to say "three of these are free".
    """

    tenant_lookup = "property__campus_distances__university"

    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.CASCADE,
        related_name="units",
        verbose_name=_("property"),
    )
    label = models.CharField(
        _("label"),
        max_length=50,
        help_text=_('"B12" for one unit, or "Bedsitters" for a pool of identical ones.'),
    )
    unit_type = models.CharField(_("unit type"), max_length=20, choices=PropertyType.choices)

    # -- Money. KES, Decimal, never float. --------------------------------
    rent_kes = models.DecimalField(_("monthly rent (KES)"), max_digits=10, decimal_places=2)
    deposit_kes = models.DecimalField(
        _("deposit (KES)"), max_digits=10, decimal_places=2, null=True, blank=True
    )

    water_included = models.BooleanField(_("water included"), default=False)
    electricity_included = models.BooleanField(
        _("electricity included"),
        default=False,
        help_text=_("Token metering is the norm otherwise."),
    )
    wifi_included = models.BooleanField(_("wifi included"), default=False)
    furnished = models.CharField(
        _("furnishing"),
        max_length=20,
        choices=FurnishingStatus.choices,
        default=FurnishingStatus.UNFURNISHED,
    )

    size_sqm = models.PositiveSmallIntegerField(
        _("size (m²)"), null=True, blank=True, help_text=_("Square metres, not feet.")
    )
    bedrooms = models.PositiveSmallIntegerField(
        _("bedrooms"), default=0, help_text=_("0 for a bedsitter or single room.")
    )
    has_private_bathroom = models.BooleanField(
        _("private bathroom"),
        default=False,
        help_text=_(
            "Replaces the draft's bathrooms>=1, which made a hostel block with "
            "shared ablutions impossible to list."
        ),
    )
    has_kitchenette = models.BooleanField(_("kitchenette"), default=False)
    floor = models.SmallIntegerField(_("floor"), null=True, blank=True)

    # -- Vacancy ----------------------------------------------------------
    total_count = models.PositiveSmallIntegerField(
        _("total units"), default=1, help_text=_("How many identical units exist.")
    )
    vacant_count = models.PositiveSmallIntegerField(
        _("vacant units"), default=0, help_text=_("How many are free right now.")
    )
    available_from = models.DateField(_("available from"), null=True, blank=True)
    min_stay_months = models.PositiveSmallIntegerField(
        _("minimum stay (months)"),
        default=4,
        help_text=_("One semester. The draft defaulted to a 12-month lease."),
    )

    is_active = models.BooleanField(_("active"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Unit")
        verbose_name_plural = _("Units")
        ordering = ["property", "label"]
        base_manager_name = "all_objects"
        default_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["property", "is_active"], name="unit_property_idx"),
            models.Index(fields=["rent_kes"], name="unit_rent_idx"),
            models.Index(fields=["unit_type", "rent_kes"], name="unit_type_rent_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["property", "label"], name="unit_label_unique_per_property"
            ),
            # More vacancies than units is not a display bug, it is a listing
            # that lies about availability.
            models.CheckConstraint(
                condition=Q(vacant_count__lte=F("total_count")), name="unit_vacant_not_over_total"
            ),
            models.CheckConstraint(condition=Q(rent_kes__gt=0), name="unit_rent_positive"),
            models.CheckConstraint(
                condition=Q(deposit_kes__isnull=True) | Q(deposit_kes__gte=0),
                name="unit_deposit_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(total_count__gte=1), name="unit_total_count_at_least_one"
            ),
            models.CheckConstraint(
                condition=Q(min_stay_months__gte=1), name="unit_min_stay_at_least_one_month"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.property.name} — {self.label}"

    # Deliberately a method, not a @property: the `property` foreign key above
    # shadows the builtin inside this class body, so `@property` here resolves
    # to a ForeignKey and raises at import. Anything on Unit that would
    # naturally be a property has to be a method.
    def is_available(self) -> bool:
        """Whether anyone could move in today."""
        return self.is_active and self.vacant_count > 0


class PropertyCampusDistance(TenantScopedModel):
    """How far a property is from one campus (ADR-002).

    The join that makes a property visible to a university at all. Distance is
    an attribute of the *pair*, not of the property, which is why it lives here
    rather than as a column on Property — and why one property can serve several
    institutions without being duplicated.
    """

    tenant_lookup = "university"

    property = models.ForeignKey(
        "properties.Property",
        on_delete=models.CASCADE,
        related_name="campus_distances",
        verbose_name=_("property"),
    )
    university = models.ForeignKey(
        University, on_delete=models.PROTECT, related_name="property_distances"
    )
    campus = models.ForeignKey(Campus, on_delete=models.PROTECT, related_name="property_distances")

    straight_line_km = models.DecimalField(
        _("straight-line distance (km)"),
        max_digits=5,
        decimal_places=2,
        help_text=_(
            "Haversine, computed on save. Always present, and an honest lower "
            "bound. Any UI showing it must label it as direct distance."
        ),
    )
    walking_distance_km = models.DecimalField(
        _("walking distance (km)"),
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text=_("From a routing provider only. Never derived from the straight line."),
    )
    walking_minutes = models.PositiveSmallIntegerField(_("walking minutes"), null=True, blank=True)
    routed_at = models.DateTimeField(_("routed at"), null=True, blank=True)
    route_provider = models.CharField(_("route provider"), max_length=32, blank=True)

    matatu_route = models.CharField(
        _("matatu route"), max_length=50, blank=True, help_text=_('e.g. "Route 45"')
    )
    is_primary = models.BooleanField(
        _("primary campus"),
        default=False,
        help_text=_("The campus this listing is marketed against."),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Property/campus distance")
        verbose_name_plural = _("Property/campus distances")
        ordering = ["property", "straight_line_km"]
        base_manager_name = "all_objects"
        default_manager_name = "all_objects"
        indexes = [
            # The platform's primary query: "within 2 km of my campus".
            models.Index(
                fields=["university", "straight_line_km"], name="pcd_university_distance_idx"
            ),
            # The routing job takes the oldest first.
            models.Index(fields=["routed_at"], name="pcd_routed_at_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["property", "campus"], name="pcd_unique_property_campus"
            ),
            models.UniqueConstraint(
                fields=["property"],
                condition=Q(is_primary=True),
                name="pcd_one_primary_campus_per_property",
            ),
            models.CheckConstraint(
                condition=Q(straight_line_km__gte=0) & Q(straight_line_km__lte=500),
                name="pcd_distance_sane",
            ),
            # The three routed fields move together. A walking time with no
            # provider and no timestamp is a number nobody can account for.
            models.CheckConstraint(
                condition=(
                    Q(walking_minutes__isnull=True)
                    & Q(walking_distance_km__isnull=True)
                    & Q(routed_at__isnull=True)
                )
                | (
                    Q(walking_minutes__isnull=False)
                    & Q(walking_distance_km__isnull=False)
                    & Q(routed_at__isnull=False)
                ),
                name="pcd_routed_fields_move_together",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.property.name} → {self.campus.name}: {self.straight_line_km} km"

    def save(self, *args, **kwargs):
        """Compute the straight-line distance before writing.

        Recomputed on every save rather than only on create, so correcting a
        property's coordinates fixes its distances without a separate step. A
        campus moving still needs the management command, because this row does
        not know it happened.
        """
        if self.property.latitude is None or self.property.longitude is None:
            # straight_line_km is NOT NULL, and ADR-002 says it is always
            # present, so there is no honest value to store for an unpinned
            # property. Refuse clearly rather than letting the database report
            # a null column nobody knew was being written.
            raise ValidationError(
                {
                    "property": _(
                        "%(name)s has no coordinates, so its distance to a campus "
                        "cannot be computed. Set latitude and longitude first."
                    )
                    % {"name": self.property.name}
                }
            )

        self.straight_line_km = straight_line_km(
            self.property.latitude,
            self.property.longitude,
            self.campus.latitude,
            self.campus.longitude,
        )
        super().save(*args, **kwargs)


class UnitPhoto(TenantScopedModel):
    """A photo of a unit, on object storage (ADR-007).

    Never local disk, in any environment. Keys refer to the **public** media
    bucket; verification documents live in a separate private bucket with its
    own backend class and never share this one.

    The original is retained so variants can be regenerated when the sizes
    change, and the API serves it until they are ready.
    """

    tenant_lookup = "unit__property__campus_distances__university"

    unit = models.ForeignKey("properties.Unit", on_delete=models.CASCADE, related_name="photos")

    original_key = models.CharField(
        _("original key"),
        max_length=500,
        help_text=_("Object key in the public media bucket."),
    )
    thumb_key = models.CharField(_("thumbnail key"), max_length=500, blank=True)
    medium_key = models.CharField(_("medium key"), max_length=500, blank=True)
    large_key = models.CharField(_("large key"), max_length=500, blank=True)

    processing_status = models.CharField(
        _("processing status"),
        max_length=20,
        choices=PhotoProcessingStatus.choices,
        default=PhotoProcessingStatus.PENDING,
    )
    processing_error = models.CharField(_("processing error"), max_length=255, blank=True)

    caption = models.CharField(_("caption"), max_length=200, blank=True)
    is_primary = models.BooleanField(_("primary photo"), default=False)
    sort_order = models.PositiveSmallIntegerField(_("sort order"), default=0)

    width = models.PositiveSmallIntegerField(_("width"), null=True, blank=True)
    height = models.PositiveSmallIntegerField(_("height"), null=True, blank=True)
    byte_size = models.PositiveIntegerField(_("size in bytes"), null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Unit photo")
        verbose_name_plural = _("Unit photos")
        ordering = ["unit", "sort_order", "created_at"]
        base_manager_name = "all_objects"
        default_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["unit", "sort_order"], name="unitphoto_order_idx"),
            # What the "variants stalled" alert reads (docs/OPERATIONS.md).
            models.Index(fields=["processing_status", "created_at"], name="unitphoto_pending_idx"),
        ]
        constraints = [
            # The draft enforced this in save(), which a bulk update bypasses.
            models.UniqueConstraint(
                fields=["unit"],
                condition=Q(is_primary=True),
                name="unitphoto_one_primary_per_unit",
            ),
            models.CheckConstraint(
                condition=~Q(processing_status=PhotoProcessingStatus.READY)
                | (~Q(thumb_key="") & ~Q(medium_key="") & ~Q(large_key="")),
                name="unitphoto_ready_has_all_variants",
            ),
            models.CheckConstraint(
                condition=~Q(processing_status=PhotoProcessingStatus.FAILED)
                | ~Q(processing_error=""),
                name="unitphoto_failed_has_a_reason",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.unit} — {self.caption or self.original_key}"

    def display_key(self, variant: str = "medium") -> str:
        """The best key available for a variant, falling back to the original.

        A photo whose variants have not been generated still renders — larger
        than it should be, which is the intended degradation (ADR-007).
        """
        key = getattr(self, f"{variant}_key", "") if variant != "original" else ""
        return key or self.original_key

    @classmethod
    def unit_is_full(cls, unit_id: int) -> bool:
        """Whether this unit has reached the per-unit photo cap."""
        return cls.all_objects.filter(unit_id=unit_id).count() >= MAX_PHOTOS_PER_UNIT
