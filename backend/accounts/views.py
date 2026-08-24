"""Views for the accounts app (ADR-003)."""

from __future__ import annotations

from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.permissions import IsPlatformStaff
from universities.constants import VerificationStatus

from .models import User
from .serializers import (
    AdminUserSerializer,
    PasswordChangeSerializer,
    UserLoginSerializer,
    UserRegistrationSerializer,
    UserSerializer,
    UserUpdateSerializer,
)


def issue_tokens(user: User) -> dict[str, str]:
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


class UserRegistrationView(APIView):
    """Create an account and return tokens."""

    permission_classes = [AllowAny]
    serializer_class = UserRegistrationSerializer

    @extend_schema(
        summary="Register",
        request=UserRegistrationSerializer,
        responses={201: UserSerializer},
        tags=["Authentication"],
    )
    def post(self, request: Request) -> Response:
        serializer = UserRegistrationSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.save()

        return Response(
            {
                "message": _("User registered successfully"),
                "user": UserSerializer(user).data,
                "tokens": issue_tokens(user),
            },
            status=status.HTTP_201_CREATED,
        )


class UserLoginView(APIView):
    permission_classes = [AllowAny]
    serializer_class = UserLoginSerializer

    @extend_schema(
        summary="Log in",
        request=UserLoginSerializer,
        responses={200: UserSerializer},
        tags=["Authentication"],
    )
    def post(self, request: Request) -> Response:
        serializer = UserLoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data["user"]

        return Response(
            {
                "message": _("Login successful"),
                "user": UserSerializer(user).data,
                "tokens": issue_tokens(user),
            }
        )


class UserLogoutView(APIView):
    """Blacklist the refresh token."""

    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Log out", request=None, responses={200: None}, tags=["Authentication"])
    def post(self, request: Request) -> Response:
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return Response(
                {"error": _("Refresh token is required")}, status=status.HTTP_400_BAD_REQUEST
            )

        try:
            RefreshToken(refresh_token).blacklist()
        except Exception:
            # Narrow enough: the only work above is parsing and blacklisting a
            # token. The draft used a bare except around a much larger block,
            # which hid a broken logout for months (docs/AUDIT.md §4.5).
            return Response({"error": _("Invalid token")}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"message": _("Logout successful")})


class UserProfileView(APIView):
    """The caller's own identity."""

    permission_classes = [IsAuthenticated]
    serializer_class = UserSerializer

    @extend_schema(summary="Get profile", responses={200: UserSerializer}, tags=["User Profile"])
    def get(self, request: Request) -> Response:
        return Response(UserSerializer(request.user).data)

    @extend_schema(
        summary="Update profile",
        request=UserUpdateSerializer,
        responses={200: UserSerializer},
        tags=["User Profile"],
    )
    def patch(self, request: Request) -> Response:
        serializer = UserUpdateSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {
                "message": _("Profile updated successfully"),
                "user": UserSerializer(request.user).data,
            }
        )

    @extend_schema(
        summary="Replace profile",
        request=UserUpdateSerializer,
        responses={200: UserSerializer},
        tags=["User Profile"],
    )
    def put(self, request: Request) -> Response:
        """PUT is supported as well as PATCH.

        The previous frontend called PUT here and got a 405 on every profile
        save, because the draft implemented only GET and PATCH
        (docs/AUDIT.md §5).
        """
        serializer = UserUpdateSerializer(request.user, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(
            {
                "message": _("Profile updated successfully"),
                "user": UserSerializer(request.user).data,
            }
        )


class PasswordChangeView(APIView):
    permission_classes = [IsAuthenticated]
    serializer_class = PasswordChangeSerializer

    @extend_schema(
        summary="Change password",
        request=PasswordChangeSerializer,
        responses={200: None},
        tags=["User Profile"],
    )
    def post(self, request: Request) -> Response:
        serializer = PasswordChangeSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"message": _("Password changed successfully")})


@extend_schema(
    summary="Current user",
    description="Identity and the caller's capability set (ADR-003).",
    responses={200: UserSerializer},
    tags=["Authentication"],
)
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def current_user(request: Request) -> Response:
    """The caller, with capabilities.

    The client reads authorization from ``capabilities`` and never derives it
    from model shapes.
    """
    user = User.objects.select_related(
        "landlord_profile", "student_profile__university", "staff_profile__university"
    ).get(pk=request.user.pk)  # type: ignore[misc]

    return Response(UserSerializer(user).data)


# ---------------------------------------------------------------------------
# Platform staff
# ---------------------------------------------------------------------------


@extend_schema(
    summary="Verify a landlord",
    request=None,
    responses={200: None, 404: None},
    tags=["Admin"],
)
@api_view(["POST"])
@permission_classes([IsPlatformStaff])
def verify_user(request: Request, user_id: int) -> Response:
    """Mark a landlord profile verified.

    Landlord verification is a platform-staff action. Student verification is
    a different flow entirely, run per-university (ADR-003).
    """
    user = get_object_or_404(User, id=user_id)
    profile = getattr(user, "landlord_profile", None)

    if profile is None:
        return Response(
            {"error": _("This user has no landlord profile to verify.")},
            status=status.HTTP_400_BAD_REQUEST,
        )

    profile.verification_status = VerificationStatus.VERIFIED
    profile.verified_at = timezone.now()
    profile.verified_by = request.user
    profile.save(update_fields=["verification_status", "verified_at", "verified_by"])

    return Response({"message": _("Landlord verified"), "user": UserSerializer(user).data})


class AdminUserViewSet(ModelViewSet):
    """User administration for platform staff."""

    queryset = User.objects.all().order_by("-date_joined")
    serializer_class = AdminUserSerializer
    permission_classes = [IsPlatformStaff]
    search_fields = ["email", "first_name", "last_name", "phone_number"]
    ordering_fields = ["date_joined", "email"]

    @extend_schema(summary="Toggle active", request=None, responses={200: None}, tags=["Admin"])
    @action(detail=True, methods=["post"])
    def toggle_active(self, request: Request, pk: str | None = None) -> Response:
        user = self.get_object()
        user.is_active = not user.is_active
        user.save(update_fields=["is_active"])
        return Response({"message": _("Status updated"), "is_active": user.is_active})


@extend_schema(
    summary="User statistics",
    responses={200: None},
    tags=["Admin"],
)
@api_view(["GET"])
@permission_classes([IsPlatformStaff])
def user_statistics(request: Request) -> Response:
    """Counts by capability rather than by a self-declared string."""
    stats = User.objects.aggregate(
        total_users=Count("id"),
        active_users=Count("id", filter=Q(is_active=True)),
        students=Count("id", filter=Q(student_profile__isnull=False)),
        landlords=Count("id", filter=Q(landlord_profile__isnull=False)),
        university_staff=Count("id", filter=Q(staff_profile__isnull=False)),
        platform_staff=Count("id", filter=Q(is_staff=True)),
        verified_students=Count(
            "id", filter=Q(student_profile__verification_status=VerificationStatus.VERIFIED)
        ),
    )
    return Response(stats)
