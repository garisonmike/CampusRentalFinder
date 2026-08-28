"""
Property and unit routes.

Two halves, deliberately adjacent so the difference is visible: everything
under `PropertyListView` and its siblings is **public read** (ADR-002), and
everything under `manage/` is the landlord and caretaker write surface
(ADR-003).

The management routes sit under their own prefix rather than adding methods to
the read endpoints. A POST hanging off the public detail URL inherits that
view's permissions by default, which is exactly the mistake
`test_every_api_view_declares_its_permission_classes` exists to catch -- and
prefixing makes it visible in a URL listing rather than only in a class body.
"""

from django.urls import path

from .views import PropertyDetailView, PropertyListView, UnitDetailView
from .write_views import (
    ManagedPropertyListView,
    PropertyCreateView,
    PropertyPublicationView,
    PropertyUpdateView,
    UnitAvailabilityView,
    UnitCreateView,
    UnitPhotoDetailView,
    UnitPhotoListView,
    UnitPhotoOrderView,
    UnitUpdateView,
    UnitVacancyView,
)

app_name = "properties"

urlpatterns = [
    # --- public read ---
    path("", PropertyListView.as_view(), name="property-list"),
    path("units/<int:pk>/", UnitDetailView.as_view(), name="unit-detail"),
    # --- management ---
    path("manage/", ManagedPropertyListView.as_view(), name="managed-list"),
    path("manage/new/", PropertyCreateView.as_view(), name="property-create"),
    path("manage/<slug:slug>/", PropertyUpdateView.as_view(), name="property-update"),
    path(
        "manage/<slug:slug>/publication/",
        PropertyPublicationView.as_view(),
        name="property-publication",
    ),
    path("manage/<slug:slug>/units/", UnitCreateView.as_view(), name="unit-create"),
    path("manage/<slug:slug>/units/<int:pk>/", UnitUpdateView.as_view(), name="unit-update"),
    path(
        "manage/<slug:slug>/units/<int:pk>/vacancy/",
        UnitVacancyView.as_view(),
        name="unit-vacancy",
    ),
    path(
        "manage/<slug:slug>/units/<int:pk>/availability/",
        UnitAvailabilityView.as_view(),
        name="unit-availability",
    ),
    path(
        "manage/<slug:slug>/units/<int:pk>/photos/",
        UnitPhotoListView.as_view(),
        name="unit-photos",
    ),
    path(
        "manage/<slug:slug>/units/<int:pk>/photos/order/",
        UnitPhotoOrderView.as_view(),
        name="unit-photo-order",
    ),
    path(
        "manage/<slug:slug>/units/<int:pk>/photos/<int:photo_id>/",
        UnitPhotoDetailView.as_view(),
        name="unit-photo-detail",
    ),
    # Last: a bare `<slug:slug>/` would otherwise swallow `manage/`.
    path("<slug:slug>/", PropertyDetailView.as_view(), name="property-detail"),
]
