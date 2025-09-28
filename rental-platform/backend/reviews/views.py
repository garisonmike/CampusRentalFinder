"""
Views for the reviews app.

This module contains views for review management, helpfulness voting, and reporting.
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

from rentals.models import Rental
from .models import Review, ReviewHelpfulness, ReviewReport
from .serializers import (
    ReviewListSerializer,
    ReviewDetailSerializer,
    ReviewCreateSerializer,
    ReviewUpdateSerializer,
    LandlordResponseSerializer,
    ReviewHelpfulnessSerializer,
    ReviewReportSerializer,
    AdminReviewSerializer,
    AdminReviewReportSerializer,
)


class IsTenantOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow tenants to create reviews.
    """
    
    def has_permission(self, request, view):
        # Read permissions for any request
        if request.method in permissions.SAFE_METHODS:
            return True
        
        # Write permissions only for authenticated tenants
        return (
            request.user.is_authenticated and 
            request.user.user_type == 'tenant'
        )
    
    def has_object_permission(self, request, view, obj):
        # Read permissions for any request
        if request.method in permissions.SAFE_METHODS:
            return True
        
        # Write permissions only for the review author or admin
        return (
            obj.tenant == request.user or 
            request.user.user_type == 'admin'
        )


class ReviewViewSet(ModelViewSet):
    """
    ViewSet for review management.
    
    Provides CRUD operations for reviews with filtering and search.
    """
    
    queryset = Review.objects.select_related(
        'tenant', 'rental'
    ).filter(is_approved=True)
    
    permission_classes = [IsTenantOrReadOnly]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['comment', 'title', 'pros', 'cons']
    ordering_fields = ['rating', 'created_at', 'helpful_votes']
    ordering = ['-created_at']
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action."""
        if self.action == 'list':
            return ReviewListSerializer
        elif self.action == 'create':
            return ReviewCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return ReviewUpdateSerializer
        else:
            return ReviewDetailSerializer
    
    def get_queryset(self):
        """Filter queryset based on request parameters."""
        queryset = self.queryset
        
        # Filter by rental
        rental_id = self.request.query_params.get('rental_id')
        if rental_id:
            queryset = queryset.filter(rental_id=rental_id)
        
        # Filter by rating
        min_rating = self.request.query_params.get('min_rating')
        if min_rating:
            try:
                queryset = queryset.filter(rating__gte=int(min_rating))
            except ValueError:
                pass
        
        max_rating = self.request.query_params.get('max_rating')
        if max_rating:
            try:
                queryset = queryset.filter(rating__lte=int(max_rating))
            except ValueError:
                pass
        
        # Filter by verification status
        verified_only = self.request.query_params.get('verified_only')
        if verified_only and verified_only.lower() == 'true':
            queryset = queryset.filter(is_verified=True)
        
        # Filter by recommendation
        recommended_only = self.request.query_params.get('recommended_only')
        if recommended_only and recommended_only.lower() == 'true':
            queryset = queryset.filter(would_recommend=True)
        
        # Filter by date range
        date_from = self.request.query_params.get('date_from')
        if date_from:
            try:
                date_obj = datetime.strptime(date_from, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__gte=date_obj)
            except ValueError:
                pass
        
        date_to = self.request.query_params.get('date_to')
        if date_to:
            try:
                date_obj = datetime.strptime(date_to, '%Y-%m-%d').date()
                queryset = queryset.filter(created_at__date__lte=date_obj)
            except ValueError:
                pass
        
        return queryset
    
    @extend_schema(
        summary="List reviews",
        description="Get paginated list of reviews with search and filtering",
        parameters=[
            OpenApiParameter('rental_id', OpenApiTypes.INT, description='Filter by rental property'),
            OpenApiParameter('min_rating', OpenApiTypes.INT, description='Minimum rating (1-5)'),
            OpenApiParameter('max_rating', OpenApiTypes.INT, description='Maximum rating (1-5)'),
            OpenApiParameter('verified_only', OpenApiTypes.BOOL, description='Show only verified reviews'),
            OpenApiParameter('recommended_only', OpenApiTypes.BOOL, description='Show only recommended properties'),
        ],
        tags=["Reviews"]
    )
    def list(self, request, *args, **kwargs):
        """List reviews with search and filtering."""
        return super().list(request, *args, **kwargs)
    
    @extend_schema(
        summary="Get review details",
        description="Get detailed information about a specific review",
        tags=["Reviews"]
    )
    def retrieve(self, request, *args, **kwargs):
        """Get review details."""
        return super().retrieve(request, *args, **kwargs)
    
    @extend_schema(
        summary="Create review",
        description="Create a new review for a rental property (Tenant only)",
        tags=["Reviews"]
    )
    def create(self, request, *args, **kwargs):
        """Create a new review."""
        return super().create(request, *args, **kwargs)
    
    @extend_schema(
        summary="Update review",
        description="Update review (Author only)",
        tags=["Reviews"]
    )
    def update(self, request, *args, **kwargs):
        """Update review."""
        return super().update(request, *args, **kwargs)
    
    @extend_schema(
        summary="Delete review",
        description="Delete review (Author only)",
        tags=["Reviews"]
    )
    def destroy(self, request, *args, **kwargs):
        """Delete review."""
        return super().destroy(request, *args, **kwargs)
    
    @action(detail=True, methods=['post'], permission_classes=[IsAuthenticated])
    @extend_schema(
        summary="Vote review helpfulness",
        description="Mark a review as helpful or not helpful",
        request=ReviewHelpfulnessSerializer,
        tags=["Reviews"]
    )
    def vote_helpfulness(self, request, pk=None):
        """Vote on review helpfulness."""
        review = self.get_object()
        
        # Prevent voting on own review
        if review.tenant == request.user:
            return Response({
                'error': _('You cannot vote on your own review')
            }, status=status.HTTP_400_BAD_REQUEST)
        
        serializer = ReviewHelpfulnessSerializer(
            data={'review': review.id, **request.data},
            context={'request': request}
        )
        
        if serializer.is_valid():
            vote = serializer.save()
            return Response({
                'message': _('Vote recorded successfully'),
                'is_helpful': vote.is_helpful,
                'helpful_votes': review.helpful_votes,
                'total_votes': review.total_votes
            })
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    @extend_schema(
        summary="Report review",
        description="Report a review for inappropriate content",
        request=ReviewReportSerializer,
        tags=["Reviews"]
    )
    def report(self, request, pk=None):
        """Report a review."""
        review = self.get_object()
        
        # Prevent reporting own review
        if review.tenant == request.user:
            return Response({
                'error': _('You cannot report your own review')
            }, status=status.HTTP_400_BAD_REQUEST)
        
        serializer = ReviewReportSerializer(
            data={'review': review.id, **request.data},
            context={'request': request}
        )
        
        if serializer.is_valid():
            serializer.save()
            return Response({
                'message': _('Review reported successfully. Our team will review it.')
            }, status=status.HTTP_201_CREATED)
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated])
    @extend_schema(
        summary="Get user's reviews",
        description="Get current user's reviews",
        tags=["Reviews"]
    )
    def my_reviews(self, request):
        """Get current user's reviews."""
        if request.user.user_type != 'tenant':
            return Response(
                {'error': _('Only tenants can access this endpoint')},
                status=status.HTTP_403_FORBIDDEN
            )
        
        reviews = Review.objects.filter(
            tenant=request.user
        ).select_related('rental')
        
        serializer = ReviewListSerializer(reviews, many=True, context={'request': request})
        return Response(serializer.data)


