from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import (
    TokenRefreshView,
    TokenVerifyView,
)

from .views import (
    UserRegistrationView,
    UserLoginView,
    UserLogoutView,
    UserProfileView,
    PasswordChangeView,
    UserProfilePreferencesView,
    current_user,
    verify_user,
    user_statistics,
    AdminUserViewSet,
)

# Router for admin viewset
router = DefaultRouter()
router.register(r'admin/users', AdminUserViewSet, basename='admin-users')

app_name = 'accounts'

urlpatterns = [
    # Authentication
    path('register/', UserRegistrationView.as_view(), name='register'),
    path('login/', UserLoginView.as_view(), name='login'),
    path('logout/', UserLogoutView.as_view(), name='logout'),

    # JWT token endpoints
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('token/verify/', TokenVerifyView.as_view(), name='token_verify'),

    # User profile
    path('profile/', UserProfileView.as_view(), name='profile'),
    path('profile/preferences/', UserProfilePreferencesView.as_view(), name='profile-preferences'),
    path('password/change/', PasswordChangeView.as_view(), name='password-change'),

    # Current user info
    path('me/', current_user, name='current-user'),

    # Admin endpoints
    path('admin/verify/<int:user_id>/', verify_user, name='verify-user'),
    path('admin/statistics/', user_statistics, name='user-statistics'),

    # Include router URLs (admin users CRUD)
    path('', include(router.urls)),
]
