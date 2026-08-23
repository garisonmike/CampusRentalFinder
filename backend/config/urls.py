"""Root URL configuration."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from config.health import health_live, health_ready

api_v1_patterns = [
    path("auth/", include("accounts.urls")),
    path("rentals/", include("rentals.urls")),
    path("reviews/", include("reviews.urls")),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include(api_v1_patterns)),
    # API documentation
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    # Probes
    path("health/live/", health_live, name="health-live"),
    path("health/ready/", health_ready, name="health-ready"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

admin.site.site_header = "CampusRentalFinder Administration"
admin.site.site_title = "CampusRentalFinder Admin"
admin.site.index_title = "Platform administration"
