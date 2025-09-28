"""
Views for the rentals app.

This module contains views for rental property management, search,
favorites, and inquiries.
"""

from rest_framework import status, permissions, filters
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated, AllowAny, IsAdminUser
from django.db.models import Q, Avg, Count, F
from django.utils.translation import gettext_lazy as _
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.openapi import OpenApiTypes
from datetime import date, datetime, timedelta

from .models import Rental, RentalImage, RentalFavorite, RentalInquiry
from .serializers import (
    RentalListSerializer,
    RentalDetailSerializer,
    RentalCreateSerializer,
    RentalUpdateSerializer,
    RentalImageSerializer,
    RentalFavoriteSerializer,
    RentalInquirySerializer,
    RentalInquiryReplySerializer,
    RentalSearchSerializer,
    AdminRentalSerializer,
)


class IsLandlordOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow landlords to create/edit rentals.
    """
    
    def has_permission(self, request, view):
        # Read permissions for any request
        if request.method in permissions.SAFE_METHODS:
            return True
        
        # Write permissions only for authenticated landlords
        return (
            request.user.is_authenticated and 
            request.user.user_type == 'landlord'
        )
    
    def has_object_permission(self, request, view, obj):
        # Read permissions for any request
        if request.method in permissions.SAFE_METHODS:
            return True
        
        # Write permissions only for the owner landlord or admin
        return (
            obj.landlord == request.user or 
            request.user.user_type == 'admin'
        )


class RentalViewSet(ModelViewSet):
    """
    ViewSet for rental property management.
    
    Provides CRUD operations for rental properties with search and filtering.
    """
    
    queryset = Rental.objects.select_related('landlord').prefetch_related(
        'images', 'reviews'
    ).filter(status__in=['available', 'rented'])
    
    permission_classes = [IsLandlordOrReadOnly]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'description', 'address', 'city']
    ordering_fields = ['price', 'created_at', 'available_from', 'views_count']
    ordering = ['-created_at']
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        if self.action == 'list':
            return RentalListSerializer
        elif self.action == 'create':
            return RentalCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return RentalUpdateSerializer
        else:
            return RentalDetailSerializer
    
    def get_queryset(self):
        """Filter queryset based on user type and request parameters."""
        queryset = self.queryset
        
        # Apply search filters
        search_params = RentalSearchSerializer(data=self.request.query_params)
        if search_params.is_valid():
            data = search_params.validated_data
            
            # Text search
            if data.get('query'):
                queryset = queryset.filter(
                    Q(title__icontains=data['query']) |
                    Q(description__icontains=data['query']) |
                    Q(address__icontains=data['query']) |
                    Q(city__icontains=data['query'])
                )
            
            # Location filters
            if data.get('city'):
                queryset = queryset.filter(city__icontains=data['city'])
            
            if data.get('state'):
                queryset = queryset.filter(state__icontains=data['state'])
            
            # Property filters
            if data.get('property_type'):
                queryset = queryset.filter(property_type=data['property_type'])
            
            if data.get('min_price'):
                queryset = queryset.filter(price__gte=data['min_price'])
            
            if data.get('max_price'):
                queryset = queryset.filter(price__lte=data['max_price'])
            
            if data.get('bedrooms') is not None:
                queryset = queryset.filter(bedrooms=data['bedrooms'])
            
            if data.get('bathrooms'):
                queryset = queryset.filter(bathrooms__gte=data['bathrooms'])
            
            # Feature filters
            if data.get('pets_allowed'):
                queryset = queryset.filter(pets_allowed=True)
            
            if data.get('parking_available'):
                queryset = queryset.filter(parking_available=True)
            
            if data.get('furnishing_status'):
                queryset = queryset.filter(furnishing_status=data['furnishing_status'])
            
            if data.get('utilities_included'):
                queryset = queryset.filter(utilities_included=True)
            
            if data.get('shuttle_service'):
                queryset = queryset.filter(shuttle_service=True)
            
            # Distance filter
            if data.get('max_distance_to_campus'):
                queryset = queryset.filter(
                    distance_to_campus__lte=data['max_distance_to_campus']
                )
            
            # Availability filter
            if data.get('available_from'):
                queryset = queryset.filter(
                    available_from__lte=data['available_from']
                )
            
            # Location-based search (simplified - would use PostGIS in production)
            # This is a basic implementation for MVP
            if all([data.get('latitude'), data.get('longitude'), data.get('radius')]):
                # Basic bounding box calculation (not accurate for large distances)
                lat = data['latitude']
                lon = data['longitude']
                radius = data['radius']
                
                # Rough approximation: 1 degree ≈ 69 miles
                lat_delta = radius / 69
                lon_delta = radius / (69 * abs(lat / 90))  # Adjust for latitude
                
                queryset = queryset.filter(
                    latitude__range=[lat - lat_delta, lat + lat_delta],
                    longitude__range=[lon - lon_delta, lon + lon_delta]
                )
            
            # Custom ordering
            if data.get('ordering'):
                queryset = queryset.order_by(data['ordering'])
        
        return queryset
    
    @extend_schema(
        summary="List rental properties",
        description="Get paginated list of rental properties with search and filtering",
        parameters=[
            OpenApiParameter('query', OpenApiTypes.STR, description='Search query'),
            OpenApiParameter('city', OpenApiTypes.STR, description='Filter by city'),
            OpenApiParameter('min_price', OpenApiTypes.NUMBER, description='Minimum price'),
            OpenApiParameter('max_price', OpenApiTypes.NUMBER, description='Maximum price'),
            OpenApiParameter('bedrooms', OpenApiTypes.INT, description='Number of bedrooms'),
            OpenApiParameter('property_type', OpenApiTypes.STR, description='Property type'),
            OpenApiParameter('pets_allowed', OpenApiTypes.BOOL, description='Pets allowed'),
            OpenApiParameter('parking_available', OpenApiTypes.BOOL, description='Parking available'),
        ],
        tags=["Rentals"]
    )
    def list(self, request, *args, **kwargs):
        """List rental properties with search and filtering."""
        return super().list(request, *args, **kwargs)
    
    @extend_schema(
        summary="Get rental details",
        description="Get detailed information about a specific rental property",
        tags=["Rentals"]
    )
    def retrieve(self, request, *args, **kwargs):
        """Get rental details and increment view count."""
        rental = self.get_object()
        
        # Increment view count (only for non-landlord users)
        if not (request.user.is_authenticated and request.user == rental.landlord):
            rental.increment_views()
        
        serializer = self.get_serializer(rental)
        return Response(serializer.data)
    
    @extend_schema(
        summary="Create rental property",
        description="Create a new rental property (Landlord only)",
        tags=["Rentals"]
    )
    def create(self, request, *args, **kwargs):
        """Create a new rental property."""
        return super().create(request, *args, **kwargs)
    
    @extend_schema(
        summary="Update rental property",
        description="Update rental property (Owner only)",
        tags=["Rentals"]
    )
    def update(self, request, *args, **kwargs):
        """Update rental property."""
        return super().update(request, *args, **kwargs)
    
    @extend_schema(
        summary="Delete rental property",
        description="Delete rental property (Owner only)",
        tags=["Rentals"]
    )
    def destroy(self, request, *args, **kwargs):
        """Delete rental property."""
        return super().destroy(request, *args, **kwargs)
    
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    @extend_schema(
        summary="Toggle favorite",
        description="Add/remove rental from user's favorites",
        tags=["Rentals"]
    )
    def toggle_favorite(self, request, pk=None):
        """Toggle rental favorite status for current user."""
        rental = self.get_object()
        user = request.user
        
        favorite, created = RentalFavorite.objects.get_or_create(
            user=user,
            rental=rental
        )
        
        if not created:
            favorite.delete()
            return Response({
                'message': _('Rental removed from favorites'),
                'is_favorited': False
            })
        else:
            return Response({
                'message': _('Rental added to favorites'),
                'is_favorited': True
            }, status=status.HTTP_201_CREATED)
    
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated])
    @extend_schema(
        summary="Get user's favorite rentals",
        description="Get list of current user's favorite rentals",
        tags=["Rentals"]
    )
    def favorites(self, request):
        """Get user's favorite rentals."""
        favorites = RentalFavorite.objects.filter(
            user=request.user
        ).select_related('rental__landlord')
        
        serializer = RentalFavoriteSerializer(favorites, many=True, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated])
    @extend_schema(
        summary="Get landlord's properties",
        description="Get current landlord's rental properties",
        tags=["Rentals"]
    )
    def my_properties(self, request):
        """Get current landlord's properties."""
        if request.user.user_type != 'landlord':
            return Response(
                {'error': _('Only landlords can access this endpoint')},
                status=status.HTTP_403_FORBIDDEN
            )
        
        rentals = Rental.objects.filter(
            landlord=request.user
        ).prefetch_related('images', 'reviews')
        
        serializer = RentalListSerializer(rentals, many=True, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'], permission_classes=[IsAuthenticated])
    @extend_schema(
        summary="Get rental inquiries",
        description="Get inquiries for a specific rental (Landlord only)",
        tags=["Rentals"]
    )
    def inquiries(self, request, pk=None):
        """Get inquiries for rental (landlord only)."""
        rental = self.get_object()
        
        # Check if user is the landlord
        if request.user != rental.landlord:
            return Response(
                {'error': _('Only the property owner can view inquiries')},
                status=status.HTTP_403_FORBIDDEN
            )
        
        inquiries = rental.inquiries.all().order_by('-created_at')
        serializer = RentalInquirySerializer(inquiries, many=True)
        return Response(serializer.data)


class RentalImageViewSet(ModelViewSet):
    """
    ViewSet for managing rental images.
    """
    
    queryset = RentalImage.objects.all()
    serializer_class = RentalImageSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Filter images by rental if rental_id is provided."""
        queryset = self.queryset
        rental_id = self.request.query_params.get('rental_id')
        if rental_id:
            queryset = queryset.filter(rental_id=rental_id)
        return queryset
    
    def perform_create(self, serializer):
        """Ensure user owns the rental before adding images."""
        rental = serializer.validated_data['rental']
        if rental.landlord != self.request.user:
            raise PermissionError(_("You can only add images to your own properties"))
        
        serializer.save()
    
    @action(detail=True, methods=['post'])
    @extend_schema(
        summary="Set as primary image",
        description="Set image as primary for the rental",
        tags=["Rental Images"]
    )
    def set_primary(self, request, pk=None):
        """Set image as primary for the rental."""
        image = self.get_object()
        
        # Check permission
        if image.rental.landlord != request.user:
            return Response(
                {'error': _('Permission denied')},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Update primary status
        RentalImage.objects.filter(rental=image.rental).update(is_primary=False)
        image.is_primary = True
        image.save()
        
        return Response({
            'message': _('Image set as primary successfully')
        })


class RentalInquiryViewSet(ModelViewSet):
    """
    ViewSet for managing rental inquiries.
    """
    
    queryset = RentalInquiry.objects.all()
    serializer_class = RentalInquirySerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Filter inquiries based on user type."""
        user = self.request.user
        
        if user.user_type == 'tenant':
            # Tenants see their own inquiries
            return self.queryset.filter(tenant=user)
        elif user.user_type == 'landlord':
            # Landlords see inquiries for their properties
            return self.queryset.filter(rental__landlord=user)
        elif user.user_type == 'admin':
            # Admins see all inquiries
            return self.queryset
        else:
            return self.queryset.none()
    
    @extend_schema(
        summary="Create rental inquiry",
        description="Submit an inquiry about a rental property (Tenant only)",
        tags=["Rental Inquiries"]
    )
    def create(self, request, *args, **kwargs):
        """Create inquiry (tenant only)."""
        if request.user.user_type != 'tenant':
            return Response(
                {'error': _('Only tenants can submit inquiries')},
                status=status.HTTP_403_FORBIDDEN
            )
        
        return super().create(request, *args, **kwargs)
    
    @action(detail=True, methods=['post'])
    @extend_schema(
        summary="Reply to inquiry",
        description="Reply to a rental inquiry (Landlord only)",
        request=RentalInquiryReplySerializer,
        tags=["Rental Inquiries"]
    )
    def reply(self, request, pk=None):
        """Reply to inquiry (landlord only)."""
        inquiry = self.get_object()
        
        # Check if user is the landlord
        if request.user != inquiry.rental.landlord:
            return Response(
                {'error': _('Only the property owner can reply to inquiries')},
                status=status.HTTP_403_FORBIDDEN
            )
        
        serializer = RentalInquiryReplySerializer(
            inquiry,
            data=request.data,
            partial=True
        )
        
        if serializer.is_valid():
            serializer.save()
            return Response({
                'message': _('Reply sent successfully'),
                'inquiry': RentalInquirySerializer(inquiry).data
            })
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# Function-based views
@extend_schema(
    summary="Get featured rentals",
    description="Get list of featured rental properties",
    responses={200: RentalListSerializer(many=True)},
    tags=["Rentals"]
)
@api_view(['GET'])
@permission_classes([AllowAny])
def featured_rentals(request):
    """Get featured rental properties."""
    rentals = Rental.objects.filter(
        is_featured=True,
        status='available'
    ).select_related('landlord').prefetch_related('images')[:10]
    
    serializer = RentalListSerializer(rentals, many=True, context={'request': request})
    return Response(serializer.data)


@extend_schema(
    summary="Get recent rentals",
    description="Get recently added rental properties",
    responses={200: RentalListSerializer(many=True)},
    tags=["Rentals"]
)
@api_view(['GET'])
@permission_classes([AllowAny])
def recent_rentals(request):
    """Get recently added rentals."""
    rentals = Rental.objects.filter(
        status='available'
    ).select_related('landlord').prefetch_related('images').order_by('-created_at')[:10]
    
    serializer = RentalListSerializer(rentals, many=True, context={'request': request})
    return Response(serializer.data)


@extend_schema(
    summary="Get rental statistics",
    description="Get rental platform statistics (Admin only)",
    tags=["Admin"]
)
@api_view(['GET'])
@permission_classes([IsAdminUser])
def rental_statistics(request):
    """Get rental statistics for admin dashboard."""
    from django.db.models import Avg, Count
    
    stats = {
        'total_rentals': Rental.objects.count(),
        'available_rentals': Rental.objects.filter(status='available').count(),
        'rented_rentals': Rental.objects.filter(status='rented').count(),
        'featured_rentals': Rental.objects.filter(is_featured=True).count(),
        'average_price': Rental.objects.aggregate(avg_price=Avg('price'))['avg_price'] or 0,
        'total_inquiries': RentalInquiry.objects.count(),
        'total_favorites': RentalFavorite.objects.count(),
    }
    
    # Property type breakdown
    property_types = Rental.objects.values('property_type').annotate(
        count=Count('id')
    ).order_by('-count')
    
    stats['property_type_stats'] = {
        item['property_type']: item['count'] 
        for item in property_types
    }
    
    # Monthly statistics (last 12 months)
    monthly_stats = []
    current_date = datetime.now().date()
    
    for i in range(12):
        month_start = current_date.replace(day=1) - timedelta(days=30*i)
        month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        
        monthly_count = Rental.objects.filter(
            created_at__date__range=[month_start, month_end]
        ).count()
        
        monthly_stats.append({
            'month': month_start.strftime('%Y-%m'),
            'rentals_created': monthly_count
        })
    
    stats['monthly_stats'] = list(reversed(monthly_stats))
    
    return Response(stats)


# Admin ViewSet
class AdminRentalViewSet(ModelViewSet):
    """
    Admin viewset for managing all rentals.
    """
    
    queryset = Rental.objects.all().select_related('landlord').prefetch_related('images')
    serializer_class = AdminRentalSerializer
    permission_classes = [IsAdminUser]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'landlord__email', 'city', 'address']
    ordering_fields = ['created_at', 'price', 'status', 'views_count']
    ordering = ['-created_at']
    
    @action(detail=True, methods=['post'])
    @extend_schema(
        summary="Toggle featured status",
        description="Toggle rental featured status (Admin only)",
        tags=["Admin"]
    )
    def toggle_featured(self, request, pk=None):
        """Toggle rental featured status."""
        rental = self.get_object()
        rental.is_featured = not rental.is_featured
        rental.save()
        
        return Response({
            'message': _('Featured status updated successfully'),
            'is_featured': rental.is_featured
        })
    
    @action(detail=True, methods=['patch'])
    @extend_schema(
        summary="Update rental status",
        description="Update rental status (Admin only)",
        tags=["Admin"]
    )
    def update_status(self, request, pk=None):
        """Update rental status."""
        rental = self.get_object()
        new_status = request.data.get('status')
        
        if new_status not in dict(Rental.RENTAL_STATUS):
            return Response(
                {'error': _('Invalid status')},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        rental.status = new_status
        rental.save()
        
        return Response({
            'message': _('Status updated successfully'),
            'status': rental.status
        })