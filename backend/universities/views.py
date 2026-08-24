"""Public tenant configuration (ADR-005)."""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response

from .serializers import TenantConfigSerializer


@extend_schema(
    summary="Tenant configuration",
    description=(
        "Branding and identity for the university serving this host. "
        "Unauthenticated: the login page itself has to be branded, and it "
        "renders before any token exists. Returns 404 on a host that resolves "
        "no tenant, so the client falls back to its neutral palette."
    ),
    responses={200: TenantConfigSerializer, 404: None},
    tags=["Tenant"],
)
@api_view(["GET"])
@permission_classes([AllowAny])
def tenant_config(request: Request) -> Response:
    """Return the active tenant's public configuration.

    Read-only and unauthenticated by design. The React app fetches this before
    first paint and applies the tokens to :root, so a slow or failing response
    degrades to the neutral palette rather than to an error.
    """
    university = getattr(request, "university", None)

    if university is None:
        return Response(
            {"detail": "No university is served on this host."},
            status=status.HTTP_404_NOT_FOUND,
        )

    return Response(TenantConfigSerializer(university).data)
