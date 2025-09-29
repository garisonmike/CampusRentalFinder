"""
Serializers for the accounts app.

This module contains serializers for user authentication, registration,
and profile management operations.
"""

from rest_framework import serializers
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
from .models import User, UserProfile


class UserRegistrationSerializer(serializers.ModelSerializer):
    """
    Serializer for user registration.
    
    Handles validation and creation of new user accounts with
    different user types (tenant, landlord, admin).
    """
    
    password = serializers.CharField(
        write_only=True,
        min_length=8,
        style={'input_type': 'password'},
        help_text=_("Password must be at least 8 characters long")
    )
    
    password_confirm = serializers.CharField(
        write_only=True,
        style={'input_type': 'password'},
        help_text=_("Confirm your password")
    )
    
    class Meta:
        model = User
        fields = [
            'email',
            'password',
            'password_confirm',
            'first_name',
            'last_name',
            'phone_number',
            'user_type',
            'date_of_birth',
            'address',
            'city',
            'state',
            'zip_code'
        ]
        extra_kwargs = {
            'first_name': {'required': True},
            'last_name': {'required': True},
            'email': {'required': True},
        }
    
    def validate_email(self, value):
        """Validate email address."""
        if User.objects.filter(email=value.lower()).exists():
            raise serializers.ValidationError(
                _("A user with this email already exists.")
            )
        return value.lower()
    
    def validate_password(self, value):
        """Validate password using Django's password validators."""
        try:
            validate_password(value)
        except ValidationError as e:
            raise serializers.ValidationError(e.messages)
        return value
    
    def validate(self, data):
        """Validate password confirmation."""
        if data.get('password') != data.get('password_confirm'):
            raise serializers.ValidationError({
                'password_confirm': _("Password confirmation doesn't match.")
            })
        return data
    
    def create(self, validated_data):
        """Create a new user with validated data."""
        # Remove password_confirm from validated_data
        validated_data.pop('password_confirm', None)
        
        # Extract password
        password = validated_data.pop('password')
        
        # Create user
        user = User.objects.create_user(
            username=validated_data['email'],  # Use email as username
            **validated_data
        )
        
        # Set password
        user.set_password(password)
        user.save()
        
        return user


class UserLoginSerializer(serializers.Serializer):
    """
    Serializer for user login.
    
    Validates user credentials and returns user object if valid.
    """
    
    email = serializers.EmailField(
        help_text=_("Enter your email address")
    )
    
    password = serializers.CharField(
        write_only=True,
        style={'input_type': 'password'},
        help_text=_("Enter your password")
    )
    
    def validate(self, data):
        """Validate user credentials."""
        email = data.get('email', '').lower()
        password = data.get('password')
        
        if email and password:
            # Try to authenticate user
            user = authenticate(
                request=self.context.get('request'),
                username=email,
                password=password
            )
            
            if user:
                if user.is_active:
                    data['user'] = user
                    return data
                else:
                    raise serializers.ValidationError(
                        _("User account is disabled.")
                    )
            else:
                raise serializers.ValidationError(
                    _("Invalid email or password.")
                )
        else:
            raise serializers.ValidationError(
                _("Must include email and password.")
            )


class UserSerializer(serializers.ModelSerializer):
    """
    Serializer for User model.
    
    Used for displaying user information and basic updates.
    """
    
    full_name = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = [
            'id',
            'email',
            'first_name',
            'last_name',
            'full_name',
            'display_name',
            'user_type',
            'phone_number',
            'profile_picture',
            'bio',
            'address',
            'city',
            'state',
            'zip_code',
            'is_verified',
            'date_joined',
        ]
        read_only_fields = [
            'id',
            'is_verified',
            'date_joined',
        ]
    
    def get_full_name(self, obj):
        """Get user's full name."""
        return obj.get_full_name()
    
    def get_display_name(self, obj):
        """Get user's display name."""
        return obj.get_display_name()


class UserProfileSerializer(serializers.ModelSerializer):
    """
    Serializer for UserProfile model.
    
    Handles extended profile information.
    """
    
    class Meta:
        model = UserProfile
        fields = [
            'preferred_contact_method',
            'email_notifications',
            'sms_notifications',
            'website',
            'linkedin',
            'business_name',
            'business_license',
        ]


