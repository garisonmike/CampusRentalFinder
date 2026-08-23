"""Admin registrations for the reviews app."""

from __future__ import annotations

from django.contrib import admin
from django.http import HttpRequest
from django.utils.translation import gettext_lazy as _

from reviews.models import Review, ReviewHelpfulness, ReviewReport


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = (
        "rental",
        "tenant",
        "rating",
        "title",
        "is_approved",
        "is_verified",
        "helpful_votes",
        "created_at",
    )
    list_filter = (
        "rating",
        "is_approved",
        "is_verified",
        "would_recommend",
        "created_at",
    )
    search_fields = (
        "title",
        "comment",
        "pros",
        "cons",
        "rental__title",
        "tenant__email",
    )
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    readonly_fields = ("helpful_votes", "total_votes", "created_at", "updated_at")
    autocomplete_fields = ("rental", "tenant")
    list_select_related = ("rental", "tenant")
    list_editable = ("is_approved", "is_verified")

    fieldsets = (
        (None, {"fields": ("rental", "tenant", "rating", "title", "comment")}),
        (
            _("Detailed ratings"),
            {
                "fields": (
                    "cleanliness_rating",
                    "location_rating",
                    "value_rating",
                    "landlord_rating",
                )
            },
        ),
        (_("Detail"), {"fields": ("pros", "cons", "would_recommend")}),
        (_("Stay"), {"fields": ("move_in_date", "move_out_date")}),
        (_("Moderation"), {"fields": ("is_approved", "is_verified", "moderation_notes")}),
        (_("Landlord response"), {"fields": ("landlord_response", "landlord_response_date")}),
        (_("Metadata"), {"fields": ("helpful_votes", "total_votes", "created_at", "updated_at")}),
    )

    actions = ("approve_reviews", "unapprove_reviews")

    def get_queryset(self, request: HttpRequest):
        return super().get_queryset(request).select_related("rental", "tenant")

    @admin.action(description=_("Approve selected reviews"))
    def approve_reviews(self, request: HttpRequest, queryset) -> None:
        updated = queryset.update(is_approved=True)
        self.message_user(request, _("%(count)d review(s) approved.") % {"count": updated})

    @admin.action(description=_("Unapprove selected reviews"))
    def unapprove_reviews(self, request: HttpRequest, queryset) -> None:
        updated = queryset.update(is_approved=False)
        self.message_user(request, _("%(count)d review(s) unapproved.") % {"count": updated})


@admin.register(ReviewHelpfulness)
class ReviewHelpfulnessAdmin(admin.ModelAdmin):
    list_display = ("review", "user", "is_helpful", "created_at")
    list_filter = ("is_helpful", "created_at")
    search_fields = ("review__title", "user__email")
    ordering = ("-created_at",)
    autocomplete_fields = ("review", "user")
    list_select_related = ("review", "user")


@admin.register(ReviewReport)
class ReviewReportAdmin(admin.ModelAdmin):
    list_display = (
        "review",
        "reporter",
        "reason",
        "is_resolved",
        "resolved_by",
        "created_at",
    )
    list_filter = ("reason", "is_resolved", "created_at")
    search_fields = ("review__title", "reporter__email", "description")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    readonly_fields = ("created_at",)
    autocomplete_fields = ("review", "reporter", "resolved_by")
    list_select_related = ("review", "reporter", "resolved_by")
