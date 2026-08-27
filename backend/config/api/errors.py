"""
One error shape, for every endpoint.

The frontend branches on this. If it varies by endpoint the frontend either
grows a per-endpoint parser or — far more likely — parses the shape it saw
first and silently mishandles the rest, showing a blank error box for the cases
that matter most.

Every error response is::

    {
      "error": {
        "code": "validation_failed",
        "message": "The stay is not long enough to review.",
        "field_errors": {"tenancy": ["A stay must reach 30 days..."]},
        "request_id": "b1f2..."
      }
    }

``field_errors`` is always present and may be empty. ``message`` is always a
single human-readable sentence, never a list and never a dict, so a client can
render it without inspecting its type first.

**The service layer's exceptions are first-class here.** There are twenty-five
named ones, each raised because a specific thing is not allowed, and every one
of them carries a reason worth showing a user. Flattening them into a generic
400 would throw away the part that took the work.
"""

from __future__ import annotations

from typing import Any

import structlog
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions as drf
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from .request_id import get_request_id

logger = structlog.get_logger("campusrental.api")


class ErrorCode:
    """The stable vocabulary a client may branch on.

    Codes are **contract**: renaming one is a breaking change, and adding one
    is not. Deliberately coarse — a client that needs to distinguish two cases
    inside a code reads ``field_errors``, which is where the specificity lives.
    """

    VALIDATION_FAILED = "validation_failed"
    NOT_AUTHENTICATED = "not_authenticated"
    PERMISSION_DENIED = "permission_denied"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    THROTTLED = "throttled"
    CONFLICT = "conflict"
    SERVER_ERROR = "server_error"

    #: Domain-specific, mapped from named service exceptions. Each exists
    #: because a client can *do* something different about it.
    VERIFICATION_REQUIRED = "verification_required"
    RATE_LIMITED = "rate_limited"
    NOT_REVIEWABLE = "not_reviewable"
    REVIEW_FROZEN = "review_frozen"
    DOCUMENT_REJECTED = "document_rejected"
    ERASURE_BLOCKED = "erasure_blocked"


#: Named service exception -> (code, HTTP status).
#:
#: Keyed by **class name** rather than by imported class, deliberately: importing
#: twenty-five exception classes here would make `config` depend on every app,
#: and `config` is imported by all of them. The name is matched against the
#: exception's own class and its bases, so a subclass inherits its parent's
#: mapping.
SERVICE_EXCEPTION_MAP: dict[str, tuple[str, int]] = {
    # Verification gating (ADR-003). 403 rather than 400: the request was
    # well-formed and the caller is simply not allowed to do it yet.
    "VerificationRequiredError": (ErrorCode.VERIFICATION_REQUIRED, 403),
    "VerificationMethodNotOfferedError": (ErrorCode.VALIDATION_FAILED, 400),
    "EmailDomainNotAcceptedError": (ErrorCode.VALIDATION_FAILED, 400),
    "InvalidVerificationTokenError": (ErrorCode.VALIDATION_FAILED, 400),
    # Rate limits raised by the service layer rather than by a DRF throttle.
    # Same code as a throttle so a client has one thing to handle.
    "VerificationRateLimitError": (ErrorCode.RATE_LIMITED, 429),
    "ClaimRateLimitExceededError": (ErrorCode.RATE_LIMITED, 429),
    "InquiryRateLimitError": (ErrorCode.RATE_LIMITED, 429),
    "ResubmissionLimitError": (ErrorCode.RATE_LIMITED, 429),
    # Documents.
    "DocumentTypeNotAllowedError": (ErrorCode.DOCUMENT_REJECTED, 400),
    "DocumentTooLargeError": (ErrorCode.DOCUMENT_REJECTED, 413),
    "DocumentUnavailableError": (ErrorCode.NOT_FOUND, 404),
    # Reviews.
    "TenancyNotReviewableError": (ErrorCode.NOT_REVIEWABLE, 409),
    "ReviewFrozenError": (ErrorCode.REVIEW_FROZEN, 409),
    # State-machine refusals. 409, because the request is valid and the
    # resource is in the wrong state -- which is a different fix for the caller
    # than "you sent the wrong thing".
    "ApplicationNotDecidableError": (ErrorCode.CONFLICT, 409),
    "DisputeNotOpenError": (ErrorCode.CONFLICT, 409),
    "InquiryNotAnswerableError": (ErrorCode.CONFLICT, 409),
    "PropertyNotContactableError": (ErrorCode.CONFLICT, 409),
    "OverlappingTenancyError": (ErrorCode.CONFLICT, 409),
    "AlreadyErasedError": (ErrorCode.CONFLICT, 409),
    "ActiveTenanciesError": (ErrorCode.ERASURE_BLOCKED, 409),
    # Configuration guards.
    "UnsafeSignupPolicyError": (ErrorCode.VALIDATION_FAILED, 400),
    "PropertyNotPublishableError": (ErrorCode.VALIDATION_FAILED, 400),
    # An unroutable dispute is our bug, not the caller's: the transition table
    # is missing an entry. 500, and it should page someone.
    "UnroutableDisputeError": (ErrorCode.SERVER_ERROR, 500),
}

