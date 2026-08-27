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

from django.contrib.auth import authenticate as authenticate_user
from django.db import models
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api.throttling import Scope

from .privacy import (
    erase_landlord_data,
    erase_personal_data,
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
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        BLOCKED = "blocked", "Blocked"

    user = models.ForeignKey(
        "accounts.User", on_delete=models.CASCADE, related_name="erasure_requests"
    )
    reason = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    #: Why it could not complete. A landlord with running tenancies is a party
    #: to a contract other people are relying on (ADR-008 §2.2).
    blockers = models.JSONField(default=list, blank=True)

    requested_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Erasure request"
        verbose_name_plural = "Erasure requests"
        ordering = ["-requested_at"]

    def __str__(self) -> str:
        return f"erasure request {self.pk} ({self.status})"


class ErasureRequestResultSerializer(serializers.ModelSerializer):
    """What the subject is told."""

    retained = serializers.SerializerMethodField(
        help_text="What survives erasure, and why. Shown before and after."
    )

    class Meta:
        model = ErasureRequest
        fields = ("id", "status", "blockers", "retained", "requested_at", "completed_at")
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
    get=extend_schema(summary="Your erasure requests and their outcomes"),
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

        erasure = ErasureRequest.objects.create(
            user=request.user, reason=serializer.validated_data.get("reason", "")
        )

        is_landlord = getattr(request.user, "landlord_profile", None) is not None
        blockers = landlord_erasure_blockers(request.user) if is_landlord else []

        if blockers:
            # Flagged, never silently partial. Erasing the safe fields and
            # leaving the rest is the worst outcome: the subject believes they
            # are erased, the platform believes it complied, and neither is
            # true.
            erasure.status = ErasureRequest.Status.BLOCKED
            erasure.blockers = blockers
            erasure.save(update_fields=["status", "blockers"])
            return Response(ErasureRequestResultSerializer(erasure).data, status=409)

        if is_landlord:
            erase_landlord_data(request.user)
        else:
            erase_personal_data(request.user)

        erasure.status = ErasureRequest.Status.COMPLETED
        erasure.completed_at = timezone.now()
        erasure.save(update_fields=["status", "completed_at"])

        return Response(ErasureRequestResultSerializer(erasure).data, status=200)


def _confirm_identity(request, password: str) -> None:
    """Re-authenticate, or refuse.

    Deliberately a 403 rather than a 401: the session is valid, and returning
    401 would make a client discard a perfectly good token and log the user
    out over a mistyped password.
    """
    from rest_framework.exceptions import PermissionDenied

    if authenticate_user(username=request.user.email, password=password) is None:
        raise PermissionDenied("That password is not correct.")
