"""
Review models for the rental platform.

This module contains models for rental property reviews and ratings.
"""

from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError


class Review(models.Model):
    """
    Model representing a review for a rental property.
    
    Contains rating, comment, and metadata for reviews left by tenants.
    """
    
    RATING_CHOICES = [
        (1, _('1 - Poor')),
        (2, _('2 - Fair')),
        (3, _('3 - Good')),
        (4, _('4 - Very Good')),
        (5, _('5 - Excellent')),
    ]
    
    # Core fields
    rental = models.ForeignKey(
        'rentals.Rental',
        on_delete=models.CASCADE,
        related_name='reviews',
        help_text=_("The rental property being reviewed")
    )
    
    tenant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reviews',
        limit_choices_to={'user_type': 'tenant'},
        help_text=_("The tenant who wrote this review")
    )
    
    # Rating and review content
    rating = models.PositiveIntegerField(
        _('rating'),
        choices=RATING_CHOICES,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        help_text=_("Overall rating from 1-5 stars")
    )
    
    comment = models.TextField(
        _('review comment'),
        max_length=1000,
        help_text=_("Detailed review comment")
    )
    
    # Specific rating categories (optional but helpful)
    cleanliness_rating = models.PositiveIntegerField(
        _('cleanliness rating'),
        choices=RATING_CHOICES,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        null=True,
        blank=True,
        help_text=_("Rating for property cleanliness")
    )
    
    location_rating = models.PositiveIntegerField(
        _('location rating'),
        choices=RATING_CHOICES,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        null=True,
        blank=True,
        help_text=_("Rating for property location")
    )
    
    value_rating = models.PositiveIntegerField(
        _('value for money rating'),
        choices=RATING_CHOICES,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        null=True,
        blank=True,
        help_text=_("Rating for value for money")
    )
    
    landlord_rating = models.PositiveIntegerField(
        _('landlord rating'),
        choices=RATING_CHOICES,
        validators=[MinValueValidator(1), MaxValueValidator(5)],
        null=True,
        blank=True,
        help_text=_("Rating for landlord responsiveness and helpfulness")
    )
    
    # Review metadata
    title = models.CharField(
        _('review title'),
        max_length=200,
        blank=True,
        help_text=_("Optional short title for the review")
    )
    
    pros = models.TextField(
        _('pros'),
        max_length=500,
        blank=True,
        help_text=_("What the reviewer liked about the property")
    )
    
    cons = models.TextField(
        _('cons'),
        max_length=500,
        blank=True,
        help_text=_("What the reviewer didn't like about the property")
    )
    
    # Stay information
    move_in_date = models.DateField(
        _('move-in date'),
        null=True,
        blank=True,
        help_text=_("When the tenant moved into the property")
    )
    
    move_out_date = models.DateField(
        _('move-out date'),
        null=True,
        blank=True,
        help_text=_("When the tenant moved out of the property")
    )
    
    # Recommendation
    would_recommend = models.BooleanField(
        _('would recommend'),
        null=True,
        blank=True,
        help_text=_("Whether the tenant would recommend this property")
    )
    
    # Verification and moderation
    is_verified = models.BooleanField(
        _('verified review'),
        default=False,
        help_text=_("Whether this review has been verified by admin")
    )
    
    is_approved = models.BooleanField(
        _('approved'),
        default=True,
        help_text=_("Whether this review is approved for display")
    )
    
    moderation_notes = models.TextField(
        _('moderation notes'),
        blank=True,
        help_text=_("Internal notes for moderators")
    )
    
    # Landlord response
    landlord_response = models.TextField(
        _('landlord response'),
        max_length=1000,
        blank=True,
        help_text=_("Response from the property landlord")
    )
    
    landlord_response_date = models.DateTimeField(
        _('landlord response date'),
        null=True,
        blank=True
    )
    
    # Helpfulness tracking
    helpful_votes = models.PositiveIntegerField(
        _('helpful votes'),
        default=0,
        help_text=_("Number of users who found this review helpful")
    )
    
    total_votes = models.PositiveIntegerField(
        _('total votes'),
        default=0,
        help_text=_("Total number of helpfulness votes")
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
    
    class Meta:
        verbose_name = _('Review')
        verbose_name_plural = _('Reviews')
        ordering = ['-created_at']
        unique_together = ['rental', 'tenant']  # One review per tenant per property
        indexes = [
            models.Index(fields=['rental', 'is_approved']),
            models.Index(fields=['tenant']),
            models.Index(fields=['rating']),
            models.Index(fields=['is_verified']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        """String representation of the review."""
        return f"Review by {self.tenant.get_full_name()} for {self.rental.title} - {self.rating}★"
    
    def clean(self):
        """Custom validation."""
        super().clean()
        
        # Validate move-out date is after move-in date
        if self.move_in_date and self.move_out_date:
            if self.move_out_date <= self.move_in_date:
                raise ValidationError({
                    'move_out_date': _('Move-out date must be after move-in date.')
                })
        
        # Ensure tenant can only review each property once
        if self.pk is None:  # New review
            existing_review = Review.objects.filter(
                rental=self.rental,
                tenant=self.tenant
            ).exists()
            
            if existing_review:
                raise ValidationError({
                    'tenant': _('You have already reviewed this property.')
                })
    
    @property
    def stay_duration_months(self):
        """Calculate stay duration in months."""
        if self.move_in_date and self.move_out_date:
            delta = self.move_out_date - self.move_in_date
            return round(delta.days / 30.44)  # Average days per month
        return None
    
    @property
    def helpfulness_percentage(self):
        """Calculate helpfulness percentage."""
        if self.total_votes > 0:
            return round((self.helpful_votes / self.total_votes) * 100, 1)
        return 0
    
    def save(self, *args, **kwargs):
        """Override save method for custom operations."""
        # Auto-generate title if not provided
        if not self.title:
            rating_text = dict(self.RATING_CHOICES)[self.rating]
            self.title = f"{rating_text} experience"
        
        super().save(*args, **kwargs)


class ReviewHelpfulness(models.Model):
    """
    Model for tracking review helpfulness votes.
    
    Prevents users from voting multiple times on the same review.
    """
    
    review = models.ForeignKey(
        Review,
        on_delete=models.CASCADE,
        related_name='helpfulness_votes'
    )
    
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='review_votes'
    )
    
    is_helpful = models.BooleanField(
        _('is helpful'),
        help_text=_("Whether the user found the review helpful")
    )
    
    created_at = models.DateTimeField(
        _('voted at'),
        auto_now_add=True
    )
    
    class Meta:
        verbose_name = _('Review Helpfulness Vote')
        verbose_name_plural = _('Review Helpfulness Votes')
        unique_together = ['review', 'user']
        indexes = [
            models.Index(fields=['review']),
            models.Index(fields=['user']),
        ]
    
    def __str__(self):
        """String representation of the vote."""
        helpful_text = "helpful" if self.is_helpful else "not helpful"
        return f"{self.user.get_full_name()} found review {helpful_text}"


class ReviewReport(models.Model):
    """
    Model for reporting inappropriate reviews.
    
    Allows users to report reviews that violate community guidelines.
    """
    
    REPORT_REASONS = [
        ('spam', _('Spam or fake review')),
        ('inappropriate', _('Inappropriate content')),
        ('offensive', _('Offensive language')),
        ('personal', _('Personal attack')),
        ('irrelevant', _('Not relevant to property')),
        ('false', _('False information')),
        ('other', _('Other reason')),
    ]
    
    review = models.ForeignKey(
        Review,
        on_delete=models.CASCADE,
        related_name='reports'
    )
    
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='review_reports'
    )
    
    reason = models.CharField(
        _('report reason'),
        max_length=20,
        choices=REPORT_REASONS,
        help_text=_("Reason for reporting this review")
    )
    
    description = models.TextField(
        _('description'),
        max_length=500,
        blank=True,
        help_text=_("Additional details about the report")
    )
    
    # Status tracking
    is_resolved = models.BooleanField(
        _('resolved'),
        default=False,
        help_text=_("Whether this report has been resolved by admin")
    )
    
    admin_action = models.TextField(
        _('admin action'),
        blank=True,
        help_text=_("Action taken by admin to resolve this report")
    )
    
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resolved_reports',
        limit_choices_to={'user_type': 'admin'}
    )
    
    resolved_at = models.DateTimeField(
        _('resolved at'),
        null=True,
        blank=True
    )
    
    created_at = models.DateTimeField(
        _('reported at'),
        auto_now_add=True
    )
    
    class Meta:
        verbose_name = _('Review Report')
        verbose_name_plural = _('Review Reports')
        ordering = ['-created_at']
        unique_together = ['review', 'reporter']  # Prevent duplicate reports
        indexes = [
            models.Index(fields=['review']),
            models.Index(fields=['reporter']),
            models.Index(fields=['is_resolved']),
            models.Index(fields=['created_at']),
        ]
    
    def __str__(self):
        """String representation of the report."""
        return f"Report by {self.reporter.get_full_name()} for review #{self.review.id}"


# Signal handlers for maintaining review statistics
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

@receiver(post_save, sender=ReviewHelpfulness)
def update_review_helpfulness_on_save(sender, instance, created, **kwargs):
    """Update review helpfulness counts when a vote is saved."""
    if created:
        review = instance.review
        helpful_count = review.helpfulness_votes.filter(is_helpful=True).count()
        total_count = review.helpfulness_votes.count()
        
        Review.objects.filter(id=review.id).update(
            helpful_votes=helpful_count,
            total_votes=total_count
        )

@receiver(post_delete, sender=ReviewHelpfulness)
def update_review_helpfulness_on_delete(sender, instance, **kwargs):
    """Update review helpfulness counts when a vote is deleted."""
    review = instance.review
    helpful_count = review.helpfulness_votes.filter(is_helpful=True).count()
    total_count = review.helpfulness_votes.count()
    
    Review.objects.filter(id=review.id).update(
        helpful_votes=helpful_count,
        total_votes=total_count
    )