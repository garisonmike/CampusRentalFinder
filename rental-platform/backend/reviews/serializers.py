"""
Serializers for the reviews app.

This module contains serializers for review management, helpfulness voting, and reporting.
"""

from rest_framework import serializers
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model
from datetime import date

from .models import Review, ReviewHelpfulness, ReviewReport

User = get_user_model()


class ReviewerSerializer(serializers.ModelSerializer):
    """
    Serializer for reviewer information in review displays.
    """
    
    full_name = serializers.SerializerMethodField()
    initials = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = [
            'id',
            'first_name',
            'last_name',
            'full_name',
            'initials',
            'is_verified',
        ]
    
    def get_full_name(self, obj):
        """Get reviewer's full name."""
        return obj.get_full_name()
    
    def get_initials(self, obj):
        """Get reviewer's initials for privacy."""
        first = obj.first_name[0] if obj.first_name else ''
        last = obj.last_name[0] if obj.last_name else ''
        return f"{first}{last}".upper()


class ReviewListSerializer(serializers.ModelSerializer):
    """
    Serializer for review list view.
    
    Contains essential information for displaying review cards/list items.
    """
    
    tenant = ReviewerSerializer(read_only=True)
    rental_title = serializers.CharField(source='rental.title', read_only=True)
    stay_duration_months = serializers.ReadOnlyField()
    helpfulness_percentage = serializers.ReadOnlyField()
    user_found_helpful = serializers.SerializerMethodField()
    
    class Meta:
        model = Review
        fields = [
            'id',
            'rental',
            'rental_title',
            'tenant',
            'rating',
            'title',
            'comment',
            'cleanliness_rating',
            'location_rating',
            'value_rating',
            'landlord_rating',
            'pros',
            'cons',
            'move_in_date',
            'move_out_date',
            'stay_duration_months',
            'would_recommend',
            'is_verified',
            'helpful_votes',
            'total_votes',
            'helpfulness_percentage',
            'user_found_helpful',
            'landlord_response',
            'landlord_response_date',
            'created_at',
        ]
    
    def get_user_found_helpful(self, obj):
        """Check if current user found this review helpful."""
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            vote = ReviewHelpfulness.objects.filter(
                review=obj,
                user=request.user
            ).first()
            return vote.is_helpful if vote else None
        return None


class ReviewDetailSerializer(serializers.ModelSerializer):
    """
    Serializer for detailed review view.
    
    Contains all review information including landlord response.
    """
    
    tenant = ReviewerSerializer(read_only=True)
    rental_title = serializers.CharField(source='rental.title', read_only=True)
    rental_address = serializers.CharField(source='rental.full_address', read_only=True)
    stay_duration_months = serializers.ReadOnlyField()
    helpfulness_percentage = serializers.ReadOnlyField()
    user_found_helpful = serializers.SerializerMethodField()
    user_can_edit = serializers.SerializerMethodField()
    
    class Meta:
        model = Review
        fields = [
            'id',
            'rental',
            'rental_title',
            'rental_address',
            'tenant',
            'rating',
            'title',
            'comment',
            'cleanliness_rating',
            'location_rating',
            'value_rating',
            'landlord_rating',
            'pros',
            'cons',
            'move_in_date',
            'move_out_date',
            'stay_duration_months',
            'would_recommend',
            'is_verified',
            'helpful_votes',
            'total_votes',
            'helpfulness_percentage',
            'user_found_helpful',
            'user_can_edit',
            'landlord_response',
            'landlord_response_date',
            'created_at',
            'updated_at',
        ]
    
    def get_user_found_helpful(self, obj):
        """Check if current user found this review helpful."""
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            vote = ReviewHelpfulness.objects.filter(
                review=obj,
                user=request.user
            ).first()
            return vote.is_helpful if vote else None
        return None
    
    def get_user_can_edit(self, obj):
        """Check if current user can edit this review."""
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return (
                obj.tenant == request.user or 
                request.user.user_type == 'admin'
            )
        return False


class ReviewCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating new reviews.
    """
    
    class Meta:
        model = Review
        fields = [
            'rental',
            'rating',
            'title',
            'comment',
            'cleanliness_rating',
            'location_rating',
            'value_rating',
            'landlord_rating',
            'pros',
            'cons',
            'move_in_date',
            'move_out_date',
            'would_recommend',
        ]
    
    def validate_rating(self, value):
        """Validate overall rating."""
        if value < 1 or value > 5:
            raise serializers.ValidationError(
                _("Rating must be between 1 and 5.")
            )
        return value
    
    def validate_comment(self, value):
        """Validate comment content."""
        if len(value.strip()) < 10:
            raise serializers.ValidationError(
                _("Review comment must be at least 10 characters long.")
            )
        return value
    
    def validate_move_out_date(self, value):
        """Validate move-out date."""
        if value and value > date.today():
            raise serializers.ValidationError(
                _("Move-out date cannot be in the future.")
            )
        return value
    
    def validate(self, data):
        """Custom validation for review data."""
        # Validate move-in/move-out date relationship
        move_in = data.get('move_in_date')
        move_out = data.get('move_out_date')
        
        if move_in and move_out and move_out <= move_in:
            raise serializers.ValidationError({
                'move_out_date': _("Move-out date must be after move-in date.")
            })
        
        # Validate move-in date is not too far in the future
        if move_in and move_in > date.today():
            raise serializers.ValidationError({
                'move_in_date': _("Move-in date cannot be in the future.")
            })
        
        return data
    
    def create(self, validated_data):
        """Create review with current user as tenant."""
        validated_data['tenant'] = self.context['request'].user
        
        # Check if user already reviewed this property
        rental = validated_data['rental']
        tenant = validated_data['tenant']
        
        if Review.objects.filter(rental=rental, tenant=tenant).exists():
            raise serializers.ValidationError(
                _("You have already reviewed this property.")
            )
        
        return super().create(validated_data)


class ReviewUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for updating reviews.
    """
    
    class Meta:
        model = Review
        fields = [
            'rating',
            'title',
            'comment',
            'cleanliness_rating',
            'location_rating',
            'value_rating',
            'landlord_rating',
            'pros',
            'cons',
            'move_in_date',
            'move_out_date',
            'would_recommend',
        ]
    
    def validate_rating(self, value):
        """Validate overall rating."""
        if value < 1 or value > 5:
            raise serializers.ValidationError(
                _("Rating must be between 1 and 5.")
            )
        return value
    
    def validate_comment(self, value):
        """Validate comment content."""
        if len(value.strip()) < 10:
            raise serializers.ValidationError(
                _("Review comment must be at least 10 characters long.")
            )
        return value
    
    def validate(self, data):
        """Custom validation for review updates."""
        # Validate move-in/move-out date relationship
        instance = self.instance
        move_in = data.get('move_in_date', instance.move_in_date)
        move_out = data.get('move_out_date', instance.move_out_date)
        
        if move_in and move_out and move_out <= move_in:
            raise serializers.ValidationError({
                'move_out_date': _("Move-out date must be after move-in date.")
            })
        
        return data


class LandlordResponseSerializer(serializers.ModelSerializer):
    """
    Serializer for landlord responses to reviews.
    """
    
    class Meta:
        model = Review
        fields = [
            'landlord_response',
        ]
    
    def validate_landlord_response(self, value):
        """Validate landlord response."""
        if not value.strip():
            raise serializers.ValidationError(
                _("Response cannot be empty.")
            )
        if len(value.strip()) < 10:
            raise serializers.ValidationError(
                _("Response must be at least 10 characters long.")
            )
        return value
    
    def update(self, instance, validated_data):
        """Update review with landlord response."""
        from django.utils import timezone
        
        validated_data['landlord_response_date'] = timezone.now()
        return super().update(instance, validated_data)


