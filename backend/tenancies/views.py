"""
Application, claim, tenancy and dispute endpoints (ADR-004).

Two paths, and they must not blur.

**The witnessed path.** A student applies; the landlord accepts; a confirmed
tenancy exists immediately. No claim, no confirmation window, no dispute
surface, no queue entry. This is the primary control on dispute volume and the
ADR says explicitly it must not be "simplified" into one uniform path.

**The claimed path.** For stays the platform did not witness — off-platform
arrangements and pre-platform history. The tenant asserts; the landlord has a
window to confirm or dispute; silence confirms.

Every rule lives in the service layer. These views resolve objects, check
relationships and call through. Where a view looks thin, that is the design.
"""

from __future__ import annotations

from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsPlatformStaff
from config.api.throttling import Scope
from config.api.views import SchemaSafeQuerysetMixin
from config.middleware import get_current_university
from properties.constants import PropertyStatus
from properties.models import Unit

from .constants import ClaimStatus, ConfirmationSource, TenancyCurrency
from .models import Application, Tenancy, TenancyClaim
from .serializers import (
    ApplicationCreateSerializer,
    ApplicationDecisionSerializer,
    ApplicationSerializer,
    ClaimCreateSerializer,
    CorrectionSerializer,
    DisputeSerializer,
    TenancyClaimSerializer,
    TenancySerializer,
)
from .services import (
    accept_application,
    accept_correction,
    accept_counter,
    confirm_claim,
    counter_correction,
    create_claim,
    raise_dispute,
    reject_application,
    reject_counter,
    resolve_escalation,
    withdraw_application,
)


def _tenant():
    university = get_current_university()
    if university is None:
        raise NotFound("No university is configured for this host.")
    return university


def _manages(user, prop) -> bool:
    """Whether this user is the owner or an assigned caretaker."""
    from accounts.models import CaretakerAssignment

    landlord_profile = getattr(user, "landlord_profile", None)
    if landlord_profile is not None and prop.landlord_id == landlord_profile.pk:
        return True
    return CaretakerAssignment.all_objects.filter(user=user, property=prop, is_active=True).exists()


# ---------------------------------------------------------------------------
# Applications: the witnessed path
# ---------------------------------------------------------------------------


@extend_schema_view(
    get=extend_schema(
        summary="Applications you sent, or for property you manage",
        description=(
            "Scoped by relationship: an applicant sees their own, a landlord "
            "or assigned caretaker sees those for their property."
        ),
    ),
    post=extend_schema(
        summary="Apply for a unit",
        description=(
            "The on-platform path. When this is accepted a confirmed tenancy "
            "is created **directly** -- no claim, no confirmation window, no "
            "dispute surface. That is ADR-004's primary control on dispute "
            "volume, and it is why applying is worth preferring over arranging "
            "off-platform and claiming later."
        ),
        request=ApplicationCreateSerializer,
    ),
)
class ApplicationListView(SchemaSafeQuerysetMixin, ListAPIView):
    """Applications the caller is a party to."""

    serializer_class = ApplicationSerializer
    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.AUTHENTICATED_READ
    schema_queryset = Application.all_objects

    def get_queryset(self):
        if self.is_schema_generation():
            return self.empty_queryset()

        user = self.request.user
        landlord_profile = getattr(user, "landlord_profile", None)

        visible = Q(applicant=user)
        if landlord_profile is not None:
            visible |= Q(unit__property__landlord=landlord_profile)
        visible |= Q(unit__property__caretaker_assignments__user=user)

        return (
            Application.objects.for_tenant(_tenant())
            .filter(visible)
            .select_related("unit__property", "applicant")
            .distinct()
            .order_by("-created_at")
        )

    def post(self, request):
        serializer = ApplicationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        unit = get_object_or_404(
            Unit.objects.for_tenant(_tenant()).filter(property__status=PropertyStatus.PUBLISHED),
            pk=serializer.validated_data["unit"].pk,
        )
        application = Application.all_objects.create(
            applicant=request.user,
            **{**serializer.validated_data, "unit": unit},
        )

        return Response(ApplicationSerializer(application).data, status=201)


