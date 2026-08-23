"""Admin registrations for the accounts app."""

from __future__ import annotations

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from accounts.models import User, UserProfile


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    """Admin for the custom user model (email is the USERNAME_FIELD)."""

    list_display = (
        "email",
        "get_full_name",
        "user_type",
        "is_verified",
        "is_active",
        "is_staff",
        "created_at",
    )
    list_filter = (
        "user_type",
        "is_verified",
        "is_active",
        "is_staff",
        "is_superuser",
        "created_at",
    )
    search_fields = ("email", "username", "first_name", "last_name", "phone_number")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at", "last_login", "date_joined")
    list_select_related = True
    date_hierarchy = "created_at"

    fieldsets = (
        (None, {"fields": ("email", "username", "password")}),
        (
            _("Personal info"),
            {
                "fields": (
                    "first_name",
                    "last_name",
                    "phone_number",
                    "date_of_birth",
                    "profile_picture",
                    "bio",
                )
            },
        ),
        (_("Address"), {"fields": ("address", "city", "state", "zip_code")}),
        (_("Role and verification"), {"fields": ("user_type", "is_verified", "verification_date")}),
        (
            _("Permissions"),
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            _("Important dates"),
            {"fields": ("last_login", "date_joined", "created_at", "updated_at")},
        ),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "username",
                    "first_name",
                    "last_name",
                    "user_type",
                    "password1",
                    "password2",
                ),
            },
        ),
    )

    @admin.display(description=_("full name"), ordering="first_name")
    def get_full_name(self, obj: User) -> str:
        return obj.get_full_name()


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """Admin for the extended profile record."""

    list_display = (
        "user",
        "preferred_contact_method",
        "email_notifications",
        "sms_notifications",
        "business_name",
        "created_at",
    )
    list_filter = (
        "preferred_contact_method",
        "email_notifications",
        "sms_notifications",
        "created_at",
    )
    search_fields = ("user__email", "user__first_name", "user__last_name", "business_name")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("user",)
    list_select_related = ("user",)

    def get_queryset(self, request: HttpRequest):
        return super().get_queryset(request).select_related("user")
