"""
The error contract (`config/api/errors.py`).

Every endpoint returns the same shape. The frontend branches on it, and if it
varies the frontend will parse whichever variant it met first and show a blank
box for the rest — which is exactly the cases that matter, because a validation
failure a user can fix is the one that must render.

These tests drive a throwaway view rather than a real endpoint on purpose: the
contract has to hold for *any* view, and pinning it to a real one would mean
re-asserting it at every endpoint added later.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from django.test import override_settings
from django.urls import path
from rest_framework import exceptions as drf
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.test import APIClient
from rest_framework.throttling import SimpleRateThrottle

from config.api.errors import ErrorCode, error_body
from config.api.request_id import RESPONSE_HEADER
from reviews.services import TenancyNotReviewableError, VerificationRequiredError
from tenancies.services import DisputeNotOpenError

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# A view that raises whatever the test asks for
# ---------------------------------------------------------------------------


RAISERS = {
    "drf_validation": lambda: drf.ValidationError({"rating": ["Must be between 1 and 5."]}),
    "drf_validation_list": lambda: drf.ValidationError(["Something was wrong."]),
    "drf_permission": lambda: drf.PermissionDenied("Not your property."),
    "drf_not_found": lambda: drf.NotFound(),
    "drf_not_authenticated": lambda: drf.NotAuthenticated(),
    "drf_method": lambda: drf.MethodNotAllowed("PATCH"),
    "django_validation": lambda: DjangoValidationError({"tenancy": ["Too short."]}),
    "django_validation_bare": lambda: DjangoValidationError("Just a message."),
    "django_permission": lambda: DjangoPermissionDenied("Nope."),
    "http_404": lambda: Http404(),
    "service_not_reviewable": lambda: TenancyNotReviewableError(
        {"tenancy": ["A stay must reach 30 days before it can be reviewed."]}
    ),
    "service_verification": lambda: VerificationRequiredError(
        {"verification": ["Your university asks students to verify before reviewing."]}
    ),
    "service_conflict": lambda: DisputeNotOpenError({"status": ["Not escalated."]}),
}


@api_view(["GET"])
@permission_classes([AllowAny])
def boom(request, kind):
    raise RAISERS[kind]()


class AlwaysThrottled(SimpleRateThrottle):
    """Rejects everything, without needing a configured rate.

    `SimpleRateThrottle.__init__` looks the scope's rate up in settings, so a
    throttle used only to exercise the error shape has to supply its own.
    """

    scope = "test"

    def get_rate(self):
        return "1/min"

    def get_cache_key(self, request, view):
        return "always"

    def allow_request(self, request, view):
        raise drf.Throttled(wait=42)


@api_view(["GET"])
@permission_classes([AllowAny])
@throttle_classes([AlwaysThrottled])
def throttled(request):  # pragma: no cover - the throttle raises first
    return Response({"ok": True})


@api_view(["GET"])
@permission_classes([AllowAny])
def fine(request):
    return Response({"ok": True})


urlpatterns = [
    path("boom/<str:kind>/", boom),
    path("throttled/", throttled),
    path("fine/", fine),
]


@pytest.fixture
def api():
    with override_settings(ROOT_URLCONF=__name__):
        yield APIClient()


def error_of(response) -> dict:
    assert "error" in response.json(), response.json()
    return response.json()["error"]


# ---------------------------------------------------------------------------
# The shape itself
# ---------------------------------------------------------------------------


class TestTheShapeIsInvariant:
    """Every key present, every time, with the same types.

    A client that has to check whether `field_errors` exists before reading it
    will forget on one branch, and that branch will be an error path nobody
    tests by hand.
    """

    @pytest.mark.parametrize("kind", sorted(RAISERS))
    def test_every_error_has_the_four_keys(self, api, kind):
        error = error_of(api.get(f"/boom/{kind}/"))

        assert set(error) == {"code", "message", "field_errors", "request_id"}

    @pytest.mark.parametrize("kind", sorted(RAISERS))
    def test_message_is_always_a_plain_string(self, api, kind):
        """DRF hands back strings, lists, dicts and trees of those. A client
        must be able to render `message` without inspecting its type."""
        error = error_of(api.get(f"/boom/{kind}/"))

        assert isinstance(error["message"], str)
        assert error["message"]

    @pytest.mark.parametrize("kind", sorted(RAISERS))
    def test_field_errors_is_always_a_dict_of_lists_of_strings(self, api, kind):
        error = error_of(api.get(f"/boom/{kind}/"))

        assert isinstance(error["field_errors"], dict)
        for field, messages in error["field_errors"].items():
            assert isinstance(field, str)
            assert isinstance(messages, list)
            assert all(isinstance(message, str) for message in messages)

    @pytest.mark.parametrize("kind", sorted(RAISERS))
    def test_every_error_carries_a_request_id(self, api, kind):
        error = error_of(api.get(f"/boom/{kind}/"))

        assert error["request_id"]


# ---------------------------------------------------------------------------
# The four the brief names
# ---------------------------------------------------------------------------


class TestAValidationFailure:
    def test_it_is_a_400_with_the_field_named(self, api):
        response = api.get("/boom/drf_validation/")

        assert response.status_code == 400
        error = error_of(response)
        assert error["code"] == ErrorCode.VALIDATION_FAILED
        assert error["field_errors"] == {"rating": ["Must be between 1 and 5."]}

    def test_a_non_field_error_lands_under_a_predictable_key(self, api):
        """So a form has one place to render errors that belong to no field,
        rather than dropping them or hoisting them into `message` only."""
        error = error_of(api.get("/boom/drf_validation_list/"))

        assert error["field_errors"] == {"non_field_errors": ["Something was wrong."]}

    def test_a_service_layer_validation_error_keeps_its_field(self, api):
        """The service layer raises Django's ValidationError, not DRF's. If the
        handler did not translate it, this would be a 500."""
        response = api.get("/boom/django_validation/")

        assert response.status_code == 400
        assert error_of(response)["field_errors"] == {"tenancy": ["Too short."]}

    def test_a_bare_django_validation_error_still_has_a_message(self, api):
        error = error_of(api.get("/boom/django_validation_bare/"))

        assert error["message"] == "Just a message."


class TestAPermissionDenial:
    def test_drf_denial_is_403(self, api):
        response = api.get("/boom/drf_permission/")

        assert response.status_code == 403
        assert error_of(response)["code"] == ErrorCode.PERMISSION_DENIED

    def test_djangos_own_denial_is_also_403(self, api):
        """`PermissionDenied` raised from a service function rather than a
        DRF permission class. DRF does not translate this one on its own."""
        response = api.get("/boom/django_permission/")

        assert response.status_code == 403
        assert error_of(response)["code"] == ErrorCode.PERMISSION_DENIED

    def test_unauthenticated_is_distinct_from_forbidden(self, api):
        """ "Log in" and "you may not do this" are different next steps, and a
        client that conflates them shows a login box to someone already logged
        in."""
        error = error_of(api.get("/boom/drf_not_authenticated/"))

        assert error["code"] == ErrorCode.NOT_AUTHENTICATED


class TestAThrottleRejection:
    def test_it_is_429_with_the_throttled_code(self, api):
        response = api.get("/throttled/")

        assert response.status_code == 429
        assert error_of(response)["code"] == ErrorCode.THROTTLED

    def test_retry_after_survives_the_rewrite(self, api):
        """The handler rewrites the body but must not lose the header DRF set.
        Without it a client cannot back off correctly and will hammer."""
        response = api.get("/throttled/")

        assert response.headers.get("Retry-After") == "42"

    def test_the_message_says_how_long(self, api):
        assert "42" in error_of(api.get("/throttled/"))["message"]


class TestA404:
    def test_drf_not_found(self, api):
        response = api.get("/boom/drf_not_found/")

        assert response.status_code == 404
        assert error_of(response)["code"] == ErrorCode.NOT_FOUND

    def test_djangos_http404_gets_the_same_shape(self, api):
        """`get_object_or_404` raises this, so it is the common path."""
        response = api.get("/boom/http_404/")

        assert response.status_code == 404
        assert error_of(response)["code"] == ErrorCode.NOT_FOUND
        assert set(error_of(response)) == {
            "code",
            "message",
            "field_errors",
            "request_id",
        }


# ---------------------------------------------------------------------------
# Service exceptions keep their meaning
# ---------------------------------------------------------------------------


class TestServiceExceptionsAreNotFlattened:
    """Twenty-five named exceptions, each defined because a specific thing is
    not allowed. Flattening them into an anonymous 400 throws away the part
    that took the work.
    """

    def test_a_not_reviewable_stay_is_409_not_400(self, api):
        """The request is valid; the resource is in the wrong state. That is a
        different fix for the caller than "you sent the wrong thing"."""
        response = api.get("/boom/service_not_reviewable/")

        assert response.status_code == 409
        assert error_of(response)["code"] == ErrorCode.NOT_REVIEWABLE

    def test_gating_is_403_not_400(self, api):
        """The caller is not allowed to do this *yet*. A 400 would tell the
        client to fix its payload, which will not help."""
        response = api.get("/boom/service_verification/")

        assert response.status_code == 403
        assert error_of(response)["code"] == ErrorCode.VERIFICATION_REQUIRED

    def test_a_state_machine_refusal_is_409(self, api):
        response = api.get("/boom/service_conflict/")

        assert response.status_code == 409
        assert error_of(response)["code"] == ErrorCode.CONFLICT

    def test_the_reason_reaches_the_user(self, api):
        """Each of these carries a sentence written for a person. It must not
        be replaced by a generic one on the way out."""
        error = error_of(api.get("/boom/service_not_reviewable/"))

        assert "30 days" in error["message"]

    def test_subclasses_inherit_their_parents_mapping(self):
        """Matched up the MRO, so an exception added later as a subclass gets
        its parent's status instead of falling through to an anonymous 400 --
        which would still return *an* error and therefore go unnoticed."""
        from config.api.errors import _lookup_service_exception

        class MoreSpecificError(TenancyNotReviewableError):
            pass

        assert _lookup_service_exception(MoreSpecificError("nope")) == (
            ErrorCode.NOT_REVIEWABLE,
            409,
        )

    def test_every_mapped_name_is_a_real_exception(self):
        """A typo in the map is a mapping that never fires, and the symptom is
        a generic 400 rather than an error -- invisible without this."""
        import importlib
        import inspect

        from config.api.errors import SERVICE_EXCEPTION_MAP

        defined = set()
        for module_path in (
            "accounts.documents",
            "accounts.privacy",
            "accounts.retention",
            "accounts.verification",
            "engagement.services",
            "properties.services",
            "reviews.services",
            "tenancies.services",
            "universities.services",
        ):
            module = importlib.import_module(module_path)
            defined |= {
                name
                for name, obj in vars(module).items()
                if inspect.isclass(obj) and issubclass(obj, Exception)
            }

        unknown = sorted(set(SERVICE_EXCEPTION_MAP) - defined)

        assert not unknown, f"mapped but never raised anywhere: {unknown}"

    def test_every_service_exception_is_mapped(self):
        """The other direction. An unmapped one becomes a 500 on a path a user
        can trigger, which is the worst of both: an alert for us and a blank
        page for them.
        """
        import importlib
        import inspect

        from config.api.errors import SERVICE_EXCEPTION_MAP

        unmapped = []
        for module_path in (
            "accounts.documents",
            "accounts.privacy",
            "accounts.verification",
            "engagement.services",
            "properties.services",
            "reviews.services",
            "tenancies.services",
            "universities.services",
        ):
            module = importlib.import_module(module_path)
            for name, obj in vars(module).items():
                if not (inspect.isclass(obj) and issubclass(obj, Exception)):
                    continue
                if obj.__module__ != module_path or not name.endswith("Error"):
                    continue
                if not any(klass.__name__ in SERVICE_EXCEPTION_MAP for klass in obj.__mro__):
                    unmapped.append(f"{module_path}.{name}")

        assert not unmapped, (
            "These service exceptions have no entry in SERVICE_EXCEPTION_MAP, "
            "so they surface as a 500:\n  " + "\n  ".join(sorted(unmapped))
        )


# ---------------------------------------------------------------------------
# Request id
# ---------------------------------------------------------------------------


class TestRequestID:
    def test_a_successful_response_carries_one_too(self, api):
        """An id present only on errors we anticipated is missing for the ones
        we did not."""
        response = api.get("/fine/")

        assert response.headers[RESPONSE_HEADER]

    def test_the_header_and_the_body_agree(self, api):
        response = api.get("/boom/drf_validation/")

        assert error_of(response)["request_id"] == response.headers[RESPONSE_HEADER]

    def test_an_inbound_id_is_honoured(self, api):
        """A trace started at the edge survives into our logs."""
        response = api.get("/fine/", HTTP_X_REQUEST_ID="edge-abc-123")

        assert response.headers[RESPONSE_HEADER] == "edge-abc-123"

    def test_a_hostile_inbound_id_is_replaced(self, api):
        """This string reaches structlog, the error body and possibly a log
        index. None of them should take an unbounded attacker-supplied value
        on trust."""
        response = api.get("/fine/", HTTP_X_REQUEST_ID="<script>alert(1)</script>")

        assert response.headers[RESPONSE_HEADER] != "<script>alert(1)</script>"
        assert response.headers[RESPONSE_HEADER]

    def test_an_overlong_inbound_id_is_replaced(self, api):
        response = api.get("/fine/", HTTP_X_REQUEST_ID="x" * 5000)

        assert len(response.headers[RESPONSE_HEADER]) <= 64

    def test_two_requests_get_different_ids(self, api):
        first = api.get("/fine/").headers[RESPONSE_HEADER]
        second = api.get("/fine/").headers[RESPONSE_HEADER]

        assert first != second


class TestTheHelper:
    def test_nothing_else_may_build_an_error_body(self):
        """`error_body` is the only constructor, so the shape has one
        definition rather than one per endpoint."""
        body = error_body(code=ErrorCode.CONFLICT, message="No.")

        assert set(body["error"]) == {"code", "message", "field_errors", "request_id"}
        assert body["error"]["field_errors"] == {}