@extend_schema_view(
    post=extend_schema(
        summary="Accept an application",
        description=(
            "Creates a confirmed tenancy in the same transaction. An accepted "
            "application with no tenancy is a stay the platform witnessed and "
            "cannot vouch for -- exactly the gap the tenancy record closes.\n\n"
            "Omit `end_date` for an open-ended tenancy. Null there means no "
            "agreed end and currently running."
        ),
        request=ApplicationDecisionSerializer,
    )
)
class ApplicationAcceptView(APIView):
    """Accept, and create the tenancy it implies."""

    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.WRITE

    def post(self, request, pk: int):
        application = get_object_or_404(
            Application.objects.for_tenant(_tenant()).select_related("unit__property"),
            pk=pk,
        )
        if not _manages(request.user, application.unit.property):
            raise NotFound("No such application.")

        serializer = ApplicationDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        tenancy = accept_application(
            application,
            decided_by=request.user,
            note=serializer.validated_data.get("note", ""),
            start_date=serializer.validated_data.get("start_date"),
            end_date=serializer.validated_data.get("end_date"),
            monthly_rent_kes=serializer.validated_data.get("monthly_rent_kes"),
        )

        return Response(TenancySerializer(tenancy).data, status=201)


@extend_schema_view(
    post=extend_schema(
        summary="Reject an application",
        description="Creates nothing. The reason is shown to the applicant.",
        request=ApplicationDecisionSerializer,
    )
)
class ApplicationRejectView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.WRITE

    def post(self, request, pk: int):
        application = get_object_or_404(
            Application.objects.for_tenant(_tenant()).select_related("unit__property"),
            pk=pk,
        )
        if not _manages(request.user, application.unit.property):
            raise NotFound("No such application.")

        serializer = ApplicationDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        return Response(
            ApplicationSerializer(
                reject_application(
                    application,
                    decided_by=request.user,
                    note=serializer.validated_data.get("note", ""),
                )
            ).data
        )


@extend_schema_view(
    post=extend_schema(
        summary="Withdraw your own application",
        description=(
            "The applicant's own act, so no decider is recorded -- withdrawing "
            "is not a decision made about them."
        ),
        request=None,
    )
)
class ApplicationWithdrawView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.WRITE

    def post(self, request, pk: int):
        application = get_object_or_404(
            Application.objects.for_tenant(_tenant()), pk=pk, applicant=request.user
        )
        return Response(ApplicationSerializer(withdraw_application(application)).data)


# ---------------------------------------------------------------------------
# Tenancies
# ---------------------------------------------------------------------------


@extend_schema_view(
    get=extend_schema(
        summary="Your tenancies",
        description=(
            "**Currency is derived, not stored.** There is no status value "
            "meaning 'current' -- filter with `?currency=current|past|"
            "upcoming`, which is computed from start_date and end_date at "
            "query time.\n\n"
            "A null `end_date` means the tenancy is OPEN-ENDED AND STILL "
            "RUNNING, not that it ended at an unknown time. Most Kenyan "
            "student lets are month-to-month with no written end, so this is "
            "the common case rather than an edge one."
        ),
        parameters=[
            OpenApiParameter(
                name="currency",
                description=("Derived from the dates. Omit for all live tenancies."),
                required=False,
                type=str,
                enum=["current", "past", "upcoming"],
            )
        ],
    )
)
class TenancyListView(SchemaSafeQuerysetMixin, ListAPIView):
    """Tenancies the caller is a party to."""

    serializer_class = TenancySerializer
    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.AUTHENTICATED_READ
    schema_queryset = Tenancy.all_objects

    def get_queryset(self):
        if self.is_schema_generation():
            return self.empty_queryset()

        user = self.request.user
        landlord_profile = getattr(user, "landlord_profile", None)

        visible = Q(tenant=user)
        if landlord_profile is not None:
            visible |= Q(unit__property__landlord=landlord_profile)

        queryset = (
            Tenancy.objects.for_tenant(_tenant())
            .filter(visible)
            .select_related("unit__property", "tenant")
            .distinct()
        )

        # Through the named queryset methods, never by filtering a status
        # value -- there is no status value that means "current", and a client
        # that tried would silently get an empty page.
        currency = self.request.query_params.get("currency")
        if currency == TenancyCurrency.CURRENT:
            queryset = queryset.current()
        elif currency == TenancyCurrency.PAST:
            queryset = queryset.past()
        elif currency == TenancyCurrency.UPCOMING:
            queryset = queryset.upcoming()
        else:
            queryset = queryset.live()

        return queryset.order_by("-start_date", "-id")


# ---------------------------------------------------------------------------
# Claims: the path the platform did not witness
# ---------------------------------------------------------------------------


