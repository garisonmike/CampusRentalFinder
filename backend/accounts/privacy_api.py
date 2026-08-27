"""
Subject access and erasure endpoints (ADR-008, Data Protection Act 2019).

**Erasure is a request with a recorded decision, not an immediate delete.**

That is not caution for its own sake. Erasure is irreversible, refuses to run
twice, and tombstones an account permanently — so an accidental one cannot be
undone, and a *coerced* one is a real risk on a platform where a landlord has
leverage over a student who reviewed them badly. A recorded request with a
decision means there is a person and a timestamp between the button and the
tombstone.

Both endpoints confirm identity first, by password. A session token is enough
to browse; it is not enough to destroy an account or to export everything the
platform knows about somebody. A stolen phone should not be able to do either.
"""

from __future__ import annotations

import datetime as dt

from django.conf import settings
from django.contrib.auth import authenticate as authenticate_user
from django.core.exceptions import ValidationError
from django.db import models
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api.throttling import Scope

from .privacy import (
    export_personal_data,
    landlord_erasure_blockers,
)


class IdentityConfirmationSerializer(serializers.Serializer):
    """Re-authentication before anything irreversible or wholesale.

    A bearer token proves the session; it does not prove the person is still
    at the keyboard. For an export -- everything we hold about somebody, in one
    payload -- and for erasure, that difference matters.
    """

    password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
        help_text="Your current password. Required to confirm it is you.",
    )


class ErasureRequestSerializer(IdentityConfirmationSerializer):
    """Asking to be erased."""

    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=500,
        help_text="Optional. Recorded with the request; not required to grant it.",
    )
    confirm_understanding = serializers.BooleanField(
        help_text=(
            "You must pass true. Erasure is irreversible, and your reviews "
            "SURVIVE with your name replaced by a tombstone -- deleting them "
            "would let anyone remove criticism by deleting an account "
            "(ADR-008)."
        )
    )

    def validate_confirm_understanding(self, value: bool) -> bool:
        if not value:
            raise serializers.ValidationError(
                "Erasure is irreversible. Confirm you understand what is "
                "retained before it can proceed."
            )
        return value


class ErasureRequest(models.Model):
    """A recorded request to be erased, and what was decided.

    Not tenant-scoped: a subject may hold profiles at more than one
    relationship to the platform, and a regulator asking "did you honour this
    request" is not asking on behalf of a university.
    """

    class Status(models.TextChoices):
        #: Cancellable by the subject, for ERASURE_COOLING_OFF_DAYS.
        COOLING_OFF = "cooling_off", "Cooling off"
        COMPLETED = "completed", "Completed"
        BLOCKED = "blocked", "Blocked"
        CANCELLED = "cancelled", "Cancelled by the subject"

    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="erasure_requests"
    )
    reason = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.COOLING_OFF)
    #: When the scheduled job may execute this. The window exists so that a
    #: coerced or compromised request is visible to the real account owner
    #: before it becomes irreversible.
    executes_after = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    #: Why it could not complete. A landlord with running tenancies is a party
    #: to a contract other people are relying on (ADR-008 §2.2).
    blockers = models.JSONField(default=list, blank=True)

    requested_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Erasure request"
        verbose_name_plural = "Erasure requests"
        ordering = ["-requested_at"]
        indexes = [
            # The execution sweep reads the oldest due row. `executes_after` is
            # nullable -- null on a blocked or cancelled request -- so the job
            # filters on `__lte` rather than relying on ordering, which
            # excludes nulls wherever they would sort (docs/OPERATIONS.md).
            models.Index(fields=["status", "executes_after"], name="erasure_due_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(status="cooling_off") | models.Q(executes_after__isnull=False),
                name="erasure_cooling_off_has_a_deadline",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="cancelled") | models.Q(cancelled_at__isnull=False),
                name="erasure_cancelled_has_a_time",
            ),
        ]

    def __str__(self) -> str:
        return f"erasure request {self.pk} ({self.status})"


class ErasureRequestResultSerializer(serializers.ModelSerializer):
    """What the subject is told."""

    retained = serializers.SerializerMethodField(
        help_text="What survives erasure, and why. Shown before and after."
    )

    class Meta:
        model = ErasureRequest
        fields = (
            "id",
            "status",
            "blockers",
            "retained",
            "requested_at",
            "executes_after",
            "cancelled_at",
            "completed_at",
        )
        read_only_fields = fields

    def get_retained(self, _request) -> list[str]:
        return [
            "Reviews you wrote, with your name replaced by 'Former student'.",
            "The tenancy records those reviews rest on, which are also the "
            "landlord's records of who occupied their property.",
            "The record that a verification decision was made, without the "
            "document -- retention deleted that separately.",
            "The log of who accessed your verification document, "
            "pseudonymised: it can no longer say whose case it was.",
        ]


@extend_schema_view(
    post=extend_schema(
        responses={200: OpenApiTypes.OBJECT},
        summary="Export everything held about you",
        description=(
            "Kenya's Data Protection Act §26(a).\n\n"
            "Requires your password, not just a session: this is everything "
            "the platform knows about you in one payload, and a bearer token "
            "proves the session rather than the person.\n\n"
            "Two deliberate omissions. **Verification document images are "
            "never included** -- returning one would re-expose an identity "
            "document to whatever channel this travels over, and the decision "
            "is the record. **The reviewer's identity is never included** -- "
            "naming the member of staff who refused your ID, in a document "
            "handed to you, is how a policy decision becomes a personal one."
        ),
        request=IdentityConfirmationSerializer,
    )
)
class DataExportView(APIView):
    """Subject access."""

    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.PRIVACY

    def post(self, request):
        serializer = IdentityConfirmationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _confirm_identity(request, serializer.validated_data["password"])

        return Response(export_personal_data(request.user))


