"""
User models for the rental platform.

This module contains the custom User model that extends Django's AbstractUser
to support different user types (tenant, landlord, admin).
"""

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.core.validators import RegexValidator
from django.utils.translation import gettext_lazy as _


class User(AbstractUser):
    """
    Custom User model with additional fields for rental platform.
    
    Extends Django's AbstractUser to include:
    - User types (tenant, landlord, admin)
    - Phone number validation
    - Email as primary identifier
    """
    
    USER_TYPE_CHOICES = (
        ('tenant', _('Tenant')),
        ('landlord', _('Landlord')),
        ('admin', _('Admin')),
    )
    
    # Phone number validator - allows international formats
    phone_regex = RegexValidator(
        regex=r'^\+?1?\d{9,15}$',
        message=_("Phone number must be entered in the format: '+999999999'. Up to 15 digits allowed.")
    )
    
    # Override email field to make it unique and required
    email = models.EmailField(
        _('email address'),
        unique=True,
        error_messages={
            'unique': _("A user with that email already exists."),
        }
    )
    
    # Additional fields
    phone_number = models.CharField(
        _('phone number'),
        validators=[phone_regex],
        max_length=17,
        blank=True,
        help_text=_("Phone number in international format")
    )
    
    user_type = models.CharField(
        _('user type'),
        max_length=10,
        choices=USER_TYPE_CHOICES,
        default='tenant',
        help_text=_("Type of user account")
    )
    
    # Profile fields
    date_of_birth = models.DateField(
        _('date of birth'),
        null=True,
        blank=True
    )
    
    profile_picture = models.ImageField(
        _('profile picture'),
        upload_to='profile_pictures/',
        null=True,
        blank=True
    )
    
    bio = models.TextField(
        _('biography'),
        max_length=500,
        blank=True,
        help_text=_("Brief description about yourself")
    )
    
    # Address fields (optional for users)
    address = models.CharField(
        _('address'),
        max_length=255,
        blank=True
    )
    
    city = models.CharField(
        _('city'),
        max_length=100,
        blank=True
    )
    
    state = models.CharField(
        _('state/province'),
        max_length=100,
        blank=True
    )
    
    zip_code = models.CharField(
        _('zip code'),
        max_length=20,
        blank=True
    )
    
    # Verification status
    is_verified = models.BooleanField(
        _('verified'),
        default=False,
        help_text=_("Designates whether this user has been verified by admin.")
    )
    
    verification_date = models.DateTimeField(
        _('verification date'),
        null=True,
        blank=True
    )
    
    # Timestamps
    created_at = models.DateTimeField(
        _('created at'),
        auto_now_add=True
    )
    
    updated_at = models.DateTimeField(
        _('updated at'),
        auto_now=True
    )
    
    # Use email as the unique identifier for authentication
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username', 'first_name', 'last_name']
    
    class Meta:
        verbose_name = _('User')
        verbose_name_plural = _('Users')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['user_type']),
            models.Index(fields=['is_verified']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        """String representation of the user."""
        return f"{self.get_full_name()} ({self.get_user_type_display()})"
    
    def get_full_name(self):
        """Return the first_name plus the last_name, with a space in between."""
        full_name = f"{self.first_name} {self.last_name}".strip()
        return full_name if full_name else self.email
    
    def get_short_name(self):
        """Return the short name for the user."""
        return self.first_name or self.email.split('@')[0]
    
    @property
    def is_tenant(self):
        """Check if user is a tenant."""
        return self.user_type == 'tenant'
    
    @property
    def is_landlord(self):
        """Check if user is a landlord."""
        return self.user_type == 'landlord'
    
    @property
    def is_platform_admin(self):
        """Check if user is a platform admin."""
        return self.user_type == 'admin'
    
    def get_display_name(self):
        """Get appropriate display name for user."""
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        elif self.first_name:
            return self.first_name
        else:
            return self.email.split('@')[0].title()
    
    def save(self, *args, **kwargs):
        """Override save method to perform custom operations."""
        # Ensure email is lowercase
        if self.email:
            self.email = self.email.lower()
        
        # Set username to email if not provided
        if not self.username:
            self.username = self.email
        
        super().save(*args, **kwargs)


class UserProfile(models.Model):
    """
    Extended profile information for users.
    
    This model stores additional information that might be added later
    without modifying the main User model.
    """
    
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='extended_profile'
    )
    
    # Preferences
    preferred_contact_method = models.CharField(
        _('preferred contact method'),
        max_length=20,
        choices=[
            ('email', _('Email')),
            ('phone', _('Phone')),
            ('both', _('Both')),
        ],
        default='email'
    )
    
    # Notification preferences
    email_notifications = models.BooleanField(
        _('email notifications'),
        default=True,
        help_text=_("Receive notifications via email")
    )
    
    sms_notifications = models.BooleanField(
        _('SMS notifications'),
        default=False,
        help_text=_("Receive notifications via SMS")
    )
    
    # Social media links (optional)
    website = models.URLField(
        _('website'),
        blank=True,
        help_text=_("Personal or business website")
    )
    
    linkedin = models.URLField(
        _('LinkedIn profile'),
        blank=True
    )
    
    # For landlords - business information
    business_name = models.CharField(
        _('business name'),
        max_length=100,
        blank=True,
        help_text=_("Business or company name (for landlords)")
    )
    
    business_license = models.CharField(
        _('business license'),
        max_length=50,
        blank=True,
        help_text=_("Business license number")
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = _('User Profile')
        verbose_name_plural = _('User Profiles')
    
    def __str__(self):
        return f"Profile for {self.user.get_full_name()}"


# Signal to create UserProfile automatically
from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    """Create UserProfile when User is created."""
    if created:
        UserProfile.objects.create(user=instance)

@receiver(post_save, sender=User)
def save_user_profile(sender, instance, **kwargs):
    """Save UserProfile when User is saved."""
    if hasattr(instance, 'extended_profile'):
        instance.extended_profile.save()