#: DRF exception -> code. Status comes from the exception itself.
DRF_EXCEPTION_CODES: dict[type[Exception], str] = {
    drf.ValidationError: ErrorCode.VALIDATION_FAILED,
    drf.NotAuthenticated: ErrorCode.NOT_AUTHENTICATED,
    drf.AuthenticationFailed: ErrorCode.NOT_AUTHENTICATED,
    drf.PermissionDenied: ErrorCode.PERMISSION_DENIED,
    drf.NotFound: ErrorCode.NOT_FOUND,
    drf.MethodNotAllowed: ErrorCode.METHOD_NOT_ALLOWED,
    drf.Throttled: ErrorCode.THROTTLED,
}


def _first_message(detail: Any) -> str:
    """One sentence out of DRF's arbitrarily nested detail structure.

    DRF hands back a string, a list, a dict, or a tree of those. `message` is
    contract-guaranteed to be a plain string, so this flattens depth-first and
    takes the first leaf -- the full structure is preserved in `field_errors`
    and nothing is lost.
    """
    if isinstance(detail, dict):
        for value in detail.values():
            found = _first_message(value)
            if found:
                return found
        return ""
    if isinstance(detail, list | tuple):
        for item in detail:
            found = _first_message(item)
            if found:
                return found
        return ""
    return str(detail)


def _field_errors(detail: Any) -> dict[str, list[str]]:
    """Per-field messages, always a dict of lists.

    Non-field errors land under DRF's own ``non_field_errors`` key rather than
    being dropped or hoisted, so a form can render them in one predictable
    place.
    """
    if isinstance(detail, dict):
        return {
            str(field): [
                str(item) for item in (value if isinstance(value, list | tuple) else [value])
            ]
            for field, value in detail.items()
        }
    if isinstance(detail, list | tuple) and detail:
        return {"non_field_errors": [str(item) for item in detail]}
    return {}


def error_body(
    *, code: str, message: str, field_errors: dict[str, list[str]] | None = None
) -> dict[str, Any]:
    """The one shape. Nothing else may construct an error response."""
    return {
        "error": {
            "code": code,
            "message": message,
            "field_errors": field_errors or {},
            "request_id": get_request_id(),
        }
    }


def _lookup_service_exception(exc: Exception) -> tuple[str, int] | None:
    """Match against the exception's class and its bases.

    Walking the MRO means a subclass added later inherits its parent's mapping
    instead of silently falling through to a generic 400 -- which is the
    failure that would be least visible, because it still returns *an* error.
    """
    for klass in type(exc).__mro__:
        mapped = SERVICE_EXCEPTION_MAP.get(klass.__name__)
        if mapped is not None:
            return mapped
    return None


def _from_django_validation_error(exc: DjangoValidationError) -> tuple[str, dict[str, list[str]]]:
    """Django's ValidationError, which the service layer raises, not DRF's."""
    if hasattr(exc, "message_dict"):
        field_errors = {
            field: [str(message) for message in messages]
            for field, messages in exc.message_dict.items()
        }
    else:
        field_errors = {"non_field_errors": [str(message) for message in exc.messages]}

    message = _first_message(field_errors) or "The request could not be completed."
    return message, field_errors


def api_exception_handler(exc: Exception, context: dict) -> Response | None:
    """The single exception handler for the whole API.

    Order matters: the named service exceptions are checked **before** the
    generic `ValidationError` branch, because most of them subclass it and
    would otherwise be flattened into an anonymous 400 -- losing exactly the
    distinction each one was defined to make.
    """
    view = context.get("view")
    view_name = type(view).__name__ if view else "unknown"

    # 1. Named service exceptions, mapped by class name up the MRO.
    mapped = _lookup_service_exception(exc)
    if mapped is not None:
        code, status = mapped
        if isinstance(exc, DjangoValidationError):
            message, field_errors = _from_django_validation_error(exc)
        else:
            message, field_errors = str(exc), {}

        if status >= 500:
            logger.error(
                "api_service_error", view=view_name, code=code, error=str(exc), exc_info=exc
            )

        return Response(
            error_body(code=code, message=message, field_errors=field_errors), status=status
        )

    # 2. Django's own exceptions, which DRF does not translate on its own when
    #    raised from a service function rather than a serializer.
    if isinstance(exc, DjangoValidationError):
        message, field_errors = _from_django_validation_error(exc)
        return Response(
            error_body(
                code=ErrorCode.VALIDATION_FAILED, message=message, field_errors=field_errors
            ),
            status=400,
        )

    if isinstance(exc, DjangoPermissionDenied):
        return Response(
            error_body(
                code=ErrorCode.PERMISSION_DENIED,
                message=str(exc) or "You do not have permission to perform this action.",
            ),
            status=403,
        )

    if isinstance(exc, Http404):
        return Response(error_body(code=ErrorCode.NOT_FOUND, message="Not found."), status=404)

    # 3. DRF's exceptions. Delegate first so DRF sets auth headers and the
    #    Retry-After on a throttle, then rewrite the body in place.
    response = drf_exception_handler(exc, context)
    if response is None:
        # Unhandled. DRF re-raises so Django's 500 machinery runs; the body a
        # client sees is produced by the handler500, not here.
        return None

    code = ErrorCode.SERVER_ERROR
    for klass, mapped_code in DRF_EXCEPTION_CODES.items():
        if isinstance(exc, klass):
            code = mapped_code
            break

    detail = getattr(exc, "detail", response.data)
    response.data = error_body(
        code=code, message=_first_message(detail), field_errors=_field_errors(detail)
    )
    return response