@extend_schema_view(
    get=extend_schema(
        summary="Claims you raised, or that concern property you manage",
    ),
    post=extend_schema(
        summary="Claim a stay the platform did not witness",
        description=(
            "For off-platform arrangements and pre-platform history **only**. "
            "An accepted application creates a confirmed tenancy directly and "
            "must never come through here.\n\n"
            "The landlord has `TENANCY_CONFIRMATION_WINDOW_DAYS` to confirm or "
            "dispute. **Silence auto-confirms**: landlord silence is a signal, "
            "not a veto (ADR-004).\n\n"
            "Rate-limited per user over a rolling 30 days, and refused with an "
            "explanation rather than silently dropped -- a genuine flood needs "
            "somewhere to go."
        ),
        request=ClaimCreateSerializer,
    ),
)
class ClaimListView(SchemaSafeQuerysetMixin, ListAPIView):
    """Claims the caller is a party to."""

    serializer_class = TenancyClaimSerializer
    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.AUTHENTICATED_READ
    schema_queryset = TenancyClaim.all_objects

    def get_queryset(self):
        if self.is_schema_generation():
            return self.empty_queryset()

        user = self.request.user
        landlord_profile = getattr(user, "landlord_profile", None)

        visible = Q(claimant=user)
        if landlord_profile is not None:
            visible |= Q(unit__property__landlord=landlord_profile)
        visible |= Q(unit__property__caretaker_assignments__user=user)

        return (
            TenancyClaim.objects.for_tenant(_tenant())
            .filter(visible)
            .select_related("unit__property", "claimant")
            .distinct()
            .order_by("-created_at")
        )

    def post(self, request):
        serializer = ClaimCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        unit = get_object_or_404(
            Unit.objects.for_tenant(_tenant()), pk=serializer.validated_data["unit"]
        )
        claim = create_claim(
            unit=unit,
            claimant=request.user,
            start_date=serializer.validated_data["start_date"],
            end_date=serializer.validated_data.get("end_date"),
            monthly_rent_kes=serializer.validated_data["monthly_rent_kes"],
            is_retrospective=serializer.validated_data.get("is_retrospective", False),
        )

        return Response(TenancyClaimSerializer(claim).data, status=201)


class ClaimActionView(APIView):
    """Base for the landlord-side claim actions."""

    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.WRITE

    def get_claim(self, request, pk: int) -> TenancyClaim:
        claim = get_object_or_404(
            TenancyClaim.objects.for_tenant(_tenant()).select_related("unit__property"),
            pk=pk,
        )
        if not _manages(request.user, claim.unit.property):
            raise NotFound("No such claim.")
        return claim


@extend_schema_view(
    post=extend_schema(
        summary="Confirm a claim",
        description=(
            "The landlord or an assigned caretaker agreeing the stay happened. Creates the tenancy."
        ),
        request=None,
    )
)
class ClaimConfirmView(ClaimActionView):
    def post(self, request, pk: int):
        claim = self.get_claim(request, pk)

        source = (
            ConfirmationSource.LANDLORD
            if getattr(request.user, "landlord_profile", None)
            and claim.unit.property.landlord_id == request.user.landlord_profile.pk
            else ConfirmationSource.CARETAKER
        )
        tenancy = confirm_claim(claim, source=source, confirmed_by=request.user)

        return Response(TenancySerializer(tenancy).data, status=201)


@extend_schema_view(
    post=extend_schema(
        summary="Dispute a claim, with a typed reason",
        description=(
            "The reason is enumerated because an untyped dispute cannot be "
            "routed and can therefore only go to a human -- and most disputes "
            "must not reach one, or the queue is unbounded (ADR-004 §2).\n\n"
            "`dates_incorrect` stays between the parties. `duplicate` "
            "auto-resolves against the same predicate the exclusion constraint "
            "enforces. `never_tenanted` goes straight to an administrator, "
            "because an identity question is not something the parties can "
            "settle.\n\n"
            "**A correction that would drop the stay below the review minimum "
            "cannot auto-resolve at all**, even with the tenant's acceptance."
        ),
        request=DisputeSerializer,
    )
)
class ClaimDisputeView(ClaimActionView):
    def post(self, request, pk: int):
        claim = self.get_claim(request, pk)

        serializer = DisputeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        disputed = raise_dispute(
            claim,
            reason=serializer.validated_data["reason"],
            disputed_by=request.user,
            note=serializer.validated_data.get("note", ""),
            proposed_start_date=serializer.validated_data.get("proposed_start_date"),
            proposed_end_date=serializer.validated_data.get("proposed_end_date"),
        )

        return Response(TenancyClaimSerializer(disputed).data)


class ClaimantActionView(APIView):
    """Base for the claimant-side responses to a dispute."""

    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.WRITE

    def get_claim(self, request, pk: int) -> TenancyClaim:
        return get_object_or_404(
            TenancyClaim.objects.for_tenant(_tenant()).select_related("unit__property"),
            pk=pk,
            claimant=request.user,
        )


