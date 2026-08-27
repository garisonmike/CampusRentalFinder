"""Property and unit routes. Public read (ADR-002)."""

from django.urls import path

from .views import PropertyDetailView, PropertyListView, UnitDetailView

app_name = "properties"

urlpatterns = [
    path("", PropertyListView.as_view(), name="property-list"),
    path("units/<int:pk>/", UnitDetailView.as_view(), name="unit-detail"),
    path("<slug:slug>/", PropertyDetailView.as_view(), name="property-detail"),
]
