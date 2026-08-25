"""
Applications and tenancies (ADR-004).

``Tenancy`` is the evidence that a stay happened, and a ``Review`` cannot exist
without one. There are two ways one comes into being, and they must not
converge:

- **witnessed** — an ``Application`` accepted on-platform creates a confirmed
  tenancy directly. The platform holds the application, the acceptance, the
  actor and the timestamp, so asking for a second confirmation would add latency
  and a dispute surface for nothing. This is the primary control on dispute
  volume.
- **claimed** — a ``TenancyClaim`` that confirmed, for stays the platform did
  not witness.
"""

from __future__ import annotations

from django.db import models
from django.db.models import F, Q
from django.utils.translation import gettext_lazy as _

from accounts.models import User
from config.tenancy import TenantScopedModel
from properties.models import Unit

from .constants import (
    OPEN_APPLICATION_STATUSES,
    UNATTRIBUTED_SOURCES,
    ApplicationStatus,
    ConfirmationSource,
    TenancyStatus,
)


class Application(TenantScopedModel):
    """A student applying for a unit.

    Distinct from an ``Inquiry``: an application is intent to take the unit, and
    accepting one is the platform witnessing an agreement.
    """

    tenant_lookup = "unit__property__campus_distances__university"

    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="applications")
    applicant = models.ForeignKey(User, on_delete=models.PROTECT, related_name="applications")

    status = models.CharField(
        _("status"),
        max_length=20,
        choices=ApplicationStatus.choices,
        default=ApplicationStatus.SUBMITTED,
    )
    move_in_date = models.DateField(_("requested move-in date"))
    intended_months = models.PositiveSmallIntegerField(_("intended stay (months)"))
    message = models.TextField(_("message"), blank=True)

    decided_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="application_decisions",
        help_text=_("The landlord or an assigned caretaker."),
    )
    decided_at = models.DateTimeField(_("decided at"), null=True, blank=True)
    decision_note = models.TextField(_("decision note"), blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Application")
        verbose_name_plural = _("Applications")
        ordering = ["-created_at"]
        base_manager_name = "all_objects"
        default_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["unit", "status"], name="application_unit_idx"),
            models.Index(fields=["applicant", "-created_at"], name="application_applicant_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["unit", "applicant"],
                condition=Q(status__in=OPEN_APPLICATION_STATUSES),
                name="application_one_open_per_unit",
            ),
            # A decision with no author cannot be audited, and accepting an
            # application is what creates a tenancy.
            models.CheckConstraint(
                condition=Q(decided_at__isnull=True) | Q(decided_by__isnull=False),
                name="application_decision_has_an_author",
            ),
            models.CheckConstraint(
                condition=Q(intended_months__gte=1), name="application_intended_months_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.applicant.get_full_name()} → {self.unit}"

    def is_open(self) -> bool:
        return self.status in OPEN_APPLICATION_STATUSES


class Tenancy(TenantScopedModel):
    """The evidence that a stay happened.

    Nothing else can vouch for a review. Two sources, and a constraint keeps
    them from blurring: an ``application``-sourced tenancy has an application
    and no claim; every other source has a claim and no application.
    """

    tenant_lookup = "unit__property__campus_distances__university"

    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="tenancies")
    tenant = models.ForeignKey(User, on_delete=models.PROTECT, related_name="tenancies")

    application = models.ForeignKey(
        Application,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tenancies",
        help_text=_("The witnessed path (ADR-004)."),
    )
    confirmation_source = models.CharField(
        _("confirmation source"), max_length=20, choices=ConfirmationSource.choices
    )
    confirmed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tenancy_confirmations",
        help_text=_("Null for 'auto' and 'dispute_timeout', which have no actor."),
    )
    confirmed_at = models.DateTimeField(_("confirmed at"))
    was_disputed = models.BooleanField(
        _("was disputed"),
        default=False,
        help_text=_("A dispute occurred, whatever its outcome. A fact, not a display decision."),
    )

    start_date = models.DateField(_("start date"))
    end_date = models.DateField(_("end date"), null=True, blank=True)
    monthly_rent_kes = models.DecimalField(_("monthly rent (KES)"), max_digits=10, decimal_places=2)
    status = models.CharField(
        _("status"), max_length=16, choices=TenancyStatus.choices, default=TenancyStatus.ACTIVE
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Tenancy")
        verbose_name_plural = _("Tenancies")
        ordering = ["-start_date"]
        base_manager_name = "all_objects"
        default_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["tenant", "-start_date"], name="tenancy_tenant_idx"),
            models.Index(fields=["unit", "status"], name="tenancy_unit_status_idx"),
            # The volume-control metric: the share of tenancies by source.
            models.Index(fields=["confirmation_source"], name="tenancy_source_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="tenancy_end_after_start",
            ),
            models.UniqueConstraint(
                fields=["unit", "tenant", "start_date"],
                name="tenancy_unique_per_unit_tenant_start",
            ),
            # Only the witnessed path exists so far: an application-sourced
            # tenancy must name its application. The next commit adds the
            # claimed path and broadens this to the full either/or.
            models.CheckConstraint(
                condition=~Q(confirmation_source=ConfirmationSource.APPLICATION)
                | Q(application__isnull=False),
                name="tenancy_application_source_has_an_application",
            ),
            models.CheckConstraint(
                condition=(
                    Q(confirmation_source__in=UNATTRIBUTED_SOURCES) & Q(confirmed_by__isnull=True)
                )
                | (
                    ~Q(confirmation_source__in=UNATTRIBUTED_SOURCES) & Q(confirmed_by__isnull=False)
                ),
                name="tenancy_confirmed_by_matches_source",
            ),
            models.CheckConstraint(
                condition=Q(monthly_rent_kes__gt=0), name="tenancy_rent_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tenant.get_full_name()} at {self.unit}"

    def is_witnessed(self) -> bool:
        """Whether the platform saw the agreement rather than being told of it."""
        return self.confirmation_source == ConfirmationSource.APPLICATION
