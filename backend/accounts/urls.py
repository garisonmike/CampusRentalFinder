from django.urls import include, path

from config.api.routers import Router

from .privacy_api import DataExportView, ErasureCancelView, ErasureRequestView
from .verification_api import (
    DocumentAccessView,
    DocumentSubmitView,
    EmailVerificationConfirmView,
    EmailVerificationRequestView,
    MyVerificationRequestsView,
    ReviewerQueueView,
    VerificationApproveView,
    VerificationRejectView,
)
from .views import (
    AdminUserViewSet,
    PasswordChangeView,
    TokenRefreshView,
    TokenVerifyView,
    UserLoginView,
    UserLogoutView,
    UserProfileView,
    UserRegistrationView,
    current_user,
    user_statistics,
    verify_user,
)

router = Router()
router.register(r"admin/users", AdminUserViewSet, basename="admin-users")

app_name = "accounts"

urlpatterns = [
    # Authentication
    path("register/", UserRegistrationView.as_view(), name="register"),
    path("login/", UserLoginView.as_view(), name="login"),
    path("logout/", UserLogoutView.as_view(), name="logout"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("token/verify/", TokenVerifyView.as_view(), name="token_verify"),
    # The caller
    path("me/", current_user, name="current-user"),
    path("profile/", UserProfileView.as_view(), name="profile"),
    path("password/change/", PasswordChangeView.as_view(), name="password-change"),
    # Platform staff
    path("admin/verify/<int:user_id>/", verify_user, name="verify-user"),
    path("admin/statistics/", user_statistics, name="user-statistics"),
    # Verification (ADR-003)
    path(
        "verification/email/request/",
        EmailVerificationRequestView.as_view(),
        name="verify-email-request",
    ),
    path(
        "verification/email/confirm/",
        EmailVerificationConfirmView.as_view(),
        name="verify-email-confirm",
    ),
    path("verification/document/", DocumentSubmitView.as_view(), name="verify-document"),
    path("verification/mine/", MyVerificationRequestsView.as_view(), name="verify-mine"),
    path("verification/queue/", ReviewerQueueView.as_view(), name="verify-queue"),
    path(
        "verification/queue/<int:pk>/document/",
        DocumentAccessView.as_view(),
        name="verify-document-access",
    ),
    path(
        "verification/queue/<int:pk>/approve/",
        VerificationApproveView.as_view(),
        name="verify-approve",
    ),
    path(
        "verification/queue/<int:pk>/reject/",
        VerificationRejectView.as_view(),
        name="verify-reject",
    ),
    # Privacy (ADR-008)
    path("privacy/export/", DataExportView.as_view(), name="privacy-export"),
    path("privacy/erasure/", ErasureRequestView.as_view(), name="privacy-erasure"),
    path(
        "privacy/erasure/<int:pk>/cancel/",
        ErasureCancelView.as_view(),
        name="privacy-erasure-cancel",
    ),
    path("", include(router.urls)),
]