class LandlordResponseView(APIView):
    """
    View for landlord responses to reviews.
    """
    
    permission_classes = [IsAuthenticated]
    
    @extend_schema(
        summary="Respond to review",
        description="Landlord response to a review (Property owner only)",
        request=LandlordResponseSerializer,
        tags=["Reviews"]
    )
    def post(self, request, review_id):
        """Add landlord response to a review."""
        review = get_object_or_404(Review, id=review_id)
        
        # Check if user is the landlord of the reviewed property
        if request.user != review.rental.landlord:
            return Response({
                'error': _('Only the property owner can respond to reviews')
            }, status=status.HTTP_403_FORBIDDEN)
        
        # Check if already responded
        if review.landlord_response:
            return Response({
                'error': _('You have already responded to this review')
            }, status=status.HTTP_400_BAD_REQUEST)
        
        serializer = LandlordResponseSerializer(
            review,
            data=request.data,
            partial=True
        )
        
        if serializer.is_valid():
            serializer.save()
            return Response({
                'message': _('Response added successfully'),
                'review': ReviewDetailSerializer(review, context={'request': request}).data
            })
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# Function-based views
@extend_schema(
    summary="Get rental reviews",
    description="Get all reviews for a specific rental property",
    responses={200: ReviewListSerializer(many=True)},
    tags=["Reviews"]
)
@api_view(['GET'])
@permission_classes([AllowAny])
def rental_reviews(request, rental_id):
    """Get reviews for a specific rental."""
    rental = get_object_or_404(Rental, id=rental_id)
    
    reviews = Review.objects.filter(
        rental=rental,
        is_approved=True
    ).select_related('tenant').order_by('-created_at')
    
    serializer = ReviewListSerializer(reviews, many=True, context={'request': request})
    return Response(serializer.data)


