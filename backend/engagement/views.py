"""
Saved-property and inquiry endpoints (ADR-003, ADR-004 §1.1).

Both are student-facing writes that a landlord sees the results of, so both are
scoped by relationship rather than by role: you see your own saved list, and you
see the inquiries you sent or the ones about property you manage.

Neither is gated on verification. Saving a listing and asking a question are in
``NEVER_GATED`` — a student who cannot ask a landlord a question cannot work out
whether to apply, and gating that makes verification a precondition for *using*
the platform rather than for transacting on it.
"""

from __future__ import annotations

from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api.throttling import Scope
from config.api.views import SchemaSafeQuerysetMixin
from config.middleware import get_current_university
from properties.constants import PropertyStatus
from properties.models import Property, Unit

from .models import Inquiry, SavedProperty
from .serializers import (
    InquiryCreateSerializer,
    InquiryResponseSerializer,
    InquirySerializer,
    SavedPropertySerializer,
    SavePropertySerializer,
)
from .services import (
    close_inquiry,
    may_respond_to,
    respond_to_inquiry,
    save_property,
    send_inquiry,
    unsave_property,
)


def _tenant():
    university = get_current_university()
    if university is None:
        raise NotFound("No university is configured for this host.")
    return university


# ---------------------------------------------------------------------------
# Saved properties
# ---------------------------------------------------------------------------


@extend_schema_view(
    get=extend_schema(
        summary="Your saved properties",
        description=(
            "Only ever your own. There is no endpoint that lists another "
            "user's saved properties, and there will not be one -- what "
            "somebody is considering is not public."
        ),
    ),
    post=extend_schema(
        summary="Save a property",
        description=(
            "Idempotent: saving twice returns the existing row rather than "
            "erroring. A double tap on a phone is not a conflict."
        ),
        request=SavePropertySerializer,
    ),
)
class SavedPropertyListView(SchemaSafeQuerysetMixin, ListAPIView):
    """The caller's own saved list."""

    serializer_class = SavedPropertySerializer
    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.AUTHENTICATED_READ
    schema_queryset = SavedProperty.all_objects

    def get_queryset(self):
        if self.is_schema_generation():
            return self.empty_queryset()

        return (
            SavedProperty.objects.for_tenant(_tenant())
            .filter(user=self.request.user)
            .select_related("property_saved")
            .order_by("-created_at")
        )

    def post(self, request):
        serializer = SavePropertySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        prop = get_object_or_404(
            Property.objects.for_tenant(_tenant()).filter(status=PropertyStatus.PUBLISHED),
            slug=serializer.validated_data["property_slug"],
        )
        saved = save_property(request.user, prop, note=serializer.validated_data.get("note", ""))

        return Response(SavedPropertySerializer(saved).data, status=201)


@extend_schema_view(
    delete=extend_schema(
        responses={204: None},
        summary="Unsave a property",
        description="Idempotent: unsaving something not saved is a 204, not a 404.",
    )
)
class SavedPropertyDeleteView(APIView):
    """Remove one from the caller's saved list."""

    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.WRITE

    def delete(self, request, slug: str):
        prop = get_object_or_404(Property.objects.for_tenant(_tenant()), slug=slug)
        unsave_property(request.user, prop)
        return Response(status=204)


# ---------------------------------------------------------------------------
# Inquiries
# ---------------------------------------------------------------------------


@extend_schema_view(
    get=extend_schema(
        summary="Inquiries you sent, or that concern property you manage",
        description=(
            "One endpoint, scoped by relationship rather than by role: a "
            "student sees what they sent, a landlord or assigned caretaker "
            "sees what was sent about their property. Nobody sees anything "
            "else.\n\n"
            "**No contact details appear in either direction.** The "
            "conversation stays on-platform so that a resulting stay is one "
            "the platform witnessed -- an off-platform arrangement arrives "
            "later as a claim, which is a dispute surface and a queue entry "
            "the application path never creates (ADR-004 §1.1)."
        ),
    ),
    post=extend_schema(
        summary="Ask about a unit",
        description=(
            "Rate-limited per user and per unit. An inquiry is an unsolicited "
            "message to a stranger, so the limit is part of the feature.\n\n"
            "Phone numbers, email addresses and messaging handles are "
            "rejected. Invite them to apply instead."
        ),
        request=InquiryCreateSerializer,
    ),
)
class InquiryListView(SchemaSafeQuerysetMixin, ListAPIView):
    """Inquiries the caller is a party to."""

    serializer_class = InquirySerializer
    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.AUTHENTICATED_READ
    schema_queryset = Inquiry.all_objects

    def get_queryset(self):
        if self.is_schema_generation():
            return self.empty_queryset()

        user = self.request.user
        landlord_profile = getattr(user, "landlord_profile", None)

        # Sent by them, OR about a property they own, OR about a property they
        # hold a caretaker assignment on. One query, three relationships.
        visible = Q(sender=user)
        if landlord_profile is not None:
            visible |= Q(unit__property__landlord=landlord_profile)
        visible |= Q(unit__property__caretaker_assignments__user=user)

        return (
            Inquiry.objects.for_tenant(_tenant())
            .filter(visible)
            .select_related("unit__property", "sender", "responded_by")
            .distinct()
            .order_by("-created_at")
        )

    def post(self, request):
        serializer = InquiryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        unit = get_object_or_404(
            Unit.objects.for_tenant(_tenant()).filter(property__status=PropertyStatus.PUBLISHED),
            pk=serializer.validated_data["unit"],
        )
        inquiry = send_inquiry(
            unit=unit,
            sender=request.user,
            message=serializer.validated_data["message"],
            preferred_move_in_date=serializer.validated_data.get("preferred_move_in_date"),
        )

        return Response(InquirySerializer(inquiry).data, status=201)


@extend_schema_view(
    post=extend_schema(
        responses=InquirySerializer,
        summary="Answer an inquiry",
        description=(
            "The landlord always; an assigned caretaker only with the "
            "`respond_inquiries` permission (ADR-003).\n\n"
            "One response, and it closes the exchange. Deliberately not a "
            "thread: a thread is a messaging product, and a messaging product "
            "is where the conversation stops producing an application."
        ),
        request=InquiryResponseSerializer,
    )
)
class InquiryRespondView(APIView):
    """Answer one inquiry."""

    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.WRITE

    def post(self, request, pk: int):
        inquiry = get_object_or_404(
            Inquiry.objects.for_tenant(_tenant()).select_related("unit__property"), pk=pk
        )

        if not may_respond_to(request.user, inquiry):
            # 404 rather than 403: an inquiry the caller has no relationship to
            # is one whose existence they are not entitled to learn.
            raise NotFound("No such inquiry.")

        serializer = InquiryResponseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        answered = respond_to_inquiry(
            inquiry, responder=request.user, response=serializer.validated_data["response"]
        )

        return Response(InquirySerializer(answered).data)


@extend_schema_view(
    post=extend_schema(
        responses=InquirySerializer,
        summary="Close an inquiry without answering",
        description=(
            "Not a rejection: an inquiry is not an application, and there is "
            "nothing to reject. Either party may close their own exchange."
        ),
        request=None,
    )
)
class InquiryCloseView(APIView):
    """Close one inquiry."""

    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.WRITE

    def post(self, request, pk: int):
        inquiry = get_object_or_404(
            Inquiry.objects.for_tenant(_tenant()).select_related("unit__property"), pk=pk
        )

        if inquiry.sender_id != request.user.pk and not may_respond_to(request.user, inquiry):
            raise NotFound("No such inquiry.")

        return Response(InquirySerializer(close_inquiry(inquiry)).data)
