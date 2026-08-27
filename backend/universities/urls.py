"""URL configuration for the universities app."""

from django.urls import path

from .admin_api import UniversityPolicyView
from .views import tenant_config

app_name = "universities"

urlpatterns = [
    path("policy/", UniversityPolicyView.as_view(), name="policy"),
    path("config/", tenant_config, name="tenant-config"),
]
