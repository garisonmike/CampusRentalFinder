"""Admin for saved properties and inquiries."""

from django.contrib import admin

from .models import Inquiry, SavedProperty


@admin.register(SavedProperty)
class SavedPropertyAdmin(admin.ModelAdmin):
    list_display = ("user", "property_saved", "created_at")
    ordering = ("-created_at",)
    # The note is the student's own. Readable for support, never editable.
    readonly_fields = ("user", "property_saved", "note", "created_at")


@admin.register(Inquiry)
class InquiryAdmin(admin.ModelAdmin):
    list_display = ("__str__", "sender", "status", "responded_by", "created_at")
    list_filter = ("status", "created_at")
    ordering = ("-created_at",)
    # The exchange itself is between two people. The admin can read it and
    # change the status; it must not put words in either party's mouth.
    readonly_fields = ("unit", "sender", "message", "response", "responded_by", "created_at")
