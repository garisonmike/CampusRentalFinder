"""
Admin for reviews (ADR-004).

Read-mostly on purpose. The admin is a path that routes around serializers, and
the invariants here are the product — so the rating, the tenancy and the
author are not editable from this screen at all. Hiding a review requires a
reason, which the database enforces regardless of what this form allows.
"""

from django.contrib import admin

from .models import Review, ReviewResponse


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("__str__", "rating", "is_published", "created_at")
    list_filter = ("is_published", "rating")
    search_fields = ("comment",)
    ordering = ("-created_at",)
    # The trust property, in the one place most likely to route around it.
    readonly_fields = ("tenancy", "rating", "editable_until", "created_at", "updated_at")


@admin.register(ReviewResponse)
class ReviewResponseAdmin(admin.ModelAdmin):
    list_display = ("review", "author", "created_at")
    ordering = ("-created_at",)
    readonly_fields = ("review", "author", "created_at", "updated_at")
