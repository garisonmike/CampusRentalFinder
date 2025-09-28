"""
Serializers for the rentals app.

This module contains serializers for rental properties, images, and related operations.
"""

from rest_framework import serializers
from django.utils.translation import gettext_lazy as _
from django.contrib.auth import get_user_model
from datetime import date

from .models import Rental, RentalImage, RentalFavorite, RentalInquiry

User = get_user_model()


class RentalImageSerializer(serializers.ModelSerializer):
    """
    Serializer for rental images.
    """
    
    image_url = serializers.SerializerMethodField()
    
    class Meta:
        model = RentalImage
        fields = [
            'id',
            'image',
            'image_url',
            'caption',
            'is_primary',
            'order',
            'uploaded_at',
        ]
        read_only_fields = ['uploaded_at']
    
    def get_image_url(self, obj):
        """Get the full URL for the image."""
        if obj.image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.image.url)
            return obj.image.url
        return None


class LandlordSerializer(serializers.ModelSerializer):
    """
    Serializer for landlord information in rental listings.
    """
    
    full_name = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = [
            'id',
            'first_name',
            'last_name',
            'full_name',
            'phone_number',
            'is_verified',
        ]
    
    def get_full_name(self, obj):
        """Get landlord's full name."""
        return obj.get_full_name()


class RentalListSerializer(serializers.ModelSerializer):
    """
    Serializer for rental list view.
    
    Contains essential information for displaying rental cards/list items.
    """
    
    landlord = LandlordSerializer(read_only=True)
    primary_image = serializers.SerializerMethodField()
    average_rating = serializers.ReadOnlyField()
    review_count = serializers.ReadOnlyField()
    is_favorited = serializers.SerializerMethodField()
    full_address = serializers.ReadOnlyField()
    
    class Meta:
        model = Rental
        fields = [
            'id',
            'title',
            'description',
            'property_type',
            'price',
            'security_deposit',
            'utilities_included',
            'address',
            'city',
            'state',
            'zip_code',
            'full_address',
            'latitude',
            'longitude',
            'bedrooms',
            'bathrooms',
            'square_footage',
            'furnishing_status',
            'available_from',
            'status',
            'is_featured',
            'distance_to_campus',
            'shuttle_service',
            'views_count',
            'landlord',
            'primary_image',
            'average_rating',
            'review_count',
            'is_favorited',
            'created_at',
        ]
    
    def get_primary_image(self, obj):
        """Get primary image for the rental."""
        primary_image = obj.images.filter(is_primary=True).first()
        if primary_image:
            return RentalImageSerializer(
                primary_image, 
                context=self.context
            ).data
        # Fallback to first image if no primary image set
        first_image = obj.images.first()
        if first_image:
            return RentalImageSerializer(
                first_image, 
                context=self.context
            ).data
        return None
    
    def get_is_favorited(self, obj):
        """Check if current user has favorited this rental."""
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return RentalFavorite.objects.filter(
                user=request.user,
                rental=obj
            ).exists()
        return False


class RentalDetailSerializer(serializers.ModelSerializer):
    """
    Serializer for detailed rental view.
    
    Contains all rental information including images and landlord details.
    """
    
    landlord = LandlordSerializer(read_only=True)
    images = RentalImageSerializer(many=True, read_only=True)
    average_rating = serializers.ReadOnlyField()
    review_count = serializers.ReadOnlyField()
    is_favorited = serializers.SerializerMethodField()
    full_address = serializers.ReadOnlyField()
    contact_email = serializers.SerializerMethodField()
    contact_phone = serializers.SerializerMethodField()
    
    class Meta:
        model = Rental
        fields = [
            'id',
            'title',
            'description',
            'property_type',
            'price',
            'security_deposit',
            'utilities_included',
            'address',
            'city',
            'state',
            'zip_code',
            'country',
            'full_address',
            'latitude',
            'longitude',
            'bedrooms',
            'bathrooms',
            'square_footage',
            'furnishing_status',
            'parking_available',
            'parking_spots',
            'pets_allowed',
            'smoking_allowed',
            'laundry_available',
            'internet_included',
            'gym_access',
            'pool_access',
            'available_from',
            'lease_duration_min',
            'lease_duration_max',
            'status',
            'is_featured',
            'contact_phone',
            'contact_email',
            'distance_to_campus',
            'shuttle_service',
            'views_count',
            'landlord',
            'images',
            'average_rating',
            'review_count',
            'is_favorited',
            'created_at',
            'updated_at',
        ]
    
    def get_is_favorited(self, obj):
        """Check if current user has favorited this rental."""
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            return RentalFavorite.objects.filter(
                user=request.user,
                rental=obj
            ).exists()
        return False
    
    def get_contact_email(self, obj):
        """Get contact email for the rental."""
        return obj.get_contact_email()
    
    def get_contact_phone(self, obj):
        """Get contact phone for the rental."""
        return obj.get_contact_phone()