@extend_schema_view(
    post=extend_schema(
        summary="Accept the disputer's corrected dates",
        description=(
            "Normally settles the dispute with no administrator involved.\n\n"
            "**Unless the correction would make the stay too short to "
            "review.** Then it escalates as `correction_defeats_review` even "
            "though you agreed, because a tenant who misremembers by a week -- "
            "or who simply wants the argument over -- may not realise that what "
            "they accepted also deletes their review. Your acceptance is "
            "recorded as evidence for the administrator, who will usually find "
            "the correction honest (ADR-004 §2b)."
        ),
        request=None,
    )
)
class ClaimAcceptCorrectionView(ClaimantActionView):
    def post(self, request, pk: int):
        result = accept_correction(self.get_claim(request, pk))

        if isinstance(result, Tenancy):
            return Response(TenancySerializer(result).data, status=201)
        return Response(TenancyClaimSerializer(result).data)


@extend_schema_view(
    post=extend_schema(
        summary="Counter the correction, once",
        description=(
            "Once, because an unbounded exchange between two people who "
            "disagree is an indefinite block by another name."
        ),
        request=CorrectionSerializer,
    )
)
class ClaimCounterView(ClaimantActionView):
    def post(self, request, pk: int):
        serializer = CorrectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        countered = counter_correction(
            self.get_claim(request, pk),
            start_date=serializer.validated_data["start_date"],
            end_date=serializer.validated_data.get("end_date"),
        )

        return Response(TenancyClaimSerializer(countered).data)


@extend_schema_view(
    post=extend_schema(
        summary="Accept the tenant's counter-dates",
        description="The same review-defeating guard applies: a correction laundered through a counter is still a correction.",
        request=None,
    )
)
class ClaimAcceptCounterView(ClaimActionView):
    def post(self, request, pk: int):
        result = accept_counter(self.get_claim(request, pk))

        if isinstance(result, Tenancy):
            return Response(TenancySerializer(result).data, status=201)
        return Response(TenancyClaimSerializer(result).data)


@extend_schema_view(
    post=extend_schema(
        summary="Reject the counter",
        description="Two parties, no agreement. Escalates as `counter_unresolved`.",
        request=None,
    )
)
class ClaimRejectCounterView(ClaimActionView):
    def post(self, request, pk: int):
        return Response(TenancyClaimSerializer(reject_counter(self.get_claim(request, pk))).data)


# ---------------------------------------------------------------------------
# The administrator's queue
# ---------------------------------------------------------------------------


@extend_schema_view(
    get=extend_schema(
        summary="The dispute queue",
        description=(
            "Escalated claims awaiting a decision, oldest first.\n\n"
            "**Filter by `escalation_reason`.** Working a mixed queue "
            "oldest-first is right; working it without knowing which kind of "
            "question each item is means gathering the wrong evidence first. "
            "An identity dispute and a fortnight's disagreement about dates "
            "need completely different evidence (ADR-004 §2a).\n\n"
            "`dispute_reason` is what the disputer claimed and is never "
            "rewritten; `escalation_reason` is what you have to decide."
        ),
        parameters=[
            OpenApiParameter(
                name="escalation_reason",
                required=False,
                type=str,
                enum=[
                    "counter_unresolved",
                    "correction_defeats_review",
                    "identity_disputed",
                    "duplicate_unmatched",
                ],
            )
        ],
    )
)
class DisputeQueueView(SchemaSafeQuerysetMixin, ListAPIView):
    """Platform staff only."""

    serializer_class = TenancyClaimSerializer
    permission_classes = [IsAuthenticated, IsPlatformStaff]
    throttle_scope = Scope.AUTHENTICATED_READ
    schema_queryset = TenancyClaim.all_objects

    def get_queryset(self):
        if self.is_schema_generation():
            return self.empty_queryset()

        # Deliberately across tenants: the queue is worked by platform staff,
        # not by a university, and an escalation missed because it belonged to
        # a school nobody was looking at is the indefinite block returning.
        queryset = TenancyClaim.all_objects.filter(status=ClaimStatus.ESCALATED).select_related(
            "unit__property", "claimant"
        )

        reason = self.request.query_params.get("escalation_reason")
        if reason:
            queryset = queryset.filter(escalation_reason=reason)

        return queryset.order_by("escalated_at")


@extend_schema_view(
    post=extend_schema(
        summary="Decide an escalated claim",
        description="Upholding it confirms the claim as an administrator decision.",
        request=None,
    )
)
class DisputeResolveView(APIView):
    permission_classes = [IsAuthenticated, IsPlatformStaff]
    throttle_scope = Scope.WRITE

    def post(self, request, pk: int):
        claim = get_object_or_404(TenancyClaim.all_objects, pk=pk)
        uphold = bool(request.data.get("uphold_claim", True))

        result = resolve_escalation(claim, resolved_by=request.user, uphold_claim=uphold)

        if isinstance(result, Tenancy):
            return Response(TenancySerializer(result).data, status=201)
        return Response(TenancyClaimSerializer(result).data)
