from django.urls import include, path

from config.api.routers import Router

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
    path("", include(router.urls)),
]
