"""
Verification endpoints (ADR-003, Data Protection Act 2019).

Both student paths, and the reviewer queue over the second one.

Two things here carry more weight than the rest.

**The reviewer queue is tenant-scoped**, and that is the isolation failure with
the worst consequences in the product: on the other side of it are national ID
numbers belonging to people who never agreed to show them to another
institution. It is asserted at the API boundary, not only in the service layer,
because the boundary is what an attacker reaches.

**Every document read writes an access log row**, and that is asserted *of the
endpoint* rather than of the service function. A future view that forgets to
call through is the realistic failure — the service function will keep working
perfectly while the audit trail quietly stops recording.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config.api.contract import VERIFICATION_DECISION_REASON
from config.api.throttling import Scope
from config.api.views import SchemaSafeQuerysetMixin

from .documents import (
    AccessPurpose,
    VerificationRequest,
    approve_verification,
    reject_verification,
    signed_document_url,
    submit_verification_document,
)
from .permissions import IsUniversityStaffForTenant
from .verification import consume_email_token, issue_email_token

# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------


class EmailVerificationRequestSerializer(serializers.Serializer):
    """Asking for a verification link."""

    student_email = serializers.EmailField(
        help_text=(
            "An address at one of your university's configured student "
            "domains. Matched EXACTLY on the full domain -- a lookalike like "
            "`evil-kyu.ac.ke` is refused."
        )
    )


class EmailVerificationConfirmSerializer(serializers.Serializer):
    """Consuming the link."""

    token = serializers.CharField(
        help_text="The token from the emailed link. Single use, and expires."
    )


class EmailVerificationResultSerializer(serializers.Serializer):
    """What confirming a token tells the caller about themselves.

    Declared so the client's type is generated rather than hand-written. An
    undeclared response leaves the schema saying "no response body", and the
    frontend then owns a private copy of the contract -- which is how
    `Paginated<T>` drifted three fields behind the API without anything
    noticing.
    """

    verification_status = serializers.CharField(read_only=True)
    verification_method = serializers.CharField(read_only=True, allow_null=True)
    verified_at = serializers.DateTimeField(read_only=True, allow_null=True)


class VerificationRequestSerializer(serializers.ModelSerializer):
    """A document review request, as the student or the reviewer sees it.

    **No reviewer identity, in either direction.** Not a field, not a name, not
    an id.
    """

    student_name = serializers.SerializerMethodField()
    university_name = serializers.CharField(source="profile.university.name", read_only=True)
    document_available = serializers.SerializerMethodField(
        help_text=(
            "Whether the image still exists. False once retention has deleted "
            "it -- the DECISION is retained, the image is not (ADR-003)."
        )
    )

    class Meta:
        model = VerificationRequest
        fields = (
            "id",
            "student_name",
            "university_name",
            "status",
            "decision_reason",
            "reviewed_at",
            "attempt",
            "document_available",
            "created_at",
        )
        read_only_fields = fields
        extra_kwargs = {
            "decision_reason": {"help_text": VERIFICATION_DECISION_REASON},
            "status": {"help_text": "pending | approved | rejected."},
        }

    def get_student_name(self, request: VerificationRequest) -> str:
        from .privacy import display_name_for

        return display_name_for(request.profile.user)

    def get_document_available(self, request: VerificationRequest) -> bool:
        return request.document.is_available()


class VerificationDecisionSerializer(serializers.Serializer):
    """Approving or rejecting one."""

    reason = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=255,
        help_text=(
            "Shown to the student. **Required for a rejection** -- a student "
            "told 'no' with nothing to act on cannot resubmit successfully."
        ),
    )


# ---------------------------------------------------------------------------
# Email-domain verification
# ---------------------------------------------------------------------------


@extend_schema_view(
    post=extend_schema(
        summary="Request an email verification link",
        description=(
            "The automated path. No human reviews anything and no reviewer is "
            "recorded -- a domain decided it, not a person.\n\n"
            "**The response is deliberately identical whether or not the "
            "address is known**, and whether or not you are already verified. "
            "Anything else tells an attacker which students exist at a "
            "university.\n\n"
            "Rate-limited per user AND per address, independently: per user "
            "alone lets several accounts mail-bomb one address, per address "
            "alone lets one account grind through a university's namespace."
        ),
        request=EmailVerificationRequestSerializer,
        responses={202: None},
    )
)
class EmailVerificationRequestView(APIView):
    """Issue a token and mail it."""

    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.VERIFICATION

    def post(self, request):
        serializer = EmailVerificationRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        profile = getattr(request.user, "student_profile", None)
        if profile is None:
            raise NotFound("No student profile.")

        _token, raw = issue_email_token(profile, serializer.validated_data["student_email"])
        self.deliver(profile, serializer.validated_data["student_email"], raw)

        # 202 with no body, always. The caller learns that we accepted the
        # request, and nothing about the address.
        return Response(status=202)

    def deliver(self, profile, email: str, raw_token: str) -> None:
        """Send the link.

        Split out so the mail backend is one seam rather than being inlined
        into the view -- and so a test can assert the token never appears in a
        response body while still checking it was sent.
        """
        from django.core.mail import send_mail

        send_mail(
            subject=f"Verify your {profile.university.display_name or profile.university.name} student email",
            message=(
                "Confirm this address to earn your verified badge:\n\n"
                f"  token: {raw_token}\n\n"
                "If you did not ask for this, ignore it -- nothing changes."
            ),
            from_email=None,
            recipient_list=[email],
            fail_silently=True,
        )


@extend_schema_view(
    post=extend_schema(
        responses=EmailVerificationResultSerializer,
        summary="Confirm an email verification token",
        description=(
            "Single use, consumed atomically -- a replayed token loses the "
            "race rather than winning it.\n\n"
            "Unknown, expired and already-used tokens return one identical "
            "error. The three are different to us and identical to anyone "
            "probing."
        ),
        request=EmailVerificationConfirmSerializer,
    )
)
class EmailVerificationConfirmView(APIView):
    """Consume a token."""

    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.VERIFICATION

    def post(self, request):
        serializer = EmailVerificationConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        profile = consume_email_token(serializer.validated_data["token"])

        return Response(
            {
                "verification_status": profile.verification_status,
                "verification_method": profile.verification_method,
                "verified_at": profile.verified_at,
            }
        )


# ---------------------------------------------------------------------------
# Document verification
# ---------------------------------------------------------------------------


@extend_schema_view(
    post=extend_schema(
        responses={201: VerificationRequestSerializer},
        summary="Upload an identity document for review",
        description=(
            "For schools that issue no student addresses.\n\n"
            "The content type is sniffed from the **leading bytes** -- the "
            "declared header and the file extension are both "
            "attacker-controlled. JPEG, PNG, WebP and PDF only.\n\n"
            "**EXIF is stripped on ingest.** A photo of a student ID carries "
            "the GPS coordinates of wherever it was taken, which is usually "
            "where that student lives.\n\n"
            "The image is deleted 7 days after a decision, or 30 days after "
            "upload whether or not anyone reviewed it. The decision is "
            "retained; the image is not."
        ),
        request={
            "multipart/form-data": {
                "type": "object",
                "properties": {"document": {"type": "string", "format": "binary"}},
            }
        },
    )
)
class DocumentSubmitView(APIView):
    """Accept an identity document."""

    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.VERIFICATION

    def post(self, request):
        profile = getattr(request.user, "student_profile", None)
        if profile is None:
            raise NotFound("No student profile.")

        upload = request.data.get("document")
        if upload is None:
            raise serializers.ValidationError({"document": ["No file was uploaded."]})

        verification = submit_verification_document(profile, upload.read())

        return Response(VerificationRequestSerializer(verification).data, status=201)


@extend_schema_view(
    get=extend_schema(
        summary="The reviewer queue for your university",
        description=(
            "**Scoped to the reviewer's own university.** Staff at one school "
            "never see another's requests, and that is the isolation failure "
            "with the worst consequences in the product: the data behind it is "
            "national ID numbers belonging to people who never agreed to show "
            "them to another institution."
        ),
        parameters=[
            OpenApiParameter(
                name="status",
                required=False,
                type=str,
                enum=["pending", "approved", "rejected"],
            )
        ],
    )
)
class ReviewerQueueView(SchemaSafeQuerysetMixin, ListAPIView):
    """University staff see their own students' requests."""

    serializer_class = VerificationRequestSerializer
    permission_classes = [IsAuthenticated, IsUniversityStaffForTenant]
    throttle_scope = Scope.AUTHENTICATED_READ
    schema_queryset = VerificationRequest.all_objects

    def get_queryset(self):
        if self.is_schema_generation():
            return self.empty_queryset()

        # From the REVIEWER's own staff profile, never from the request host.
        # A host header is caller-supplied; a staff profile is granted.
        university = self.request.user.staff_profile.university

        queryset = (
            VerificationRequest.objects.for_tenant(university)
            .select_related("profile__user", "profile__university", "document")
            .order_by("created_at")
        )

        status = self.request.query_params.get("status")
        if status:
            queryset = queryset.filter(status=status)

        return queryset