@extend_schema(
    summary="Get rental review statistics",
    description="Get review statistics for a specific rental property",
    tags=["Reviews"]
)
@api_view(['GET'])
@permission_classes([AllowAny])
def rental_review_statistics(request, rental_id):
    """Get review statistics for a specific rental."""
    rental = get_object_or_404(Rental, id=rental_id)
    
    reviews = Review.objects.filter(
        rental=rental,
        is_approved=True
    )
    
    if not reviews.exists():
        return Response({
            'total_reviews': 0,
            'average_rating': 0,
            'verified_reviews': 0,
            'rating_distribution': {str(i): 0 for i in range(1, 6)},
            'average_cleanliness': 0,
            'average_location': 0,
            'average_value': 0,
            'average_landlord': 0,
            'recommendation_percentage': 0,
        })
    
    # Basic statistics
    stats = reviews.aggregate(
        total_reviews=Count('id'),
        average_rating=Avg('rating'),
        verified_reviews=Count('id', filter=Q(is_verified=True)),
        average_cleanliness=Avg('cleanliness_rating'),
        average_location=Avg('location_rating'),
        average_value=Avg('value_rating'),
        average_landlord=Avg('landlord_rating'),
    )
    
    # Rating distribution
    rating_distribution = {}
    for i in range(1, 6):
        count = reviews.filter(rating=i).count()
        rating_distribution[str(i)] = count
    
    # Recommendation percentage
    recommended = reviews.filter(would_recommend=True).count()
    total_with_recommendation = reviews.exclude(would_recommend__isnull=True).count()
    recommendation_percentage = (
        (recommended / total_with_recommendation * 100) 
        if total_with_recommendation > 0 else 0
    )
    
    stats.update({
        'rating_distribution': rating_distribution,
        'recommendation_percentage': round(recommendation_percentage, 1),
    })
    
    # Round averages
    for key in ['average_rating', 'average_cleanliness', 'average_location', 'average_value', 'average_landlord']:
        if stats[key]:
            stats[key] = round(float(stats[key]), 2)
        else:
            stats[key] = 0
    
    return Response(stats)