class RentalCreateSerializer(serializers.ModelSerializer):
    """
    Serializer for creating new rental properties.
    """
    
    images = serializers.ListField(
        child=serializers.ImageField(allow_empty_file=False),
        write_only=True,
        required=False,
        help_text=_("Upload multiple images for the property")
    )
    
    class Meta:
        model = Rental
        fields = [
            'title',
            'description',
            'property_type',
            'price',
            'security_deposit',
            'utilities_included',
            'address',
            'city',
            'state',
            'zip_code',
            'country',
            'latitude',
            'longitude',
            'bedrooms',
            'bathrooms',
            'square_footage',
            'furnishing_status',
            'parking_available',
            'parking_spots',
            'pets_allowed',
            'smoking_allowed',
            'laundry_available',
            'internet_included',
            'gym_access',
            'pool_access',
            'available_from',
            'lease_duration_min',
            'lease_duration_max',
            'contact_phone',
            'contact_email',
            'distance_to_campus',
            'shuttle_service',
            'images',
        ]
    
    def validate_available_from(self, value):
        """Validate available_from date."""
        if value < date.today():
            raise serializers.ValidationError(
                _("Available date cannot be in the past.")
            )
        return value
    
    def validate_lease_duration_max(self, value):
        """Validate maximum lease duration."""
        if value and value < 1:
            raise serializers.ValidationError(
                _("Maximum lease duration must be at least 1 month.")
            )
        return value
    
    def validate(self, data):
        """Custom validation for rental data."""
        # Check lease duration
        min_duration = data.get('lease_duration_min', 12)
        max_duration = data.get('lease_duration_max')
        
        if max_duration and max_duration < min_duration:
            raise serializers.ValidationError({
                'lease_duration_max': _(
                    "Maximum lease duration cannot be less than minimum duration."
                )
            })
        
        # Validate parking spots
        if data.get('parking_available') and not data.get('parking_spots'):
            data['parking_spots'] = 1  # Default to 1 spot if parking available
        
        return data
    
    def create(self, validated_data):
        """Create rental with images."""
        # Extract images from validated data
        images_data = validated_data.pop('images', [])
        
        # Set landlord to current user
        validated_data['landlord'] = self.context['request'].user
        
        # Create rental
        rental = Rental.objects.create(**validated_data)
        
        # Create images
        for idx, image in enumerate(images_data):
            RentalImage.objects.create(
                rental=rental,
                image=image,
                order=idx,
                is_primary=(idx == 0)  # First image is primary
            )
        
        return rental


class RentalUpdateSerializer(serializers.ModelSerializer):
    """
    Serializer for updating rental properties.
    """
    
    class Meta:
        model = Rental
        fields = [
            'title',
            'description',
            'property_type',
            'price',
            'security_deposit',
            'utilities_included',
            'address',
            'city',
            'state',
            'zip_code',
            'country',
            'latitude',
            'longitude',
            'bedrooms',
            'bathrooms',
            'square_footage',
            'furnishing_status',
            'parking_available',
            'parking_spots',
            'pets_allowed',
            'smoking_allowed',
            'laundry_available',
            'internet_included',
            'gym_access',
            'pool_access',
            'available_from',
            'lease_duration_min',
            'lease_duration_max',
            'status',
            'contact_phone',
            'contact_email',
            'distance_to_campus',
            'shuttle_service',
        ]
    
    def validate_available_from(self, value):
        """Validate available_from date."""
        if value < date.today():
            raise serializers.ValidationError(
                _("Available date cannot be in the past.")
            )
        return value
    
    def validate(self, data):
        """Custom validation for rental updates."""
        # Check lease duration
        instance = self.instance
        min_duration = data.get('lease_duration_min', instance.lease_duration_min)
        max_duration = data.get('lease_duration_max', instance.lease_duration_max)
        
        if max_duration and max_duration < min_duration:
            raise serializers.ValidationError({
                'lease_duration_max': _(
                    "Maximum lease duration cannot be less than minimum duration."
                )
            })
        
        return data


class RentalFavoriteSerializer(serializers.ModelSerializer):
    """
    Serializer for rental favorites.
    """
    
    rental = RentalListSerializer(read_only=True)
    
    class Meta:
        model = RentalFavorite
        fields = [
            'id',
            'rental',
            'created_at',
        ]
        read_only_fields = ['created_at']


class RentalInquirySerializer(serializers.ModelSerializer):
    """
    Serializer for rental inquiries.
    """
    
    tenant = serializers.StringRelatedField(read_only=True)
    rental_title = serializers.CharField(source='rental.title', read_only=True)
    
    class Meta:
        model = RentalInquiry
        fields = [
            'id',
            'rental',
            'rental_title',
            'tenant',
            'message',
            'contact_phone',
            'preferred_move_date',
            'status',
            'landlord_reply',
            'replied_at',
            'created_at',
        ]
        read_only_fields = [
            'tenant',
            'status',
            'landlord_reply',
            'replied_at',
            'created_at',
        ]
    
    def create(self, validated_data):
        """Create inquiry with current user as tenant."""
        validated_data['tenant'] = self.context['request'].user
        return super().create(validated_data)
    
    def validate_preferred_move_date(self, value):
        """Validate preferred move-in date."""
        if value and value < date.today():
            raise serializers.ValidationError(
                _("Preferred move-in date cannot be in the past.")
            )
        return value


