"""
Admin registrations for the properties app.

`vacant_count` is editable here, which makes the admin a **second write path**
for a field that has a single service function guarding it. Both entry points
below route through `state_vacancy()` rather than letting the ModelForm save
the number on its own: a form save writes the count and leaves
`vacant_count_updated_at` where it was, producing a fresh number wearing an old
date -- the exact failure the service function exists to prevent, arriving
through the one door that bypasses it.
"""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from .models import Property, Unit, UnitPhoto
from .services import state_vacancy


def restate_vacancy_if_changed(request: HttpRequest, unit: Unit, form) -> None:
    """Stamp provenance when an admin edit moved the vacancy count.

    Only when it *moved*. Re-saving a unit without touching the number is not
    a restatement, and stamping it as one would refresh the staleness signal
    without anybody having looked at the rooms -- which is worse than the
    stale label, because it is a false claim of currency rather than an honest
    admission of age.
    """
    if "vacant_count" not in getattr(form, "changed_data", ()):
        return

    state_vacancy(unit, vacant_count=unit.vacant_count, stated_by=request.user)


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

    def save_formset(self, request: HttpRequest, form, formset, change) -> None:
        """Route inline vacancy edits through the service function.

        The units inline is the likeliest place for this to happen -- someone
        fixing a listing edits the property and the rooms together -- and it
        is the place a `save_model` override would not cover.
        """
        super().save_formset(request, form, formset, change)

        if formset.model is not Unit:
            return

        for unit_form in formset.forms:
            if unit_form.instance.pk and not unit_form.cleaned_data.get("DELETE"):
                restate_vacancy_if_changed(request, unit_form.instance, unit_form)

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
    readonly_fields = (
        "created_at",
        "updated_at",
        "vacant_count_updated_at",
        "vacant_count_updated_by",
    )

    def save_model(self, request: HttpRequest, obj: Unit, form, change: bool) -> None:
        super().save_model(request, obj, form, change)
        restate_vacancy_if_changed(request, obj, form)


@admin.register(UnitPhoto)
class UnitPhotoAdmin(admin.ModelAdmin):
    list_display = ("unit", "caption", "is_primary", "processing_status", "sort_order")
    list_filter = ("processing_status", "is_primary")
    search_fields = ("caption", "unit__label", "unit__property__name")
    ordering = ("unit", "sort_order")
    autocomplete_fields = ("unit",)
    list_select_related = ("unit",)
    readonly_fields = ("created_at", "updated_at", "width", "height", "byte_size")