@extend_schema(
    summary="Get review statistics",
    description="Get platform-wide review statistics (Admin only)",
    tags=["Admin"]
)
@api_view(['GET'])
@permission_classes([IsAdminUser])
def review_statistics(request):
    """Get platform-wide review statistics for admin dashboard."""
    stats = Review.objects.aggregate(
        total_reviews=Count('id'),
        approved_reviews=Count('id', filter=Q(is_approved=True)),
        pending_reviews=Count('id', filter=Q(is_approved=False)),
        verified_reviews=Count('id', filter=Q(is_verified=True)),
        average_rating=Avg('rating'),
        total_reports=Count('reports', distinct=True),
        unresolved_reports=Count('reports', filter=Q(reports__is_resolved=False), distinct=True),
    )
    
    # Rating distribution
    rating_distribution = {}
    for i in range(1, 6):
        count = Review.objects.filter(rating=i, is_approved=True).count()
        rating_distribution[str(i)] = count
    
    stats['rating_distribution'] = rating_distribution
    
    # Monthly statistics (last 12 months)
    monthly_stats = []
    current_date = datetime.now().date()
    
    for i in range(12):
        month_start = current_date.replace(day=1) - timedelta(days=30*i)
        month_end = (month_start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        
        monthly_count = Review.objects.filter(
            created_at__date__range=[month_start, month_end]
        ).count()
        
        monthly_stats.append({
            'month': month_start.strftime('%Y-%m'),
            'reviews_created': monthly_count
        })
    
    stats['monthly_stats'] = list(reversed(monthly_stats))
    
    # Round averages
    if stats['average_rating']:
        stats['average_rating'] = round(float(stats['average_rating']), 2)
    else:
        stats['average_rating'] = 0
    
    return Response(stats)


@extend_schema(
    summary="Get recent reviews",
    description="Get recently added reviews across the platform",
    responses={200: ReviewListSerializer(many=True)},
    tags=["Reviews"]
)
@api_view(['GET'])
@permission_classes([AllowAny])
def recent_reviews(request):
    """Get recently added reviews."""
    reviews = Review.objects.filter(
        is_approved=True
    ).select_related('tenant', 'rental').order_by('-created_at')[:10]
    
    serializer = ReviewListSerializer(reviews, many=True, context={'request': request})
    return Response(serializer.data)


@extend_schema(
    summary="Get top-rated reviews",
    description="Get highest-rated reviews across the platform",
    responses={200: ReviewListSerializer(many=True)},
    tags=["Reviews"]
)
@api_view(['GET'])
@permission_classes([AllowAny])
def top_rated_reviews(request):
    """Get top-rated reviews."""
    reviews = Review.objects.filter(
        is_approved=True,
        rating__gte=4
    ).select_related('tenant', 'rental').order_by('-rating', '-helpful_votes')[:10]
    
    serializer = ReviewListSerializer(reviews, many=True, context={'request': request})
    return Response(serializer.data)


# Admin ViewSets
class AdminReviewViewSet(ModelViewSet):
    """
    Admin viewset for managing all reviews.
    """
    
    queryset = Review.objects.all().select_related('tenant', 'rental')
    serializer_class = AdminReviewSerializer
    permission_classes = [IsAdminUser]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['comment', 'tenant__email', 'rental__title']
    ordering_fields = ['created_at', 'rating', 'helpful_votes', 'is_verified']
    ordering = ['-created_at']
    
    def get_queryset(self):
        """Filter queryset based on admin parameters."""
        queryset = self.queryset
        
        # Filter by approval status
        is_approved = self.request.query_params.get('is_approved')
        if is_approved is not None:
            approved = is_approved.lower() == 'true'
            queryset = queryset.filter(is_approved=approved)
        
        # Filter by verification status
        is_verified = self.request.query_params.get('is_verified')
        if is_verified is not None:
            verified = is_verified.lower() == 'true'
            queryset = queryset.filter(is_verified=verified)
        
        # Filter by reported reviews
        has_reports = self.request.query_params.get('has_reports')
        if has_reports and has_reports.lower() == 'true':
            queryset = queryset.filter(reports__isnull=False).distinct()
        
        return queryset
    
    @action(detail=True, methods=['post'])
    @extend_schema(
        summary="Toggle review approval",
        description="Approve/disapprove review (Admin only)",
        tags=["Admin"]
    )
    def toggle_approval(self, request, pk=None):
        """Toggle review approval status."""
        review = self.get_object()
        review.is_approved = not review.is_approved
        review.save()
        
        return Response({
            'message': _('Review approval status updated successfully'),
            'is_approved': review.is_approved
        })
    
    @action(detail=True, methods=['post'])
    @extend_schema(
        summary="Toggle review verification",
        description="Verify/unverify review (Admin only)",
        tags=["Admin"]
    )
    def toggle_verification(self, request, pk=None):
        """Toggle review verification status."""
        review = self.get_object()
        review.is_verified = not review.is_verified
        review.save()
        
        return Response({
            'message': _('Review verification status updated successfully'),
            'is_verified': review.is_verified
        })
    
    @action(detail=True, methods=['patch'])
    @extend_schema(
        summary="Add moderation notes",
        description="Add moderation notes to review (Admin only)",
        tags=["Admin"]
    )
    def add_moderation_notes(self, request, pk=None):
        """Add moderation notes to review."""
        review = self.get_object()
        notes = request.data.get('moderation_notes', '')
        
        if not notes.strip():
            return Response({
                'error': _('Moderation notes cannot be empty')
            }, status=status.HTTP_400_BAD_REQUEST)
        
        review.moderation_notes = notes
        review.save()
        
        return Response({
            'message': _('Moderation notes added successfully'),
            'moderation_notes': review.moderation_notes
        })


class AdminReviewReportViewSet(ModelViewSet):
    """
    Admin viewset for managing review reports.
    """
    
    queryset = ReviewReport.objects.all().select_related('review', 'reporter')
    serializer_class = AdminReviewReportSerializer
    permission_classes = [IsAdminUser]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['reason', 'description', 'reporter__email']
    ordering_fields = ['created_at', 'is_resolved']
    ordering = ['-created_at']
    
    def get_queryset(self):
        """Filter queryset based on resolution status."""
        queryset = self.queryset
        
        # Filter by resolution status
        is_resolved = self.request.query_params.get('is_resolved')
        if is_resolved is not None:
            resolved = is_resolved.lower() == 'true'
            queryset = queryset.filter(is_resolved=resolved)
        
        return queryset
    
    @action(detail=True, methods=['post'])
    @extend_schema(
        summary="Resolve report",
        description="Mark report as resolved with action taken (Admin only)",
        tags=["Admin"]
    )
    def resolve(self, request, pk=None):
        """Resolve a review report."""
        report = self.get_object()
        
        if report.is_resolved:
            return Response({
                'error': _('Report is already resolved')
            }, status=status.HTTP_400_BAD_REQUEST)
        
        admin_action = request.data.get('admin_action', '')
        if not admin_action.strip():
            return Response({
                'error': _('Admin action description is required')
            }, status=status.HTTP_400_BAD_REQUEST)
        
        from django.utils import timezone
        
        report.is_resolved = True
        report.admin_action = admin_action
        report.resolved_by = request.user
        report.resolved_at = timezone.now()
        report.save()
        
        return Response({
            'message': _('Report resolved successfully'),
            'admin_action': report.admin_action
        })
    
    @action(detail=True, methods=['post'])
    @extend_schema(
        summary="Dismiss report",
        description="Dismiss report without action (Admin only)",
        tags=["Admin"]
    )
    def dismiss(self, request, pk=None):
        """Dismiss a review report."""
        report = self.get_object()
        
        from django.utils import timezone
        
        report.is_resolved = True
        report.admin_action = "Report dismissed - no action required"
        report.resolved_by = request.user
        report.resolved_at = timezone.now()
        report.save()
        
        return Response({
            'message': _('Report dismissed successfully')
        })