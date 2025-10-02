"""
URL configuration for rental_platform project.
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.http import JsonResponse
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

# API URL patterns
api_v1_patterns = [
    # Authentication endpoints
    path('auth/', include('accounts.urls')),
    
    # Rental endpoints
    path('rentals/', include('rentals.urls')),
    
    # Review endpoints  
    path('reviews/', include('reviews.urls')),
]

# Simple health check view
def health_check(request):
    return JsonResponse({"status": "ok"})

urlpatterns = [
    # Admin panel
    path('admin/', admin.site.urls),
    
    # API v1 routes
    path('api/v1/', include(api_v1_patterns)),
    
    # API Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
    
    # Health check endpoint
    path('health/', health_check, name="health-check"),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)

# Custom admin site headers
admin.site.site_header = "Rental Platform Administration"
admin.site.site_title = "Rental Platform Admin"
admin.site.index_title = "Welcome to Rental Platform Administration"