class ReviewerActionView(APIView):
    """Base for the actions a reviewer takes on one request."""

    permission_classes = [IsAuthenticated, IsUniversityStaffForTenant]
    throttle_scope = Scope.WRITE

    def get_request_object(self, request, pk: int) -> VerificationRequest:
        """Resolve within the reviewer's own university, or 404.

        Scoped from the staff profile rather than the host, and a 404 rather
        than a 403: confirming that another university's request exists is
        itself a disclosure.
        """
        university = request.user.staff_profile.university
        return get_object_or_404(
            VerificationRequest.objects.for_tenant(university).select_related(
                "profile__user", "document"
            ),
            pk=pk,
        )


@extend_schema_view(
    get=extend_schema(
        summary="A short-lived signed URL for one document",
        description=(
            "Generated per request and **never stored** -- a stored URL is a "
            "permanent bearer capability that would outlive both the review "
            "and the reviewer's employment. Expiry is minutes.\n\n"
            "**Every call writes a `DocumentAccessLog` row before the URL is "
            "returned.** If the log write fails there is no URL: an unlogged "
            "read is worse than a blocked one."
        ),
        responses={200: {"type": "object", "properties": {"url": {"type": "string"}}}},
    )
)
class DocumentAccessView(ReviewerActionView):
    """Hand a reviewer a signed URL, and record that it happened."""

    throttle_scope = Scope.AUTHENTICATED_READ

    def get(self, request, pk: int):
        verification = self.get_request_object(request, pk)

        url = signed_document_url(
            verification.document,
            reviewer=request.user,
            purpose=AccessPurpose.REVIEW,
            request_id=getattr(request, "request_id", ""),
        )

        return Response({"url": url})


