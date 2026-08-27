"""Review, response and rating routes (ADR-004)."""

from django.urls import path

from .views import (
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
