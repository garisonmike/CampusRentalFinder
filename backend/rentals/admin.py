"""Admin registrations for the rentals app."""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from rentals.models import Rental, RentalFavorite, RentalImage, RentalInquiry


class RentalImageInline(admin.TabularInline):
    model = RentalImage
    extra = 1
    fields = ("image", "caption", "is_primary", "order")
    ordering = ("order",)


@admin.register(Rental)
class RentalAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "landlord",
        "property_type",
        "city",
        "price",
        "bedrooms",
        "status",
        "is_featured",
        "views_count",
        "created_at",
    )
    list_filter = (
        "status",
        "property_type",
        "furnishing_status",
        "is_featured",
        "utilities_included",
        "city",
        "created_at",
    )
    search_fields = (
        "title",
        "description",
        "address",
        "city",
        "landlord__email",
        "landlord__first_name",
        "landlord__last_name",
    )
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    readonly_fields = ("views_count", "created_at", "updated_at")
    autocomplete_fields = ("landlord",)
    list_select_related = ("landlord",)
    list_editable = ("status", "is_featured")
    inlines = (RentalImageInline,)

    fieldsets = (
        (None, {"fields": ("title", "description", "property_type", "landlord")}),
        (_("Pricing"), {"fields": ("price", "security_deposit", "utilities_included")}),
        (
            _("Location"),
            {
                "fields": (
                    "address",
                    "city",
                    "state",
                    "zip_code",
                    "country",
                    "latitude",
                    "longitude",
                )
            },
        ),
        (
            _("Property details"),
            {"fields": ("bedrooms", "bathrooms", "square_footage", "furnishing_status")},
        ),
        (
            _("Amenities"),
            {
                "classes": ("collapse",),
                "fields": (
                    "parking_available",
                    "parking_spots",
                    "pets_allowed",
                    "smoking_allowed",
                    "laundry_available",
                    "internet_included",
                    "gym_access",
                    "pool_access",
                ),
            },
        ),
        (
            _("Availability"),
            {"fields": ("available_from", "lease_duration_min", "lease_duration_max", "status")},
        ),
        (_("Campus"), {"fields": ("distance_to_campus", "shuttle_service")}),
        (_("Contact"), {"fields": ("contact_phone", "contact_email")}),
        (_("Metadata"), {"fields": ("is_featured", "views_count", "created_at", "updated_at")}),
    )

    def get_queryset(self, request: HttpRequest):
        return super().get_queryset(request).select_related("landlord")


@admin.register(RentalImage)
class RentalImageAdmin(admin.ModelAdmin):
    list_display = ("rental", "caption", "is_primary", "order", "uploaded_at")
    list_filter = ("is_primary", "uploaded_at")
    search_fields = ("rental__title", "caption")
    ordering = ("rental", "order")
    autocomplete_fields = ("rental",)
    list_select_related = ("rental",)


@admin.register(RentalFavorite)
class RentalFavoriteAdmin(admin.ModelAdmin):
    list_display = ("user", "rental", "created_at")
    list_filter = ("created_at",)
    search_fields = ("user__email", "rental__title")
    ordering = ("-created_at",)
    autocomplete_fields = ("user", "rental")
    list_select_related = ("user", "rental")


@admin.register(RentalInquiry)
class RentalInquiryAdmin(admin.ModelAdmin):
    list_display = (
        "rental",
        "tenant",
        "status",
        "preferred_move_date",
        "replied_at",
        "created_at",
    )
    list_filter = ("status", "created_at", "preferred_move_date")
    search_fields = ("rental__title", "tenant__email", "message")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    readonly_fields = ("created_at",)
    autocomplete_fields = ("rental", "tenant")
    list_select_related = ("rental", "tenant")
