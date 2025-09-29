"""
URL configuration for reviews app.

This module defines URL patterns for review management, 
helpfulness voting, reporting, and admin operations.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    ReviewViewSet,
    LandlordResponseView,
    AdminReviewViewSet,
    AdminReviewReportViewSet,
    rental_reviews,
    rental_review_statistics,
    review_statistics,
    recent_reviews,
    top_rated_reviews,
)

# Create router for viewsets
router = DefaultRouter()
router.register(r'', ReviewViewSet, basename='reviews')
router.register(r'admin/reviews', AdminReviewViewSet, basename='admin-reviews')
router.register(r'admin/reports', AdminReviewReportViewSet, basename='admin-reports')

app_name = 'reviews'

urlpatterns = [
    # Rental-specific review endpoints
    path('rental/<int:rental_id>/', rental_reviews, name='rental-reviews'),
    path('rental/<int:rental_id>/statistics/', rental_review_statistics, name='rental-review-statistics'),
    
    # Landlord response endpoint
    path('<int:review_id>/response/', LandlordResponseView.as_view(), name='landlord-response'),
    
    # Public review endpoints
    path('recent/', recent_reviews, name='recent-reviews'),
    path('top-rated/', top_rated_reviews, name='top-rated-reviews'),
    
    # Admin statistics
    path('admin/statistics/', review_statistics, name='review-statistics'),
    
    # Include router URLs
    path('', include(router.urls)),
]