class ReviewHelpfulnessSerializer(serializers.ModelSerializer):
    """
    Serializer for review helpfulness votes.
    """
    
    class Meta:
        model = ReviewHelpfulness
        fields = [
            'id',
            'review',
            'is_helpful',
            'created_at',
        ]
        read_only_fields = ['created_at']
    
    def create(self, validated_data):
        """Create or update helpfulness vote."""
        validated_data['user'] = self.context['request'].user
        
        # Check if user already voted on this review
        review = validated_data['review']
        user = validated_data['user']
        
        existing_vote = ReviewHelpfulness.objects.filter(
            review=review,
            user=user
        ).first()
        
        if existing_vote:
            # Update existing vote
            existing_vote.is_helpful = validated_data['is_helpful']
            existing_vote.save()
            return existing_vote
        else:
            # Create new vote
            return super().create(validated_data)


class ReviewReportSerializer(serializers.ModelSerializer):
    """
    Serializer for reporting reviews.
    """
    
    class Meta:
        model = ReviewReport
        fields = [
            'id',
            'review',
            'reason',
            'description',
            'created_at',
        ]
        read_only_fields = ['created_at']
    
    def validate_description(self, value):
        """Validate report description."""
        if value and len(value.strip()) < 10:
            raise serializers.ValidationError(
                _("Description must be at least 10 characters long if provided.")
            )
        return value
    
    def create(self, validated_data):
        """Create report with current user as reporter."""
        validated_data['reporter'] = self.context['request'].user
        
        # Check if user already reported this review
        review = validated_data['review']
        reporter = validated_data['reporter']
        
        if ReviewReport.objects.filter(review=review, reporter=reporter).exists():
            raise serializers.ValidationError(
                _("You have already reported this review.")
            )
        
        return super().create(validated_data)


class ReviewStatisticsSerializer(serializers.Serializer):
    """
    Serializer for review statistics.
    """
    
    total_reviews = serializers.IntegerField(read_only=True)
    average_rating = serializers.DecimalField(max_digits=3, decimal_places=2, read_only=True)
    verified_reviews = serializers.IntegerField(read_only=True)
    
    # Rating distribution
    rating_distribution = serializers.DictField(read_only=True)
    
    # Category averages
    average_cleanliness = serializers.DecimalField(max_digits=3, decimal_places=2, read_only=True)
    average_location = serializers.DecimalField(max_digits=3, decimal_places=2, read_only=True)
    average_value = serializers.DecimalField(max_digits=3, decimal_places=2, read_only=True)
    average_landlord = serializers.DecimalField(max_digits=3, decimal_places=2, read_only=True)
    
    # Recommendation percentage
    recommendation_percentage = serializers.FloatField(read_only=True)
    
    # Monthly statistics
    monthly_stats = serializers.ListField(read_only=True)


# Admin-only serializers
class AdminReviewSerializer(serializers.ModelSerializer):
    """
    Admin serializer for managing reviews.
    """
    
    tenant = ReviewerSerializer(read_only=True)
    rental_title = serializers.CharField(source='rental.title', read_only=True)
    reports_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Review
        fields = '__all__'
    
    def get_reports_count(self, obj):
        """Get number of reports for this review."""
        return obj.reports.filter(is_resolved=False).count()


class AdminReviewReportSerializer(serializers.ModelSerializer):
    """
    Admin serializer for managing review reports.
    """
    
    reporter = ReviewerSerializer(read_only=True)
    review_details = serializers.SerializerMethodField()
    
    class Meta:
        model = ReviewReport
        fields = '__all__'
    
    def get_review_details(self, obj):
        """Get basic review information."""
        return {
            'id': obj.review.id,
            'rating': obj.review.rating,
            'comment': obj.review.comment[:100] + '...' if len(obj.review.comment) > 100 else obj.review.comment,
            'rental_title': obj.review.rental.title,
            'tenant_name': obj.review.tenant.get_full_name(),
        }