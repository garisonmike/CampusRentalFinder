"""
URL configuration for rentals app.

This module defines URL patterns for rental property management,
search, favorites, inquiries, and admin operations.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    RentalViewSet,
    RentalImageViewSet,
    RentalInquiryViewSet,
    AdminRentalViewSet,
    featured_rentals,
    recent_rentals,
    rental_statistics,
)

# Create router for viewsets
router = DefaultRouter()
router.register(r'properties', RentalViewSet, basename='rentals')
router.register(r'images', RentalImageViewSet, basename='rental-images')
router.register(r'inquiries', RentalInquiryViewSet, basename='rental-inquiries')
router.register(r'admin/properties', AdminRentalViewSet, basename='admin-rentals')

app_name = 'rentals'

urlpatterns = [
    # Featured and recent rentals (public endpoints)
    path('featured/', featured_rentals, name='featured-rentals'),
    path('recent/', recent_rentals, name='recent-rentals'),
    
    # Admin statistics
    path('admin/statistics/', rental_statistics, name='rental-statistics'),
    
    # Include router URLs
    path('', include(router.urls)),
]