class RentalInquiryReplySerializer(serializers.ModelSerializer):
    """
    Serializer for landlord replies to inquiries.
    """
    
    class Meta:
        model = RentalInquiry
        fields = [
            'landlord_reply',
        ]
    
    def validate_landlord_reply(self, value):
        """Validate reply message."""
        if not value.strip():
            raise serializers.ValidationError(
                _("Reply message cannot be empty.")
            )
        return value
    
    def update(self, instance, validated_data):
        """Update inquiry with reply."""
        from django.utils import timezone
        
        validated_data['status'] = 'replied'
        validated_data['replied_at'] = timezone.now()
        
        return super().update(instance, validated_data)


class RentalSearchSerializer(serializers.Serializer):
    """
    Serializer for rental search parameters.
    """
    
    query = serializers.CharField(
        required=False,
        help_text=_("Search query for title, description, or address")
    )
    
    city = serializers.CharField(
        required=False,
        help_text=_("Filter by city")
    )
    
    state = serializers.CharField(
        required=False,
        help_text=_("Filter by state")
    )
    
    property_type = serializers.ChoiceField(
        choices=Rental.PROPERTY_TYPES,
        required=False,
        help_text=_("Filter by property type")
    )
    
    min_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        help_text=_("Minimum monthly rent")
    )
    
    max_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        help_text=_("Maximum monthly rent")
    )
    
    bedrooms = serializers.IntegerField(
        required=False,
        min_value=0,
        help_text=_("Number of bedrooms")
    )
    
    bathrooms = serializers.IntegerField(
        required=False,
        min_value=1,
        help_text=_("Number of bathrooms")
    )
    
    pets_allowed = serializers.BooleanField(
        required=False,
        help_text=_("Filter properties that allow pets")
    )
    
    parking_available = serializers.BooleanField(
        required=False,
        help_text=_("Filter properties with parking")
    )
    
    furnishing_status = serializers.ChoiceField(
        choices=Rental.FURNISHING_STATUS,
        required=False,
        help_text=_("Filter by furnishing status")
    )
    
    utilities_included = serializers.BooleanField(
        required=False,
        help_text=_("Filter properties with utilities included")
    )
    
    max_distance_to_campus = serializers.FloatField(
        required=False,
        min_value=0,
        help_text=_("Maximum distance to campus in miles")
    )
    
    shuttle_service = serializers.BooleanField(
        required=False,
        help_text=_("Filter properties with shuttle service")
    )
    
    available_from = serializers.DateField(
        required=False,
        help_text=_("Available from date")
    )
    
    # Location-based search
    latitude = serializers.FloatField(
        required=False,
        min_value=-90,
        max_value=90,
        help_text=_("Latitude for location-based search")
    )
    
    longitude = serializers.FloatField(
        required=False,
        min_value=-180,
        max_value=180,
        help_text=_("Longitude for location-based search")
    )
    
    radius = serializers.FloatField(
        required=False,
        min_value=0.1,
        max_value=50,
        help_text=_("Search radius in miles")
    )
    
    # Sorting options
    ordering = serializers.ChoiceField(
        choices=[
            ('created_at', _('Newest first')),
            ('-created_at', _('Oldest first')),
            ('price', _('Price: Low to High')),
            ('-price', _('Price: High to Low')),
            ('distance_to_campus', _('Closest to campus')),
            ('-views_count', _('Most viewed')),
        ],
        required=False,
        default='-created_at',
        help_text=_("Sort order for results")
    )
    
    def validate(self, data):
        """Validate search parameters."""
        # Validate price range
        min_price = data.get('min_price')
        max_price = data.get('max_price')
        
        if min_price and max_price and min_price > max_price:
            raise serializers.ValidationError({
                'max_price': _("Maximum price cannot be less than minimum price.")
            })
        
        # Validate location-based search
        latitude = data.get('latitude')
        longitude = data.get('longitude')
        radius = data.get('radius')
        
        if any([latitude, longitude, radius]):
            if not all([latitude, longitude, radius]):
                raise serializers.ValidationError(
                    _("Latitude, longitude, and radius are all required for location-based search.")
                )
        
        return data


# Admin-only serializers
class AdminRentalSerializer(serializers.ModelSerializer):
    """
    Admin serializer for managing rentals.
    
    Includes all fields and allows admin operations.
    """
    
    landlord = LandlordSerializer(read_only=True)
    images_count = serializers.SerializerMethodField()
    inquiries_count = serializers.SerializerMethodField()
    favorites_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Rental
        fields = '__all__'
    
    def get_images_count(self, obj):
        """Get number of images for this rental."""
        return obj.images.count()
    
    def get_inquiries_count(self, obj):
        """Get number of inquiries for this rental."""
        return obj.inquiries.count()
    
    def get_favorites_count(self, obj):
        """Get number of users who favorited this rental."""
        return obj.favorited_by.count()