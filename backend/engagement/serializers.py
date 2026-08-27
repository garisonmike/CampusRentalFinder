"""
Saved-property and inquiry serializers (ADR-004 §1.1).

**No contact details cross this boundary in either direction.** Not the
landlord's phone, not the student's, not an email address. That is not
squeamishness: the moment a conversation moves off-platform, the resulting
tenancy is one the platform did not witness, so it arrives later as a *claim*
rather than as an accepted application — and every claim is a dispute surface
and a queue entry that the on-platform path does not create.

Keeping the conversation here is what keeps the application path the default
route, which is what keeps ADR-004's primary volume control doing its job.
"""

from __future__ import annotations

import re

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Inquiry, SavedProperty

#: Patterns that look like a way to continue the conversation elsewhere.
#:
#: Deliberately blunt and deliberately not a blocklist of "bad" content -- the
#: point is not moderation. A Kenyan mobile number, an email address, and the
#: obvious messaging handles cover the ways this actually happens, and anything
#: cleverer would be an arms race we do not need to win: a determined pair will
#: exchange numbers regardless, and the design survives that (they end up
#: raising a claim). What this stops is the *default* drifting off-platform.
CONTACT_PATTERNS = (
    # +254 7xx xxx xxx, 07xx xxx xxx, and the spaced or dashed variants.
    re.compile(r"(?:\+?254|0)\s*[-.]?\s*7\d(?:\s*[-.]?\s*\d){7}"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    re.compile(r"\b(?:whatsapp|telegram|wa\.me|t\.me)\b", re.IGNORECASE),
)

CONTACT_MESSAGE = (
    "Keep the conversation on the platform -- remove the phone number, email "
    "address or messaging handle. Arranging a stay here is what lets us "
    "confirm it later without anyone having to prove it."
)


def _looks_like_contact_details(text: str) -> bool:
    return any(pattern.search(text) for pattern in CONTACT_PATTERNS)


class SavedPropertySerializer(serializers.ModelSerializer):
    """A saved listing, as it appears in the student's own list."""

    property_slug = serializers.CharField(source="property_saved.slug", read_only=True)
    property_name = serializers.CharField(source="property_saved.name", read_only=True)
    property_town = serializers.CharField(source="property_saved.town", read_only=True)

    class Meta:
        model = SavedProperty
        fields = (
            "id",
            "property_slug",
            "property_name",
            "property_town",
            "note",
            "created_at",
        )
        read_only_fields = ("id", "property_slug", "property_name", "property_town", "created_at")


class SavePropertySerializer(serializers.Serializer):
    """Saving one, by slug.

    By slug rather than id, because the slug is what a client already holds
    from the listing it is looking at.
    """

    property_slug = serializers.CharField(
        help_text="Slug of the property to save. Idempotent -- saving twice is not an error."
    )
    note = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=200,
        help_text="A private reminder to yourself. Never shown to the landlord.",
    )


class InquirySerializer(serializers.ModelSerializer):
    """An inquiry, as either party sees it.

    The sender's name is shown to the landlord because they are being asked to
    reply to a person. Nobody's **contact details** appear, in either
    direction.
    """

    unit_label = serializers.CharField(source="unit.label", read_only=True)
    property_name = serializers.CharField(source="unit.property.name", read_only=True)
    property_slug = serializers.CharField(source="unit.property.slug", read_only=True)
    sender_name = serializers.SerializerMethodField(
        help_text="'Former student' for an erased account (ADR-008)."
    )
    responded_by_name = serializers.SerializerMethodField(
        help_text=(
            "Who replied -- the landlord or an assigned caretaker. Shown "
            "because the student is owed the knowledge that a person answered, "
            "and which."
        )
    )

    class Meta:
        model = Inquiry
        fields = (
            "id",
            "unit",
            "unit_label",
            "property_name",
            "property_slug",
            "sender_name",
            "message",
            "preferred_move_in_date",
            "status",
            "response",
            "responded_by_name",
            "responded_at",
            "created_at",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_sender_name(self, inquiry: Inquiry) -> str:
        from accounts.privacy import display_name_for

        return display_name_for(inquiry.sender)

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_responded_by_name(self, inquiry: Inquiry) -> str | None:
        from accounts.privacy import display_name_for

        if inquiry.responded_by is None:
            return None
        return display_name_for(inquiry.responded_by)


class InquiryCreateSerializer(serializers.Serializer):
    """Sending one.

    An inquiry is an unsolicited message to a stranger, so the rate limit is
    part of the feature rather than a later hardening pass -- enforced here at
    the boundary AND in the service layer, which is what a management command
    or a future job would go through.
    """

    unit = serializers.IntegerField(help_text="Id of the unit you are asking about.")
    message = serializers.CharField(
        max_length=2000,
        help_text=(
            "What you want to ask. **Do not include phone numbers, email "
            "addresses or messaging handles** -- they are rejected. Keeping the "
            "conversation here is what lets the platform confirm a resulting "
            "stay without anyone having to prove it later (ADR-004 §1.1)."
        ),
    )
    preferred_move_in_date = serializers.DateField(
        required=False,
        allow_null=True,
        help_text="Optional. When you would want to move in.",
    )

    def validate_message(self, message: str) -> str:
        if _looks_like_contact_details(message):
            raise serializers.ValidationError(CONTACT_MESSAGE)
        return message


class InquiryResponseSerializer(serializers.Serializer):
    """The landlord's or caretaker's reply.

    Same contact-details rule, in the other direction. A landlord answering
    "call me on 07..." is the same leak with the same consequence.
    """

    response = serializers.CharField(
        max_length=2000,
        help_text=(
            "Your reply. **Do not include phone numbers, email addresses or "
            "messaging handles** -- they are rejected in this direction too. "
            "Invite them to apply instead; that is the path the platform can "
            "witness."
        ),
    )

    def validate_response(self, response: str) -> str:
        if _looks_like_contact_details(response):
            raise serializers.ValidationError(CONTACT_MESSAGE)
        return response
