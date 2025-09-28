"""
Rental models for the rental platform.

This module contains models for rental properties, images, and related functionality.
"""

from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils.translation import gettext_lazy as _
from django.urls import reverse
import uuid
import os


def rental_image_upload_path(instance, filename):
    """Generate upload path for rental images."""
    # Get file extension
    ext = filename.split('.')[-1]
    # Generate unique filename
    filename = f"{uuid.uuid4()}.{ext}"
    # Return path
    return os.path.join('rental_images', str(instance.rental.id), filename)


class Rental(models.Model):
    """
    Model representing a rental property.
    
    Contains all information about a rental property including
    location, pricing, and features.
    """
    
    PROPERTY_TYPES = [
        ('apartment', _('Apartment')),
        ('house', _('House')),
        ('condo', _('Condo')),
        ('townhouse', _('Townhouse')),
        ('studio', _('Studio')),
        ('room', _('Single Room')),
        ('other', _('Other')),
    ]
    
    RENTAL_STATUS = [
        ('available', _('Available')),
        ('rented', _('Rented')),
        ('pending', _('Pending')),
        ('maintenance', _('Under Maintenance')),
        ('inactive', _('Inactive')),
    ]
    
    FURNISHING_STATUS = [
        ('furnished', _('Fully Furnished')),
        ('semi_furnished', _('Semi Furnished')),
        ('unfurnished', _('Unfurnished')),
    ]
    
    # Basic Information
    title = models.CharField(
        _('title'),
        max_length=200,
        help_text=_("Attractive title for the rental property")
    )
    
    description = models.TextField(
        _('description'),
        help_text=_("Detailed description of the property")
    )
    
    property_type = models.CharField(
        _('property type'),
        max_length=20,
        choices=PROPERTY_TYPES,
        default='apartment'
    )
    
    # Owner Information
    landlord = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='rental_properties',
        limit_choices_to={'user_type': 'landlord'},
        help_text=_("Property owner/landlord")
    )
    
    # Pricing
    price = models.DecimalField(
        _('monthly rent'),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text=_("Monthly rent amount")
    )
    
    security_deposit = models.DecimalField(
        _('security deposit'),
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        blank=True,
        null=True,
        help_text=_("Security deposit amount")
    )
    
    utilities_included = models.BooleanField(
        _('utilities included'),
        default=False,
        help_text=_("Whether utilities (water, electricity, etc.) are included in rent")
    )
    
    # Location
    address = models.CharField(
        _('address'),
        max_length=255,
        help_text=_("Street address of the property")
    )
    
    city = models.CharField(
        _('city'),
        max_length=100
    )
    
    state = models.CharField(
        _('state/province'),
        max_length=100
    )
    
    zip_code = models.CharField(
        _('zip code'),
        max_length=20
    )
    
    country = models.CharField(
        _('country'),
        max_length=100,
        default='United States'
    )
    
    # GPS Coordinates (for Google Maps)
    latitude = models.FloatField(
        _('latitude'),
        null=True,
        blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
        help_text=_("GPS latitude coordinate")
    )
    
    longitude = models.FloatField(
        _('longitude'),
        null=True,
        blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
        help_text=_("GPS longitude coordinate")
    )
    
    # Property Details
    bedrooms = models.PositiveIntegerField(
        _('bedrooms'),
        validators=[MinValueValidator(0), MaxValueValidator(20)],
        help_text=_("Number of bedrooms")
    )
    
    bathrooms = models.PositiveIntegerField(
        _('bathrooms'),
        validators=[MinValueValidator(1), MaxValueValidator(20)],
        help_text=_("Number of bathrooms")
    )
    
    square_footage = models.PositiveIntegerField(
        _('square footage'),
        null=True,
        blank=True,
        validators=[MinValueValidator(100)],
        help_text=_("Total area in square feet")
    )
    
    furnishing_status = models.CharField(
        _('furnishing status'),
        max_length=20,
        choices=FURNISHING_STATUS,
        default='unfurnished'
    )
    
    # Features and Amenities
    parking_available = models.BooleanField(
        _('parking available'),
        default=False
    )
    
    parking_spots = models.PositiveIntegerField(
        _('parking spots'),
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(10)]
    )
    
    pets_allowed = models.BooleanField(
        _('pets allowed'),
        default=False
    )
    
    smoking_allowed = models.BooleanField(
        _('smoking allowed'),
        default=False
    )
    
    laundry_available = models.BooleanField(
        _('laundry available'),
        default=False
    )
    
    internet_included = models.BooleanField(
        _('internet included'),
        default=False
    )
    
    gym_access = models.BooleanField(
        _('gym access'),
        default=False
    )
    
    pool_access = models.BooleanField(
        _('pool access'),
        default=False
    )
    
    # Availability
    available_from = models.DateField(
        _('available from'),
        help_text=_("Date when the property becomes available")
    )
    
    lease_duration_min = models.PositiveIntegerField(
        _('minimum lease duration (months)'),
        default=12,
        validators=[MinValueValidator(1), MaxValueValidator(60)],
        help_text=_("Minimum lease duration in months")
    )
    
    lease_duration_max = models.PositiveIntegerField(
        _('maximum lease duration (months)'),
        null=True,
        blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(60)],
        help_text=_("Maximum lease duration in months (leave blank for no limit)")
    )
    
    # Status
    status = models.CharField(
        _('rental status'),
        max_length=20,
        choices=RENTAL_STATUS,
        default='available'
    )
    
    is_featured = models.BooleanField(
        _('featured listing'),
        default=False,
        help_text=_("Whether this listing should be featured prominently")
    )
    
    # Contact Information
    contact_phone = models.CharField(
        _('contact phone'),
        max_length=17,
        blank=True,
        help_text=_("Phone number for inquiries")
    )
    
    contact_email = models.EmailField(
        _('contact email'),
        blank=True,
        help_text=_("Email for inquiries (defaults to landlord email)")
    )
    
    # University-specific fields
    distance_to_campus = models.FloatField(
        _('distance to campus (miles)'),
        null=True,
        blank=True,
        validators=[MinValueValidator(0)],
        help_text=_("Distance to main university campus in miles")
    )
    
    shuttle_service = models.BooleanField(
        _('shuttle service to campus'),
        default=False
    )
    
    # Metadata
    views_count = models.PositiveIntegerField(
        _('views count'),
        default=0,
        help_text=_("Number of times this listing has been viewed")
    )
    
    created_at = models.DateTimeField(
        _('created at'),
        auto_now_add=True
    )
    
    updated_at = models.DateTimeField(
        _('updated at'),
        auto_now=True
    )
    
    class Meta:
        verbose_name = _('Rental Property')
        verbose_name_plural = _('Rental Properties')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'available_from']),
            models.Index(fields=['city', 'state']),
            models.Index(fields=['price']),
            models.Index(fields=['bedrooms', 'bathrooms']),
            models.Index(fields=['property_type']),
            models.Index(fields=['landlord']),
            models.Index(fields=['is_featured']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        """String representation of the rental."""
        return f"{self.title} - ${self.price}/month"
    
    def get_absolute_url(self):
        """Get the absolute URL for this rental."""
        return reverse('rentals:detail', kwargs={'pk': self.pk})
    
    @property
    def full_address(self):
        """Get the complete address."""
        return f"{self.address}, {self.city}, {self.state} {self.zip_code}"
    
    @property
    def average_rating(self):
        """Calculate average rating from reviews."""
        reviews = self.reviews.all()
        if reviews.exists():
            return reviews.aggregate(
                avg_rating=models.Avg('rating')
            )['avg_rating']
        return 0
    
    @property
    def review_count(self):
        """Get total number of reviews."""
        return self.reviews.count()
    
    @property
    def is_available(self):
        """Check if property is currently available."""
        from datetime import date
        return (
            self.status == 'available' and 
            self.available_from <= date.today()
        )
    
    def increment_views(self):
        """Increment the views count."""
        self.views_count = models.F('views_count') + 1
        self.save(update_fields=['views_count'])
    
    def get_contact_email(self):
        """Get contact email, fallback to landlord email."""
        return self.contact_email or self.landlord.email
    
    def get_contact_phone(self):
        """Get contact phone, fallback to landlord phone."""
        return self.contact_phone or self.landlord.phone_number
    
    def save(self, *args, **kwargs):
        """Override save method for custom validation."""
        # Validate lease duration
        if self.lease_duration_max and self.lease_duration_max < self.lease_duration_min:
            raise ValueError(_("Maximum lease duration cannot be less than minimum"))
        
        # Set default contact info if not provided
        if not self.contact_email:
            self.contact_email = self.landlord.email
        
        super().save(*args, **kwargs)


class RentalImage(models.Model):
    """
    Model for storing rental property images.
    
    Multiple images can be associated with each rental property.
    """
    
    rental = models.ForeignKey(
        Rental,
        on_delete=models.CASCADE,
        related_name='images'
    )
    
    image = models.ImageField(
        _('image'),
        upload_to=rental_image_upload_path,
        help_text=_("Property image")
    )
    
    caption = models.CharField(
        _('caption'),
        max_length=200,
        blank=True,
        help_text=_("Optional caption for the image")
    )
    
    is_primary = models.BooleanField(
        _('primary image'),
        default=False,
        help_text=_("Whether this is the main image for the property")
    )
    
    order = models.PositiveIntegerField(
        _('display order'),
        default=0,
        help_text=_("Order in which images should be displayed")
    )
    
    uploaded_at = models.DateTimeField(
        _('uploaded at'),
        auto_now_add=True
    )
    
    class Meta:
        verbose_name = _('Rental Image')
        verbose_name_plural = _('Rental Images')
        ordering = ['order', 'uploaded_at']
        indexes = [
            models.Index(fields=['rental', 'is_primary']),
            models.Index(fields=['rental', 'order']),
        ]
    
    def __str__(self):
        """String representation of the image."""
        return f"Image for {self.rental.title}"
    
    def save(self, *args, **kwargs):
        """Override save method to ensure only one primary image."""
        if self.is_primary:
            # Set other images for this rental as not primary
            RentalImage.objects.filter(
                rental=self.rental,
                is_primary=True
            ).exclude(pk=self.pk).update(is_primary=False)
        
        super().save(*args, **kwargs)


class RentalFavorite(models.Model):
    """
    Model for users to save favorite rental properties.
    """
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='favorite_rentals'
    )
    
    rental = models.ForeignKey(
        Rental,
        on_delete=models.CASCADE,
        related_name='favorited_by'
    )
    
    created_at = models.DateTimeField(
        _('favorited at'),
        auto_now_add=True
    )
    
    class Meta:
        verbose_name = _('Rental Favorite')
        verbose_name_plural = _('Rental Favorites')
        unique_together = ['user', 'rental']
        indexes = [
            models.Index(fields=['user']),
            models.Index(fields=['rental']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        """String representation of the favorite."""
        return f"{self.user.get_full_name()} favorited {self.rental.title}"


class RentalInquiry(models.Model):
    """
    Model for rental inquiries from potential tenants.
    """
    
    INQUIRY_STATUS = [
        ('new', _('New')),
        ('read', _('Read')),
        ('replied', _('Replied')),
        ('closed', _('Closed')),
    ]
    
    rental = models.ForeignKey(
        Rental,
        on_delete=models.CASCADE,
        related_name='inquiries'
    )
    
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='rental_inquiries',
        limit_choices_to={'user_type': 'tenant'}
    )
    
    message = models.TextField(
        _('message'),
        help_text=_("Inquiry message from tenant")
    )
    
    contact_phone = models.CharField(
        _('contact phone'),
        max_length=17,
        blank=True
    )
    
    preferred_move_date = models.DateField(
        _('preferred move-in date'),
        null=True,
        blank=True
    )
    
    status = models.CharField(
        _('status'),
        max_length=10,
        choices=INQUIRY_STATUS,
        default='new'
    )
    
    landlord_reply = models.TextField(
        _('landlord reply'),
        blank=True
    )
    
    replied_at = models.DateTimeField(
        _('replied at'),
        null=True,
        blank=True
    )
    
    created_at = models.DateTimeField(
        _('created at'),
        auto_now_add=True
    )
    
    class Meta:
        verbose_name = _('Rental Inquiry')
        verbose_name_plural = _('Rental Inquiries')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['rental']),
            models.Index(fields=['tenant']),
            models.Index(fields=['status']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        """String representation of the inquiry."""
        return f"Inquiry from {self.tenant.get_full_name()} for {self.rental.title}"