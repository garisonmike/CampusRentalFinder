"""Application, claim, tenancy and dispute routes (ADR-004)."""

from django.urls import path

from .views import (
    ApplicationAcceptView,
    ApplicationListView,
    ApplicationRejectView,
    ApplicationWithdrawView,
    ClaimAcceptCorrectionView,
    ClaimAcceptCounterView,
    ClaimConfirmView,
    ClaimCounterView,
    ClaimDisputeView,
    ClaimListView,
    ClaimRejectCounterView,
    DisputeQueueView,
    DisputeResolveView,
    TenancyListView,
)

app_name = "tenancies"

urlpatterns = [
    path("applications/", ApplicationListView.as_view(), name="application-list"),
    path(
        "applications/<int:pk>/accept/", ApplicationAcceptView.as_view(), name="application-accept"
    ),
    path(
        "applications/<int:pk>/reject/", ApplicationRejectView.as_view(), name="application-reject"
    ),
    path(
        "applications/<int:pk>/withdraw/",
        ApplicationWithdrawView.as_view(),
        name="application-withdraw",
    ),
    path("", TenancyListView.as_view(), name="tenancy-list"),
    path("claims/", ClaimListView.as_view(), name="claim-list"),
    path("claims/<int:pk>/confirm/", ClaimConfirmView.as_view(), name="claim-confirm"),
    path("claims/<int:pk>/dispute/", ClaimDisputeView.as_view(), name="claim-dispute"),
    path(
        "claims/<int:pk>/accept-correction/",
        ClaimAcceptCorrectionView.as_view(),
        name="claim-accept-correction",
    ),
    path("claims/<int:pk>/counter/", ClaimCounterView.as_view(), name="claim-counter"),
    path(
        "claims/<int:pk>/accept-counter/",
        ClaimAcceptCounterView.as_view(),
        name="claim-accept-counter",
    ),
    path(
        "claims/<int:pk>/reject-counter/",
        ClaimRejectCounterView.as_view(),
        name="claim-reject-counter",
    ),
    path("disputes/", DisputeQueueView.as_view(), name="dispute-queue"),
    path("disputes/<int:pk>/resolve/", DisputeResolveView.as_view(), name="dispute-resolve"),
]
