"""Saved-property and inquiry routes (ADR-004 §1.1)."""

from django.urls import path

from .views import (
    InquiryCloseView,
    InquiryListView,
    InquiryRespondView,
    SavedPropertyDeleteView,
    SavedPropertyListView,
)

app_name = "engagement"

urlpatterns = [
    path("saved/", SavedPropertyListView.as_view(), name="saved-list"),
    path("saved/<slug:slug>/", SavedPropertyDeleteView.as_view(), name="saved-delete"),
    path("inquiries/", InquiryListView.as_view(), name="inquiry-list"),
    path("inquiries/<int:pk>/respond/", InquiryRespondView.as_view(), name="inquiry-respond"),
    path("inquiries/<int:pk>/close/", InquiryCloseView.as_view(), name="inquiry-close"),
]
