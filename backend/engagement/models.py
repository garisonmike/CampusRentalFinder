"""
Saved properties and inquiries.

The two things a student does before applying. Both are deliberately light —
neither carries notification machinery, and `Inquiry` deliberately carries no
contact details.

That last one is load-bearing rather than fussy. If an inquiry could contain a
phone number, the conversation would move off-platform on the first message,
and with it the `Application` that ADR-004 §1.1 depends on: an accepted
application is what creates a confirmed tenancy with no claim, no confirmation
window and no dispute surface. Every conversation that leaves early comes back
later as a claim, which is the queue the whole design exists to bound.
"""

from __future__ import annotations

import datetime as dt

from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from accounts.models import User
from config.tenancy import TenantScopedModel
from properties.models import Property, Unit

from .constants import (
    MAX_INQUIRY_LENGTH,
    MAX_RESPONSE_LENGTH,
    OPEN_INQUIRY_STATUSES,
    InquiryStatus,
)


class SavedProperty(TenantScopedModel):
    """A student bookmarking a property.

    No notification machinery. A saved property is a bookmark, and a bookmark
    that emails you is a subscription nobody asked for.
    """

    tenant_lookup = "property_saved__campus_distances__university"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="saved_properties")
    #: Not named `property`: a field called `property` shadows the builtin in
    #: the class namespace (see tools/check_field_shadowing.py).
    property_saved = models.ForeignKey(
        Property, on_delete=models.CASCADE, related_name="saved_by", db_column="property_id"
    )
    #: The student's own note. Never shown to the landlord -- "too far from the
    #: matatu stage" is for the person deciding, not the person selling.
    note = models.CharField(_("note"), max_length=200, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = _("Saved property")
        verbose_name_plural = _("Saved properties")
        ordering = ["-created_at"]
        base_manager_name = "all_objects"
        default_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["user", "-created_at"], name="saved_user_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "property_saved"], name="saved_unique_per_user_and_property"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user.get_full_name()} saved {self.property_saved}"


class Inquiry(TenantScopedModel):
    """A student asking a landlord about a specific unit.

    **An unsolicited message to a stranger**, which is why the rate limits are
    part of the model rather than a later hardening pass. A messaging feature
    without them is a spam feature with extra steps.
    """

    tenant_lookup = "unit__property__campus_distances__university"

    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="inquiries")
    #: PROTECT: an inquiry that led to an application is part of the audit
    #: trail from first contact to confirmed tenancy.
    sender = models.ForeignKey(User, on_delete=models.PROTECT, related_name="inquiries_sent")

    message = models.TextField(_("message"), max_length=MAX_INQUIRY_LENGTH)
    #: Optional. A student who has not decided when they are moving still has
    #: questions worth asking.
    preferred_move_in_date = models.DateField(_("preferred move-in date"), null=True, blank=True)

    status = models.CharField(
        _("status"),
        max_length=16,
        choices=InquiryStatus.choices,
        default=InquiryStatus.SENT,
    )

    #: The landlord, or a caretaker holding the RESPOND_TO_INQUIRIES
    #: permission. Recorded because "who answered" matters when a caretaker
    #: makes a commitment the owner did not.
    responded_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="inquiries_answered",
    )
    response = models.TextField(_("response"), max_length=MAX_RESPONSE_LENGTH, blank=True)
    responded_at = models.DateTimeField(_("responded at"), null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Inquiry")
        verbose_name_plural = _("Inquiries")
        ordering = ["-created_at"]
        base_manager_name = "all_objects"
        default_manager_name = "all_objects"
        indexes = [
            # The rate limits.
            models.Index(fields=["sender", "-created_at"], name="inquiry_sender_idx"),
            models.Index(fields=["unit", "sender", "-created_at"], name="inquiry_unit_sender_idx"),
            # The landlord's queue, and the expiry sweep.
            models.Index(fields=["status", "created_at"], name="inquiry_queue_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["unit", "sender"],
                condition=Q(status__in=OPEN_INQUIRY_STATUSES),
                name="inquiry_one_open_per_unit_and_sender",
            ),
            # A response names its author and its time, or it is not a reply
            # anybody can be held to.
            models.CheckConstraint(
                condition=Q(response="")
                | (Q(responded_by__isnull=False) & Q(responded_at__isnull=False)),
                name="inquiry_response_is_attributed",
            ),
            models.CheckConstraint(
                condition=~Q(status=InquiryStatus.ANSWERED) | ~Q(response=""),
                name="inquiry_answered_has_a_response",
            ),
            models.CheckConstraint(condition=~Q(message=""), name="inquiry_message_not_empty"),
        ]

    def __str__(self) -> str:
        return f"inquiry {self.pk} about {self.unit}"

    def is_open(self) -> bool:
        """A method, not a property. See tools/check_field_shadowing.py."""
        return self.status in OPEN_INQUIRY_STATUSES

    def age(self, *, now: dt.datetime | None = None) -> dt.timedelta:
        from django.utils import timezone

        return (now or timezone.now()) - self.created_at
