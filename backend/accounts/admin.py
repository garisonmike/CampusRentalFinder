"""Admin registrations for the accounts app."""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminPasswordChangeForm, UserChangeForm, UserCreationForm
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from .models import (
    CaretakerAssignment,
    LandlordProfile,
    StudentProfile,
    UniversityStaffProfile,
    User,
)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Identity only.

    There is no role field to edit here: capability lives in the profile models
    below, and `is_staff` is the only flag meaning platform administrator.
    """

    add_form = UserCreationForm
    form = UserChangeForm
    change_password_form = AdminPasswordChangeForm

    list_display = ("email", "get_full_name", "is_active", "is_staff", "date_joined")
    list_filter = ("is_active", "is_staff", "is_superuser", "email_verified", "date_joined")
    search_fields = ("email", "first_name", "last_name", "phone_number")
    ordering = ("-date_joined",)
    readonly_fields = ("last_login", "date_joined", "updated_at")
    date_hierarchy = "date_joined"

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            _("Personal info"),
            {"fields": ("first_name", "last_name", "phone_number", "avatar_url")},
        ),
        (_("Verification"), {"fields": ("email_verified", "phone_verified")}),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                ),
                "description": _(
                    "is_staff is the only meaning of 'platform administrator'. "
                    "Landlord, student and university-staff capability come from "
                    "the profile models, not from here."
                ),
            },
        ),
        (_("Important dates"), {"fields": ("last_login", "date_joined", "updated_at")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "first_name", "last_name", "password1", "password2"),
            },
        ),
    )

    @admin.display(description=_("full name"), ordering="first_name")
    def get_full_name(self, obj: User) -> str:
        return obj.get_full_name()


@admin.register(LandlordProfile)
class LandlordProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "business_name", "verification_status", "verified_at")
    list_filter = ("verification_status", "created_at")
    search_fields = ("user__email", "user__first_name", "user__last_name", "business_name")
    ordering = ("-created_at",)
    autocomplete_fields = ("user", "verified_by")
    list_select_related = ("user",)
    readonly_fields = ("created_at", "updated_at")
    # Regulated personal data (ADR-003). The key is shown, never a URL: the
    # document lives in the private bucket behind a short-lived signed URL.
    exclude = ("id_document_key",)

    def get_queryset(self, request: HttpRequest):
        return super().get_queryset(request).select_related("user")


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "university",
        "verification_status",
        "verification_method",
        "verified_at",
    )
    list_filter = ("verification_status", "verification_method", "university")
    search_fields = ("user__email", "user__first_name", "user__last_name", "student_email")
    ordering = ("-created_at",)
    autocomplete_fields = ("user", "university", "verified_by")
    list_select_related = ("user", "university")
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request: HttpRequest):
        return super().get_queryset(request).select_related("user", "university")


@admin.register(UniversityStaffProfile)
class UniversityStaffProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "university", "job_title", "can_review_verifications", "is_active")
    list_filter = ("is_active", "can_review_verifications", "university")
    search_fields = ("user__email", "user__first_name", "user__last_name", "job_title")
    ordering = ("university", "user")
    autocomplete_fields = ("user", "university")
    list_select_related = ("user", "university")
    readonly_fields = ("created_at", "updated_at")


@admin.register(CaretakerAssignment)
class CaretakerAssignmentAdmin(admin.ModelAdmin):
    list_display = ("user", "property", "granted_by", "is_active", "granted_at", "revoked_at")
    list_filter = ("is_active", "granted_at")
    search_fields = ("user__email", "property__name", "granted_by__email")
    ordering = ("-granted_at",)
    date_hierarchy = "granted_at"
    autocomplete_fields = ("user", "property", "granted_by", "revoked_by")
    list_select_related = ("user", "property", "granted_by")
    readonly_fields = ("granted_at", "updated_at")
