"""Admin registrations for the universities app."""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from .models import Campus, University


class CampusInline(admin.TabularInline):
    model = Campus
    extra = 1
    fields = ("name", "town", "county", "latitude", "longitude", "is_main")


@admin.register(University)
class UniversityAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "display_name",
        "subdomain",
        "county",
        "signup_policy",
        "is_active",
    )
    list_filter = ("is_active", "signup_policy", "county", "verification_required_to_review")
    search_fields = ("name", "display_name", "subdomain", "domain", "town")
    ordering = ("name",)
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    inlines = (CampusInline,)

    fieldsets = (
        (None, {"fields": ("name", "display_name", "slug", "subdomain", "domain")}),
        (_("Location"), {"fields": ("county", "town")}),
        (
            _("Branding (ADR-005)"),
            {
                "fields": (
                    "logo_url",
                    "favicon_url",
                    "primary_hsl",
                    "secondary_hsl",
                    "accent_hsl",
                ),
                "description": _(
                    "Colours are three space-separated HSL components with no wrapper, "
                    'e.g. "142 71% 45%". Foregrounds are derived by contrast, not stored.'
                ),
            },
        ),
        (
            _("Student verification (ADR-003)"),
            {
                "fields": (
                    "verification_methods_enabled",
                    "student_email_domains",
                    "signup_policy",
                    "verification_enforced_from",
                    "verification_required_to_review",
                    "id_review_retention_days",
                ),
                "description": _(
                    "Requiring verification at signup needs at least one already-verified "
                    "student here, so a school cannot lock out its own intake."
                ),
            },
        ),
        (_("Status"), {"fields": ("is_active", "created_at", "updated_at")}),
    )

    def save_model(self, request: HttpRequest, obj: University, form, change: bool) -> None:
        """The admin is a write path like any other.

        The signup-policy rule spans tables, so no database constraint catches
        it here. ADR-003 requires every write path to call the service function.
        """
        from .services import assert_signup_policy_is_safe

        if change:
            assert_signup_policy_is_safe(obj, obj.signup_policy)
        super().save_model(request, obj, form, change)


@admin.register(Campus)
class CampusAdmin(admin.ModelAdmin):
    # `join_radius_km` is in the list, not buried in the form: it decides
    # which properties are visible to this campus's students, and a setting
    # with that reach should be readable at a glance rather than one click
    # into each row. Blank shows as the platform default.
    list_display = ("name", "university", "town", "county", "is_main", "join_radius_km")
    list_filter = ("is_main", "county", "university")
    search_fields = ("name", "town", "university__name")
    ordering = ("university", "name")
    autocomplete_fields = ("university",)
    list_select_related = ("university",)
