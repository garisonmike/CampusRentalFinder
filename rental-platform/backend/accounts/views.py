"""
Views for the accounts app.

This module contains views for user authentication, registration,
profile management, and admin operations.
"""

from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated, AllowAny, IsAdminUser
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView
from django.contrib.auth import update_session_auth_hash
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.openapi import OpenApiTypes

from .models import User, UserProfile
from .serializers import (
    UserRegistrationSerializer,
    UserLoginSerializer,
    UserSerializer,
    UserDetailSerializer,
    UserUpdateSerializer,
    PasswordChangeSerializer,
    UserProfileUpdateSerializer,
    AdminUserSerializer,
)


class UserRegistrationView(APIView):
    """
    User registration endpoint.
    
    Allows new users to register with different user types.
    """
    
    permission_classes = [AllowAny]
    
    @extend_schema(
        summary="Register new user",
        description="Create a new user account with email and password",
        request=UserRegistrationSerializer,
        responses={
            201: UserSerializer,
            400: "Bad Request - Validation errors"
        },
        tags=["Authentication"]
    )
    def post(self, request):
        """Register a new user."""
        serializer = UserRegistrationSerializer(data=request.data)
        
        if serializer.is_valid():
            user = serializer.save()
            
            # Generate JWT tokens
            refresh = RefreshToken.for_user(user)
            
            return Response({
                'message': _('User registered successfully'),
                'user': UserSerializer(user).data,
                'tokens': {
                    'access': str(refresh.access_token),
                    'refresh': str(refresh),
                }
            }, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UserLoginView(APIView):
    """
    User login endpoint.
    
    Authenticates user and returns JWT tokens.
    """
    
    permission_classes = [AllowAny]
    
    @extend_schema(
        summary="User login",
        description="Authenticate user and return JWT tokens",
        request=UserLoginSerializer,
        responses={
            200: UserSerializer,
            400: "Bad Request - Invalid credentials"
        },
        tags=["Authentication"]
    )
    def post(self, request):
        """Login user with email and password."""
        serializer = UserLoginSerializer(
            data=request.data,
            context={'request': request}
        )
        
        if serializer.is_valid():
            user = serializer.validated_data['user']
            
            # Generate JWT tokens
            refresh = RefreshToken.for_user(user)
            
            return Response({
                'message': _('Login successful'),
                'user': UserSerializer(user).data,
                'tokens': {
                    'access': str(refresh.access_token),
                    'refresh': str(refresh),
                }
            }, status=status.HTTP_200_OK)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UserLogoutView(APIView):
    """
    User logout endpoint.
    
    Blacklists the refresh token to log out user.
    """
    
    permission_classes = [IsAuthenticated]
    
    @extend_schema(
        summary="User logout",
        description="Logout user by blacklisting refresh token",
        request={
            'type': 'object',
            'properties': {
                'refresh': {'type': 'string', 'description': 'Refresh token'}
            },
            'required': ['refresh']
        },
        responses={
            200: "Logout successful",
            400: "Bad Request - Invalid token"
        },
        tags=["Authentication"]
    )
    def post(self, request):
        """Logout user by blacklisting refresh token."""
        try:
            refresh_token = request.data.get('refresh')
            if not refresh_token:
                return Response(
                    {'error': _('Refresh token is required')},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            token = RefreshToken(refresh_token)
            token.blacklist()
            
            return Response({
                'message': _('Logout successful')
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            return Response(
                {'error': _('Invalid token')},
                status=status.HTTP_400_BAD_REQUEST
            )


class UserProfileView(APIView):
    """
    User profile management endpoint.
    
    Allows users to view and update their profile information.
    """
    
    permission_classes = [IsAuthenticated]
    
    @extend_schema(
        summary="Get user profile",
        description="Retrieve current user's profile information",
        responses={200: UserDetailSerializer},
        tags=["User Profile"]
    )
    def get(self, request):
        """Get current user's profile."""
        serializer = UserDetailSerializer(request.user)
        return Response(serializer.data)
    
    @extend_schema(
        summary="Update user profile",
        description="Update current user's profile information",
        request=UserUpdateSerializer,
        responses={
            200: UserDetailSerializer,
            400: "Bad Request - Validation errors"
        },
        tags=["User Profile"]
    )
    def patch(self, request):
        """Update current user's profile."""
        serializer = UserUpdateSerializer(
            request.user,
            data=request.data,
            partial=True
        )
        
        if serializer.is_valid():
            serializer.save()
            
            # Return updated user data
            updated_user = UserDetailSerializer(request.user)
            
            return Response({
                'message': _('Profile updated successfully'),
                'user': updated_user.data
            })
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class PasswordChangeView(APIView):
    """
    Password change endpoint.
    
    Allows authenticated users to change their password.
    """
    
    permission_classes = [IsAuthenticated]
    
    @extend_schema(
        summary="Change password",
        description="Change current user's password",
        request=PasswordChangeSerializer,
        responses={
            200: "Password changed successfully",
            400: "Bad Request - Validation errors"
        },
        tags=["User Profile"]
    )
    def post(self, request):
        """Change user password."""
        serializer = PasswordChangeSerializer(
            data=request.data,
            context={'request': request}
        )
        
        if serializer.is_valid():
            user = serializer.save()
            
            # Update session hash to prevent logout
            update_session_auth_hash(request, user)
            
            return Response({
                'message': _('Password changed successfully')
            })
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UserProfilePreferencesView(APIView):
    """
    User profile preferences endpoint.
    
    Manages extended profile preferences and settings.
    """
    
    permission_classes = [IsAuthenticated]
    
    @extend_schema(
        summary="Get profile preferences",
        description="Retrieve user's profile preferences",
        responses={200: UserProfileUpdateSerializer},
        tags=["User Profile"]
    )
    def get(self, request):
        """Get user's profile preferences."""
        try:
            profile = request.user.extended_profile
            serializer = UserProfileUpdateSerializer(profile)
            return Response(serializer.data)
        except UserProfile.DoesNotExist:
            # Create profile if it doesn't exist
            profile = UserProfile.objects.create(user=request.user)
            serializer = UserProfileUpdateSerializer(profile)
            return Response(serializer.data)
    
    @extend_schema(
        summary="Update profile preferences",
        description="Update user's profile preferences",
        request=UserProfileUpdateSerializer,
        responses={
            200: UserProfileUpdateSerializer,
            400: "Bad Request - Validation errors"
        },
        tags=["User Profile"]
    )
    def patch(self, request):
        """Update user's profile preferences."""
        try:
            profile = request.user.extended_profile
        except UserProfile.DoesNotExist:
            profile = UserProfile.objects.create(user=request.user)
        
        serializer = UserProfileUpdateSerializer(
            profile,
            data=request.data,
            partial=True
        )
        
        if serializer.is_valid():
            serializer.save()
            return Response({
                'message': _('Preferences updated successfully'),
                'preferences': serializer.data
            })
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# Function-based views for simple operations
@extend_schema(
    summary="Get current user info",
    description="Get basic information about the current authenticated user",
    responses={200: UserSerializer},
    tags=["Authentication"]
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def current_user(request):
    """Get current user information."""
    serializer = UserSerializer(request.user)
    return Response(serializer.data)


@extend_schema(
    summary="Verify user account",
    description="Admin endpoint to verify/unverify user accounts",
    parameters=[
        OpenApiParameter(
            name='user_id',
            type=OpenApiTypes.INT,
            location=OpenApiParameter.PATH,
            description='User ID to verify'
        )
    ],
    responses={
        200: "User verification updated",
        404: "User not found",
        403: "Permission denied"
    },
    tags=["Admin"]
)
@api_view(['POST'])
@permission_classes([IsAdminUser])
def verify_user(request, user_id):
    """Admin endpoint to verify user accounts."""
    try:
        user = User.objects.get(id=user_id)
        user.is_verified = not user.is_verified
        if user.is_verified:
            from django.utils import timezone
            user.verification_date = timezone.now()
        else:
            user.verification_date = None
        user.save()
        
        return Response({
            'message': _('User verification updated successfully'),
            'user_id': user_id,
            'is_verified': user.is_verified
        })
    except User.DoesNotExist:
        return Response(
            {'error': _('User not found')},
            status=status.HTTP_404_NOT_FOUND
        )


# Admin ViewSet for user management
class AdminUserViewSet(ModelViewSet):
    """
    Admin viewset for managing users.
    
    Provides full CRUD operations for user management by admins.
    """
    
    queryset = User.objects.all().order_by('-date_joined')
    serializer_class = AdminUserSerializer
    permission_classes = [IsAdminUser]
    
    @extend_schema(
        summary="List all users",
        description="Get paginated list of all users (Admin only)",
        tags=["Admin"]
    )
    def list(self, request, *args, **kwargs):
        """List all users with pagination."""
        return super().list(request, *args, **kwargs)
    
    @extend_schema(
        summary="Get user details",
        description="Get detailed information about a specific user (Admin only)",
        tags=["Admin"]
    )
    def retrieve(self, request, *args, **kwargs):
        """Get specific user details."""
        return super().retrieve(request, *args, **kwargs)
    
    @extend_schema(
        summary="Update user",
        description="Update user information (Admin only)",
        tags=["Admin"]
    )
    def update(self, request, *args, **kwargs):
        """Update user information."""
        return super().update(request, *args, **kwargs)
    
    @extend_schema(
        summary="Delete user",
        description="Delete user account (Admin only)",
        tags=["Admin"]
    )
    def destroy(self, request, *args, **kwargs):
        """Delete user account."""
        return super().destroy(request, *args, **kwargs)
    
    @action(detail=True, methods=['post'])
    @extend_schema(
        summary="Toggle user verification",
        description="Toggle user verification status (Admin only)",
        tags=["Admin"]
    )
    def toggle_verification(self, request, pk=None):
        """Toggle user verification status."""
        user = self.get_object()
        user.is_verified = not user.is_verified
        if user.is_verified:
            from django.utils import timezone
            user.verification_date = timezone.now()
        else:
            user.verification_date = None
        user.save()
        
        return Response({
            'message': _('User verification toggled successfully'),
            'is_verified': user.is_verified
        })
    
    @action(detail=True, methods=['post'])
    @extend_schema(
        summary="Toggle user active status",
        description="Activate/deactivate user account (Admin only)",
        tags=["Admin"]
    )
    def toggle_active(self, request, pk=None):
        """Toggle user active status."""
        user = self.get_object()
        user.is_active = not user.is_active
        user.save()
        
        return Response({
            'message': _('User active status toggled successfully'),
            'is_active': user.is_active
        })


# Statistics and dashboard views
@extend_schema(
    summary="Get user statistics",
    description="Get user registration and activity statistics (Admin only)",
    responses={
        200: {
            'type': 'object',
            'properties': {
                'total_users': {'type': 'integer'},
                'active_users': {'type': 'integer'},
                'verified_users': {'type': 'integer'},
                'tenants': {'type': 'integer'},
                'landlords': {'type': 'integer'},
                'admins': {'type': 'integer'},
            }
        }
    },
    tags=["Admin"]
)
@api_view(['GET'])
@permission_classes([IsAdminUser])
def user_statistics(request):
    """Get user statistics for admin dashboard."""
    from django.db.models import Count, Q
    
    stats = User.objects.aggregate(
        total_users=Count('id'),
        active_users=Count('id', filter=Q(is_active=True)),
        verified_users=Count('id', filter=Q(is_verified=True)),
        tenants=Count('id', filter=Q(user_type='tenant')),
        landlords=Count('id', filter=Q(user_type='landlord')),
        admins=Count('id', filter=Q(user_type='admin')),
    )
    
    return Response(stats)