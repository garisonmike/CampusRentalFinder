"""
Application, claim and tenancy serializers (ADR-004).

The most careful thing in this file is what `TenancyStatus` does **not**
contain. There is no value meaning "current" — currency is derived from
`start_date` and `end_date` at query time, because a stored currency flag needs
a job to keep it true and lies silently when the job stops. The contract note
says so on the status field, on `end_date`, and on the endpoint, because it is
the most likely misread in the whole API.
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from config.api.contract import (
    TENANCY_END_DATE,
    TENANCY_STATUS,
    TENANCY_TERMINATED_EARLY,
)

from .models import Application, Tenancy, TenancyClaim


class ApplicationSerializer(serializers.ModelSerializer):
    """An application, as either party sees it."""

    unit_label = serializers.CharField(source="unit.label", read_only=True)
    property_name = serializers.CharField(source="unit.property.name", read_only=True)
    property_slug = serializers.CharField(source="unit.property.slug", read_only=True)
    applicant_name = serializers.SerializerMethodField()

    class Meta:
        model = Application
        fields = (
            "id",
            "unit",
            "unit_label",
            "property_name",
            "property_slug",
            "applicant_name",
            "move_in_date",
            "intended_months",
            "message",
            "status",
            "decision_note",
            "decided_at",
            "created_at",
            "inquiry",
        )
        read_only_fields = (
            "id",
            "unit_label",
            "property_name",
            "property_slug",
            "applicant_name",
            "status",
            "decision_note",
            "decided_at",
            "created_at",
        )
        extra_kwargs = {
            "inquiry": {
                "required": False,
                "allow_null": True,
                "help_text": (
                    "The inquiry this application came from, if any. Optional, "
                    "and its only purpose is to make the on-platform path "
                    "traceable end to end."
                ),
            },
            "status": {
                "help_text": (
                    "submitted | under_review | accepted | rejected | "
                    "withdrawn | expired. An accepted application creates a "
                    "confirmed tenancy directly -- no claim, no confirmation "
                    "window, no dispute surface (ADR-004 §1.1)."
                )
            },
        }

    @extend_schema_field(serializers.CharField())
    def get_applicant_name(self, application: Application) -> str:
        from accounts.privacy import display_name_for

        return display_name_for(application.applicant)


class ApplicationCreateSerializer(serializers.ModelSerializer):
    """Applying for a unit."""

    class Meta:
        model = Application
        fields = ("unit", "move_in_date", "intended_months", "message", "inquiry")
        extra_kwargs = {
            "inquiry": {"required": False, "allow_null": True},
            "message": {"required": False, "allow_blank": True},
        }


class ApplicationDecisionSerializer(serializers.Serializer):
    """Accepting or rejecting one."""

    note = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=500,
        help_text="Shown to the applicant. A rejection with no reason gives them nothing to act on.",
    )
    start_date = serializers.DateField(
        required=False,
        allow_null=True,
        help_text="Agreed start, if different from the applied-for move-in date.",
    )
    end_date = serializers.DateField(
        required=False,
        allow_null=True,
        help_text=(
            "Agreed end. **Omit for an open-ended tenancy** -- null means no "
            "agreed end and currently running, not unknown."
        ),
    )
    monthly_rent_kes = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        allow_null=True,
        help_text="Agreed rent, if different from the unit's advertised rent.",
    )


class TenancySerializer(serializers.ModelSerializer):
    """A tenancy record.

    Read the three currency notes below before rendering anything from this.
    """

    unit_label = serializers.CharField(source="unit.label", read_only=True)
    property_name = serializers.CharField(source="unit.property.name", read_only=True)
    property_slug = serializers.CharField(source="unit.property.slug", read_only=True)
    tenant_name = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField(
        help_text=(
            "DERIVED from start_date and end_date at read time: `current`, "
            "`past` or `upcoming`. There is no stored field behind this and "
            "there must not be -- a stored currency flag needs a job to stay "
            "true and lies silently when the job stops. Filter lists with "
            "?currency= rather than by status."
        )
    )
    is_reviewable = serializers.SerializerMethodField(
        help_text=(
            "Whether this stay is long enough to review and has not been "
            "reviewed yet. Derived, because the minimum-stay rule compares "
            "against today (ADR-004)."
        )
    )

    class Meta:
        model = Tenancy
        fields = (
            "id",
            "unit",
            "unit_label",
            "property_name",
            "property_slug",
            "tenant_name",
            "start_date",
            "end_date",
            "monthly_rent_kes",
            "status",
            "currency",
            "confirmation_source",
            "was_disputed",
            "terminated_early",
            "termination_reason",
            "is_reviewable",
            "created_at",
        )
        read_only_fields = fields
        extra_kwargs = {
            "status": {"help_text": TENANCY_STATUS},
            "end_date": {"help_text": TENANCY_END_DATE},
            "terminated_early": {"help_text": TENANCY_TERMINATED_EARLY},
        }

    @extend_schema_field(serializers.CharField())
    def get_tenant_name(self, tenancy: Tenancy) -> str:
        from accounts.privacy import display_name_for

        return display_name_for(tenancy.tenant)

    @extend_schema_field(serializers.ChoiceField(choices=["current", "past", "upcoming"]))
    def get_currency(self, tenancy: Tenancy) -> str:
        return tenancy.currency()

    @extend_schema_field(serializers.BooleanField())
    def get_is_reviewable(self, tenancy: Tenancy) -> bool:
        from reviews.services import assert_tenancy_is_reviewable

        try:
            assert_tenancy_is_reviewable(tenancy)
        except Exception:
            return False
        return True


class TenancyClaimSerializer(serializers.ModelSerializer):
    """A claim, as the claimant or the landlord sees it."""

    unit_label = serializers.CharField(source="unit.label", read_only=True)
    property_name = serializers.CharField(source="unit.property.name", read_only=True)
    claimant_name = serializers.SerializerMethodField()

    class Meta:
        model = TenancyClaim
        fields = (
            "id",
            "unit",
            "unit_label",
            "property_name",
            "claimant_name",
            "start_date",
            "end_date",
            "monthly_rent_kes",
            "status",
            "is_retrospective",
            "confirmation_deadline",
            "dispute_reason",
            "dispute_note",
            "proposed_start_date",
            "proposed_end_date",
            "counter_start_date",
            "counter_end_date",
            "escalation_reason",
            "escalation_deadline",
            "created_at",
            "resolved_at",
        )
        read_only_fields = fields
        extra_kwargs = {
            "status": {
                "help_text": (
                    "pending | confirmed | disputed | escalated | withdrawn | "
                    "expired. Silence past `confirmation_deadline` "
                    "auto-confirms: landlord silence is a signal, not a veto "
                    "(ADR-004)."
                )
            },
            "is_retrospective": {
                "help_text": (
                    "The stay predates the property's presence on the "
                    "platform. **For analytics and the operations queue only "
                    "-- never for display and never for weighting.** A flag "
                    "that reaches the UI becomes a second class of review."
                )
            },
            "escalation_reason": {
                "help_text": (
                    "What an administrator has to decide, distinct from why "
                    "the dispute was raised. counter_unresolved | "
                    "correction_defeats_review | identity_disputed | "
                    "duplicate_unmatched."
                )
            },
        }

    @extend_schema_field(serializers.CharField())
    def get_claimant_name(self, claim: TenancyClaim) -> str:
        from accounts.privacy import display_name_for

        return display_name_for(claim.claimant)


class ClaimCreateSerializer(serializers.Serializer):
    """Raising a claim for a stay the platform did not witness."""

    unit = serializers.IntegerField(help_text="Id of the unit you stayed in.")
    start_date = serializers.DateField()
    end_date = serializers.DateField(
        required=False,
        allow_null=True,
        help_text="Omit if you are still living there.",
    )
    monthly_rent_kes = serializers.DecimalField(max_digits=10, decimal_places=2)
    is_retrospective = serializers.BooleanField(
        required=False,
        default=False,
        help_text=(
            "The stay predates this property's presence on the platform. Runs "
            "through identical machinery with no lower evidentiary bar."
        ),
    )


class DisputeSerializer(serializers.Serializer):
    """Disputing a claim, with a typed reason."""

    reason = serializers.ChoiceField(
        choices=["dates_incorrect", "never_tenanted", "duplicate"],
        help_text=(
            "Enumerated, because an untyped dispute cannot be routed and can "
            "therefore only go to a human. Free text belongs in `note` as "
            "additional context, never as a substitute."
        ),
    )
    note = serializers.CharField(required=False, allow_blank=True, max_length=1000)
    proposed_start_date = serializers.DateField(
        required=False,
        allow_null=True,
        help_text="Required for `dates_incorrect`: the dates you say are right.",
    )
    proposed_end_date = serializers.DateField(required=False, allow_null=True)


class CorrectionSerializer(serializers.Serializer):
    """The tenant's single counter-offer on a dates dispute."""

    start_date = serializers.DateField()
    end_date = serializers.DateField(required=False, allow_null=True)
