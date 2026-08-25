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

import datetime as dt

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators
from django.db import models
from django.db.models import F, Func, Q, Value
from django.utils.translation import gettext_lazy as _

from accounts.models import User
from config.tenancy import TenantScopedModel
from properties.models import Unit

from .constants import (
    OPEN_APPLICATION_STATUSES,
    OPEN_CLAIM_STATUSES,
    TERMINAL_CLAIM_STATUSES,
    UNATTRIBUTED_SOURCES,
    ApplicationStatus,
    ClaimStatus,
    ConfirmationSource,
    TenancyStatus,
)


class TenancyDateRange(Func):
    """``daterange(start_date, coalesce(end_date, 'infinity'), '[]')``.

    An ongoing tenancy has a null ``end_date``, and a range with a null bound is
    unbounded in PostgreSQL — which is what we want, but only if it is spelled
    out. Coalescing to infinity makes "still living there" overlap every later
    range, so a second active tenancy on the same unit is refused.
    """

    function = "daterange"
    output_field = models.Field()  # daterange; Django has no native field for it

    def __init__(self, start_field: str, end_field: str) -> None:
        super().__init__(
            F(start_field),
            Func(
                F(end_field),
                Value(dt.date.max),
                function="COALESCE",
                output_field=models.DateField(),
            ),
            Value("[]"),
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
    claim = models.ForeignKey(
        "tenancies.TenancyClaim",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tenancies",
        help_text=_(
            "The claimed path. PROTECT, not SET_NULL: the review's dispute "
            "annotation is derived from this record, so it must survive."
        ),
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
            # The two paths cannot blur. Either the platform witnessed the
            # agreement or somebody claimed it; never both, never neither.
            models.CheckConstraint(
                condition=(
                    Q(confirmation_source=ConfirmationSource.APPLICATION)
                    & Q(application__isnull=False)
                    & Q(claim__isnull=True)
                )
                | (
                    ~Q(confirmation_source=ConfirmationSource.APPLICATION)
                    & Q(claim__isnull=False)
                    & Q(application__isnull=True)
                ),
                name="tenancy_source_matches_its_origin",
            ),
            # One person cannot hold the same unit twice over overlapping
            # dates. A serializer cannot see a concurrent insert; this can.
            # Requires btree_gist, from universities/migrations/0001_extensions.
            #
            # Scoped per unit AND per tenant, not per unit alone. A Unit row
            # can represent a POOL -- forty identical bedsitters in a hostel
            # block are one row with total_count=40 -- so a per-unit-only
            # exclusion would let exactly one student occupy the whole block.
            # Vacancy is counted by vacant_count, not by the absence of a
            # tenancy row.
            ExclusionConstraint(
                name="tenancy_no_overlapping_active_stay",
                expressions=[
                    ("unit", RangeOperators.EQUAL),
                    ("tenant", RangeOperators.EQUAL),
                    (TenancyDateRange("start_date", "end_date"), RangeOperators.OVERLAPS),
                ],
                condition=Q(status=TenancyStatus.ACTIVE),
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


class TenancyClaim(TenantScopedModel):
    """A tenant asserting a stay the platform did not witness (ADR-004).

    **Only** for off-platform arrangements and pre-platform history. An accepted
    ``Application`` produces a confirmed ``Tenancy`` directly, with no claim at
    all — that is the primary control on dispute volume, and ADR-004 says
    explicitly that it must not be "simplified" into one uniform path.

    The tenant initiates. The landlord and any assigned caretaker have
    ``settings.TENANCY_CONFIRMATION_WINDOW_DAYS`` to confirm or dispute, and
    silence auto-confirms: landlord silence is a signal, not a veto.
    """

    tenant_lookup = "unit__property__campus_distances__university"

    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="tenancy_claims")
    claimant = models.ForeignKey(User, on_delete=models.PROTECT, related_name="tenancy_claims")

    start_date = models.DateField(_("start date"))
    end_date = models.DateField(_("end date"), null=True, blank=True)
    monthly_rent_kes = models.DecimalField(_("monthly rent (KES)"), max_digits=10, decimal_places=2)

    status = models.CharField(
        _("status"), max_length=16, choices=ClaimStatus.choices, default=ClaimStatus.PENDING
    )
    confirmation_deadline = models.DateTimeField(
        _("confirmation deadline"),
        help_text=_("Silence past this point auto-confirms the claim."),
    )

    is_retrospective = models.BooleanField(
        _("retrospective"),
        default=False,
        help_text=_(
            "The stay predates the property's presence on the platform. For "
            "analytics and the operations queue ONLY — never for display and "
            "never for weighting (ADR-004). A flag that reaches the UI becomes "
            "a second class of review."
        ),
    )

    resolved_at = models.DateTimeField(_("resolved at"), null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Tenancy claim")
        verbose_name_plural = _("Tenancy claims")
        ordering = ["-created_at"]
        base_manager_name = "all_objects"
        default_manager_name = "all_objects"
        indexes = [
            # The auto-confirm job, and the overdue-claim alert. Both read the
            # OLDEST row, never the count (docs/OPERATIONS.md).
            models.Index(fields=["status", "confirmation_deadline"], name="claim_deadline_idx"),
            # The per-claimant rate limit.
            models.Index(fields=["claimant", "-created_at"], name="claim_claimant_idx"),
            models.Index(fields=["is_retrospective"], name="claim_retrospective_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["unit", "claimant"],
                condition=Q(status__in=OPEN_CLAIM_STATUSES),
                name="claim_one_open_per_unit_and_claimant",
            ),
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
                name="claim_end_after_start",
            ),
            models.CheckConstraint(
                condition=~Q(status__in=TERMINAL_CLAIM_STATUSES) | Q(resolved_at__isnull=False),
                name="claim_terminal_status_has_a_resolution_time",
            ),
            models.CheckConstraint(condition=Q(monthly_rent_kes__gt=0), name="claim_rent_positive"),
        ]

    def __str__(self) -> str:
        return f"claim: {self.claimant.get_full_name()} at {self.unit}"

    def is_open(self) -> bool:
        return self.status in OPEN_CLAIM_STATUSES

    def was_disputed_at_any_point(self) -> bool:
        """Whether a dispute was ever raised, whatever its outcome.

        Copied onto the resulting Tenancy as a fact. The dispute fields arrive
        with the state machine in the next commit; until then no claim has been
        disputed.
        """
        return self.status in (ClaimStatus.DISPUTED, ClaimStatus.ESCALATED)
