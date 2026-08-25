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
    DISPUTE_TRANSITIONS,
    DISPUTED_CLAIM_STATUSES,
    OPEN_APPLICATION_STATUSES,
    OPEN_CLAIM_STATUSES,
    TERMINAL_CLAIM_STATUSES,
    UNATTRIBUTED_SOURCES,
    ApplicationStatus,
    ClaimStatus,
    ConfirmationSource,
    DisputeReason,
    EscalationReason,
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


def _permitted_escalations_condition() -> Q:
    """Build the escalation check constraint from ``DISPUTE_TRANSITIONS``.

    Generated rather than written out, so the transition table stays the only
    place a routing decision is recorded (ADR-004 section 2c). Adding a dispute
    reason to the table and forgetting the constraint is not a mistake anyone
    can make here, because there is nothing to forget.
    """
    condition = Q(escalation_reason="")
    for reason, transition in DISPUTE_TRANSITIONS.items():
        if transition.escalates_to:
            condition |= Q(dispute_reason=reason, escalation_reason__in=transition.escalates_to)
    return condition


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

    # -- The dispute, as raised (ADR-004 section 2) ------------------------
    #
    # dispute_reason is never rewritten. It records what the disputer actually
    # claimed; where the dispute ended up is escalation_reason.
    dispute_reason = models.CharField(
        _("dispute reason"),
        max_length=24,
        choices=DisputeReason.choices,
        blank=True,
        help_text=_("Enumerated, because an untyped dispute can only be routed to a human."),
    )
    dispute_note = models.TextField(
        _("dispute note"),
        blank=True,
        help_text=_("Additional context. Never a substitute for the enumerated reason."),
    )
    disputed_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="raised_disputes",
        null=True,
        blank=True,
    )
    disputed_at = models.DateTimeField(_("disputed at"), null=True, blank=True)

    # -- dates_incorrect: the correction exchange --------------------------
    proposed_start_date = models.DateField(_("proposed start date"), null=True, blank=True)
    proposed_end_date = models.DateField(_("proposed end date"), null=True, blank=True)

    #: The tenant may counter exactly once. A counter the disputer does not
    #: accept escalates as counter_unresolved.
    counter_start_date = models.DateField(_("counter start date"), null=True, blank=True)
    counter_end_date = models.DateField(_("counter end date"), null=True, blank=True)

    #: Evidence, NOT a resolution (ADR-004 section 2b). When a correction would
    #: drop the stay under the review minimum, the tenant's acceptance is
    #: recorded and shown to the admin -- but it does not settle the dispute,
    #: because the accepting party may not realise what they accepted.
    tenant_accepted_correction_at = models.DateTimeField(
        _("tenant accepted the correction at"), null=True, blank=True
    )

    # -- The escalation ----------------------------------------------------
    escalation_reason = models.CharField(
        _("escalation reason"),
        max_length=26,
        choices=EscalationReason.choices,
        blank=True,
        help_text=_("What the administrator has to decide. The queue sorts on this."),
    )
    escalated_at = models.DateTimeField(_("escalated at"), null=True, blank=True)
    #: The disputer took it back. Load-bearing for ADR-004 section 3a: the
    #: review annotation is derived at read time, so withdrawing a dispute
    #: clears it -- which is the whole reason it is not a stored boolean.
    dispute_withdrawn_at = models.DateTimeField(_("dispute withdrawn at"), null=True, blank=True)
    escalation_deadline = models.DateTimeField(
        _("escalation deadline"),
        null=True,
        blank=True,
        help_text=_(
            "The deadline binds the PLATFORM, not the tenant. Past it the claim "
            "confirms in the tenant's favour (ADR-004)."
        ),
    )

    resolved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="resolved_claims",
        null=True,
        blank=True,
        help_text=_("Null when the claim resolved by timeout: silence has no author."),
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
            # The admin queue: filtered by what has to be decided, worked
            # oldest-first, and swept by the symmetric-timeout job.
            models.Index(fields=["escalation_reason", "escalated_at"], name="claim_queue_idx"),
            models.Index(fields=["status", "escalation_deadline"], name="claim_escalation_sla_idx"),
            # The dispute_rate metric (docs/OPERATIONS.md).
            models.Index(fields=["disputed_by", "-disputed_at"], name="claim_disputer_idx"),
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
            # A dispute carries an enumerated reason and an author. Without
            # both it cannot be routed, so it could only ever go to a human.
            models.CheckConstraint(
                condition=~Q(status__in=DISPUTED_CLAIM_STATUSES)
                | (
                    ~Q(dispute_reason="")
                    & Q(disputed_by__isnull=False)
                    & Q(disputed_at__isnull=False)
                ),
                name="claim_dispute_is_typed_and_attributed",
            ),
            # A dates_incorrect dispute must carry the corrected dates. A
            # correction nobody stated is not something the tenant can accept.
            models.CheckConstraint(
                condition=~Q(dispute_reason=DisputeReason.DATES_INCORRECT)
                | Q(proposed_start_date__isnull=False),
                name="claim_correction_states_its_dates",
            ),
            models.CheckConstraint(
                condition=Q(proposed_end_date__isnull=True)
                | Q(proposed_end_date__gte=F("proposed_start_date")),
                name="claim_proposed_end_after_start",
            ),
            models.CheckConstraint(
                condition=Q(counter_end_date__isnull=True)
                | Q(counter_end_date__gte=F("counter_start_date")),
                name="claim_counter_end_after_start",
            ),
            # You cannot counter a correction that was not proposed.
            models.CheckConstraint(
                condition=Q(counter_start_date__isnull=True) | Q(proposed_start_date__isnull=False),
                name="claim_counter_answers_a_correction",
            ),
            # The deadline exists exactly when the escalation does. A queue
            # entry with no deadline is the indefinite block again.
            models.CheckConstraint(
                condition=Q(escalated_at__isnull=True, escalation_deadline__isnull=True)
                | Q(escalated_at__isnull=False, escalation_deadline__isnull=False),
                name="claim_escalation_has_a_deadline",
            ),
            # An escalated claim names what the admin has to decide.
            models.CheckConstraint(
                condition=~Q(status=ClaimStatus.ESCALATED)
                | (~Q(escalation_reason="") & Q(escalated_at__isnull=False)),
                name="claim_escalation_names_its_question",
            ),
            # Generated from DISPUTE_TRANSITIONS, so the table is the only
            # place a transition is written down (ADR-004 section 2c). A new
            # dispute reason with no escalation path cannot reach the queue,
            # and a mismatched pair cannot be stored at all.
            models.CheckConstraint(
                condition=_permitted_escalations_condition(),
                name="claim_escalation_matches_its_dispute",
            ),
        ]

    def __str__(self) -> str:
        return f"claim: {self.claimant.get_full_name()} at {self.unit}"

    def is_open(self) -> bool:
        return self.status in OPEN_CLAIM_STATUSES

    def was_disputed_at_any_point(self) -> bool:
        """Whether a dispute was ever raised, whatever its outcome.

        Copied onto the resulting Tenancy as a fact, and read by the review
        annotation. Reads ``disputed_at`` rather than the current status,
        because a claim that was disputed and then confirmed is no longer in a
        disputed status but was still disputed.
        """
        return self.disputed_at is not None

    def stay_days(self) -> int | None:
        """Length of the claimed stay, or None while it is ongoing."""
        if self.end_date is None:
            return None
        return (self.end_date - self.start_date).days
