"""Review, response and rating routes (ADR-004)."""

from django.urls import path

from .views import (
    ManagedReviewListView,
    PropertyRatingView,
    PropertyReviewListView,
    ReviewCreateView,
    ReviewEditView,
    ReviewResponseCreateView,
    UnitRatingView,
)

app_name = "reviews"

urlpatterns = [
    path("", ReviewCreateView.as_view(), name="review-create"),
    # Before `<int:pk>/`, which would otherwise never match a word but does
    # match nothing useful either -- kept ahead so the intent is obvious.
    path("manage/", ManagedReviewListView.as_view(), name="managed-reviews"),
    path("<int:pk>/", ReviewEditView.as_view(), name="review-edit"),
    path("<int:pk>/response/", ReviewResponseCreateView.as_view(), name="review-response"),
    path(
        "properties/<slug:slug>/",
        PropertyReviewListView.as_view(),
        name="property-reviews",
    ),
    path(
        "properties/<slug:slug>/rating/",
        PropertyRatingView.as_view(),
        name="property-rating",
    ),
    path("units/<int:pk>/rating/", UnitRatingView.as_view(), name="unit-rating"),
]
