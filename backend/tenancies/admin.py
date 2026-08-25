"""Admin registrations for the tenancies app."""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest

from .models import Application, Tenancy


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ("applicant", "unit", "status", "move_in_date", "decided_at")
    list_filter = ("status", "created_at", "move_in_date")
    search_fields = ("applicant__email", "unit__label", "unit__property__name")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    autocomplete_fields = ("unit", "applicant", "decided_by")
    list_select_related = ("applicant", "unit")
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request: HttpRequest):
        return super().get_queryset(request).select_related("applicant", "unit__property")


@admin.register(Tenancy)
class TenancyAdmin(admin.ModelAdmin):
    list_display = (
        "tenant",
        "unit",
        "confirmation_source",
        "start_date",
        "end_date",
        "status",
        "was_disputed",
    )
    list_filter = ("confirmation_source", "status", "was_disputed", "start_date")
    search_fields = ("tenant__email", "unit__label", "unit__property__name")
    ordering = ("-start_date",)
    date_hierarchy = "start_date"
    autocomplete_fields = ("unit", "tenant", "application", "confirmed_by")
    list_select_related = ("tenant", "unit")
    readonly_fields = ("created_at", "updated_at", "confirmed_at")

    def get_queryset(self, request: HttpRequest):
        return super().get_queryset(request).select_related("tenant", "unit__property")
