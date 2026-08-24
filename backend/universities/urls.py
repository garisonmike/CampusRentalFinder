"""URL configuration for the universities app."""

from django.urls import path

from .views import tenant_config

app_name = "universities"

urlpatterns = [
    path("config/", tenant_config, name="tenant-config"),
]
