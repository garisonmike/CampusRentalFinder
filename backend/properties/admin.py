"""Admin registrations for the properties app."""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from .models import Property, Unit, UnitPhoto


class UnitInline(admin.TabularInline):
    model = Unit
    extra = 1
    fields = ("label", "unit_type", "rent_kes", "total_count", "vacant_count", "is_active")


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ("name", "landlord", "property_type", "town", "county", "status", "view_count")
    list_filter = ("status", "property_type", "county", "created_at")
    search_fields = ("name", "estate", "town", "landmark", "landlord__business_name")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("view_count", "created_at", "updated_at")
    autocomplete_fields = ("landlord",)
    list_select_related = ("landlord",)
    inlines = (UnitInline,)

    fieldsets = (
        (None, {"fields": ("name", "slug", "landlord", "property_type", "description")}),
        (
            _("Address"),
            {
                "fields": (
                    "county",
                    "town",
                    "estate",
                    "street",
                    "landmark",
                    "postal_address",
                    "latitude",
                    "longitude",
                ),
                "description": _("County/town/estate. A landmark is often more use than a street."),
            },
        ),
        (
            _("Amenities"),
            {
                "classes": ("collapse",),
                "fields": (
                    "has_water_tank",
                    "has_borehole",
                    "has_backup_power",
                    "has_perimeter_wall",
                    "has_security_guard",
                    "has_cctv",
                    "has_wifi",
                    "has_parking",
                    "caretaker_on_site",
                ),
            },
        ),
        (
            _("Status"),
            {"fields": ("status", "published_at", "view_count", "created_at", "updated_at")},
        ),
    )

    def get_queryset(self, request: HttpRequest):
        return super().get_queryset(request).select_related("landlord")


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = (
        "label",
        "property",
        "unit_type",
        "rent_kes",
        "total_count",
        "vacant_count",
        "is_active",
    )
    list_filter = ("unit_type", "furnished", "is_active", "has_private_bathroom")
    search_fields = ("label", "property__name", "property__estate")
    ordering = ("property", "label")
    autocomplete_fields = ("property",)
    list_select_related = ("property",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(UnitPhoto)
class UnitPhotoAdmin(admin.ModelAdmin):
    list_display = ("unit", "caption", "is_primary", "processing_status", "sort_order")
    list_filter = ("processing_status", "is_primary")
    search_fields = ("caption", "unit__label", "unit__property__name")
    ordering = ("unit", "sort_order")
    autocomplete_fields = ("unit",)
    list_select_related = ("unit",)
    readonly_fields = ("created_at", "updated_at", "width", "height", "byte_size")