@extend_schema_view(
    post=extend_schema(
        responses=VerificationRequestSerializer,
        summary="Approve a verification request",
        description="The student earns the badge. The reviewer is recorded internally and never shown.",
        request=VerificationDecisionSerializer,
    )
)
class VerificationApproveView(ReviewerActionView):
    def post(self, request, pk: int):
        verification = self.get_request_object(request, pk)

        serializer = VerificationDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        decided = approve_verification(
            verification,
            reviewer=request.user,
            reason=serializer.validated_data.get("reason", ""),
        )

        return Response(VerificationRequestSerializer(decided).data)


@extend_schema_view(
    post=extend_schema(
        responses=VerificationRequestSerializer,
        summary="Reject a verification request",
        description=(
            "The reason is shown to the student and is required -- a blurry "
            "photo is the common case and they need to know that is what it "
            "was.\n\n"
            "**Not terminal.** They may resubmit, up to "
            "`VERIFICATION_MAX_SUBMISSIONS`. A dead end here would be an "
            "accessibility failure dressed as a security control."
        ),
        request=VerificationDecisionSerializer,
    )
)
class VerificationRejectView(ReviewerActionView):
    def post(self, request, pk: int):
        verification = self.get_request_object(request, pk)

        serializer = VerificationDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        decided = reject_verification(
            verification,
            reviewer=request.user,
            reason=serializer.validated_data.get("reason", ""),
        )

        return Response(VerificationRequestSerializer(decided).data)


@extend_schema_view(
    get=extend_schema(
        summary="Your own verification requests",
        description="Outcomes only. The document image is never returned here.",
    )
)
class MyVerificationRequestsView(SchemaSafeQuerysetMixin, ListAPIView):
    """A student's own history."""

    serializer_class = VerificationRequestSerializer
    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.AUTHENTICATED_READ
    schema_queryset = VerificationRequest.all_objects

    def get_queryset(self):
        if self.is_schema_generation():
            return self.empty_queryset()

        profile = getattr(self.request.user, "student_profile", None)
        if profile is None:
            return VerificationRequest.all_objects.none()

        return (
            VerificationRequest.all_objects.filter(profile=profile)
            .select_related("profile__user", "profile__university", "document")
            .order_by("-created_at")
        )