@extend_schema_view(
    post=extend_schema(
        responses={202: ErasureRequestResultSerializer, 409: ErasureRequestResultSerializer},
        summary="Request erasure of your personal data",
        description=(
            "Kenya's Data Protection Act §26(e).\n\n"
            "**Recorded as a request with a decision, not an immediate "
            "delete.** Erasure is irreversible and cannot run twice, so an "
            "accidental one cannot be undone -- and a coerced one is a real "
            "risk where a landlord has leverage over a student who reviewed "
            "them badly. The record puts a person and a timestamp between the "
            "button and the tombstone.\n\n"
            "**Your reviews survive**, with your name replaced by 'Former "
            "student'. Deleting them would make erasure a suppression tool: a "
            "landlord wanting a bad review gone would need one cooperating "
            "student and one support ticket. The right to be forgotten is not "
            "a right to unpublish what you said about someone else.\n\n"
            "A landlord with running or upcoming tenancies **cannot complete "
            "erasure** -- they are a party to a contract other people are "
            "relying on. The response says which, so it can be cleared."
        ),
        request=ErasureRequestSerializer,
    ),
    get=extend_schema(
        summary="Your erasure requests and their outcomes",
        responses=ErasureRequestResultSerializer(many=True),
    ),
)
class ErasureRequestView(APIView):
    """Erasure."""

    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.PRIVACY

    def get(self, request):
        requests = ErasureRequest.objects.filter(user=request.user)
        return Response(ErasureRequestResultSerializer(requests, many=True).data)

    def post(self, request):
        serializer = ErasureRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _confirm_identity(request, serializer.validated_data["password"])

        is_landlord = getattr(request.user, "landlord_profile", None) is not None
        blockers = landlord_erasure_blockers(request.user) if is_landlord else []

        if blockers:
            # Flagged, never silently partial. Erasing the safe fields and
            # leaving the rest is the worst outcome: the subject believes they
            # are erased, the platform believes it complied, and neither is
            # true. Recorded as blocked rather than entering cooling-off,
            # because a window that will end in a refusal is a week of false
            # reassurance.
            erasure = ErasureRequest.objects.create(
                user=request.user,
                reason=serializer.validated_data.get("reason", ""),
                status=ErasureRequest.Status.BLOCKED,
                blockers=blockers,
            )
            return Response(ErasureRequestResultSerializer(erasure).data, status=409)

        now = timezone.now()
        erasure = ErasureRequest.objects.create(
            user=request.user,
            reason=serializer.validated_data.get("reason", ""),
            status=ErasureRequest.Status.COOLING_OFF,
            executes_after=now + dt.timedelta(days=settings.ERASURE_COOLING_OFF_DAYS),
        )
        _notify_cooling_off(request.user, erasure)

        return Response(ErasureRequestResultSerializer(erasure).data, status=202)


@extend_schema_view(
    post=extend_schema(
        responses=ErasureRequestResultSerializer,
        summary="Cancel your erasure request",
        description=(
            "Only during the cooling-off window, and **only by the subject**. "
            "Nobody else may cancel it -- not support, not an administrator. "
            "The window exists to protect the account owner from a coerced or "
            "compromised request, and a third party who could cancel could "
            "also be leaned on.\n\n"
            "The account is **not suspended** while it cools off, for the same "
            "reason: a suspended account cannot cancel its own request."
        ),
        request=IdentityConfirmationSerializer,
    )
)
class ErasureCancelView(APIView):
    """Cancel a request inside its window."""

    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.PRIVACY

    def post(self, request, pk: int):
        serializer = IdentityConfirmationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        _confirm_identity(request, serializer.validated_data["password"])

        # Filtered by user, so another account's request is a 404 rather than
        # a 403: its existence is not theirs to learn.
        erasure = get_object_or_404(ErasureRequest, pk=pk, user=request.user)

        if erasure.status != ErasureRequest.Status.COOLING_OFF:
            raise ValidationError(
                {"status": f"This request is already {erasure.get_status_display().lower()}."}
            )

        erasure.status = ErasureRequest.Status.CANCELLED
        erasure.cancelled_at = timezone.now()
        erasure.executes_after = None
        erasure.save(update_fields=["status", "cancelled_at", "executes_after"])

        return Response(ErasureRequestResultSerializer(erasure).data)


def _notify_cooling_off(user, erasure) -> None:
    """Tell the account owner that an erasure is running.

    **The whole point of the window.** A coerced request, or one made from a
    stolen session, is invisible to the real owner unless something reaches
    them out of band. This is that thing.

    `fail_silently` because a bounced notification must not roll back the
    request -- the subject asked, and a mail failure is our problem rather
    than a reason to refuse them.
    """
    from django.core.mail import send_mail

    send_mail(
        subject="Your CampusRentalFinder account is scheduled for erasure",
        message=(
            "An erasure request was made on your account.\n\n"
            f"It becomes permanent on {erasure.executes_after:%d %B %Y}. "
            "Until then you can cancel it by signing in.\n\n"
            "If you did not make this request, sign in and cancel it now, "
            "then change your password."
        ),
        from_email=None,
        recipient_list=[user.email],
        fail_silently=True,
    )


def _confirm_identity(request, password: str) -> None:
    """Re-authenticate, or refuse.

    Deliberately a 403 rather than a 401: the session is valid, and returning
    401 would make a client discard a perfectly good token and log the user
    out over a mistyped password.
    """
    from rest_framework.exceptions import PermissionDenied

    if authenticate_user(username=request.user.email, password=password) is None:
        raise PermissionDenied("That password is not correct.")