class UserDetailSerializer(serializers.ModelSerializer):
    """
    Detailed serializer for User model including profile information.
    
    Used for user profile pages and detailed user information.
    """
    
    extended_profile = UserProfileSerializer(read_only=True)
    full_name = serializers.SerializerMethodField()
    display_name = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = [
            'id',
            'email',
            'first_name',
            'last_name',
            'full_name',
            'display_name',
            'user_type',
            'phone_number',
            'profile_picture',
            'bio',
            'date_of_birth',
            'address',
            'city',
            'state',
            'zip_code',
            'is_verified',
            'verification_date',
            'date_joined',
            'last_login',
            'extended_profile',
        ]
        read_only_fields = [
            'id',
            'is_verified',
            'verification_date',
            'date_joined',
            'last_login',
        ]
    
    def get_full_name(self, obj):
        """Get user's full name."""
        return obj.get_full_name()
    
    def get_display_name(self, obj):
        """Get user's display name."""
        return obj.get_display_name()


class UserUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for updating user information.
    
    Allows users to update their profile information.
    """
    
    class Meta:
        model = User
        fields = [
            'first_name',
            'last_name',
            'phone_number',
            'profile_picture',
            'bio',
            'date_of_birth',
            'address',
            'city',
            'state',
            'zip_code',
        ]
    
    def validate_phone_number(self, value):
        """Validate phone number format."""
        if value and not value.startswith('+'):
            # Add basic format validation
            if len(value.replace(' ', '').replace('-', '')) < 10:
                raise serializers.ValidationError(
                    _("Phone number must be at least 10 digits.")
                )
        return value


class PasswordChangeSerializer(serializers.Serializer):
    """
    Serializer for changing user password.
    
    Requires current password and validates new password.
    """
    
    current_password = serializers.CharField(
        write_only=True,
        style={'input_type': 'password'},
        help_text=_("Enter your current password")
    )
    
    new_password = serializers.CharField(
        write_only=True,
        min_length=8,
        style={'input_type': 'password'},
        help_text=_("Enter your new password")
    )
    
    new_password_confirm = serializers.CharField(
        write_only=True,
        style={'input_type': 'password'},
        help_text=_("Confirm your new password")
    )
    
    def validate_current_password(self, value):
        """Validate current password."""
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError(
                _("Current password is incorrect.")
            )
        return value
    
    def validate_new_password(self, value):
        """Validate new password."""
        try:
            validate_password(value, user=self.context['request'].user)
        except ValidationError as e:
            raise serializers.ValidationError(e.messages)
        return value
    
    def validate(self, data):
        """Validate password confirmation."""
        if data.get('new_password') != data.get('new_password_confirm'):
            raise serializers.ValidationError({
                'new_password_confirm': _("New password confirmation doesn't match.")
            })
        return data
    
    def save(self):
        """Update user password."""
        user = self.context['request'].user
        user.set_password(self.validated_data['new_password'])
        user.save()
        return user


class UserProfileUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for updating user profile preferences.
    """
    
    class Meta:
        model = UserProfile
        fields = [
            'preferred_contact_method',
            'email_notifications',
            'sms_notifications',
            'website',
            'linkedin',
            'business_name',
            'business_license',
        ]
    
    def validate_website(self, value):
        """Validate website URL."""
        if value and not (value.startswith('http://') or value.startswith('https://')):
            value = 'https://' + value
        return value
    
    def validate_linkedin(self, value):
        """Validate LinkedIn URL."""
        if value and 'linkedin.com' not in value.lower():
            raise serializers.ValidationError(
                _("Please enter a valid LinkedIn profile URL.")
            )
        return value


# Admin-only serializers
class AdminUserSerializer(serializers.ModelSerializer):
    """
    Admin serializer for managing users.
    
    Includes all fields and allows admin operations.
    """
    
    full_name = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = [
            'id',
            'email',
            'username',
            'first_name',
            'last_name',
            'full_name',
            'user_type',
            'phone_number',
            'is_active',
            'is_staff',
            'is_superuser',
            'is_verified',
            'verification_date',
            'date_joined',
            'last_login',
        ]
        read_only_fields = [
            'id',
            'date_joined',
            'last_login',
        ]
    
    def get_full_name(self, obj):
        """Get user's full name."""
        return obj.get_full_name()