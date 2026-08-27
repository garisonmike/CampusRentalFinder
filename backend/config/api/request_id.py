"""
A request id on every request, and in every error body.

The frontend reports a bug by pasting an id; support finds the exact log lines
without asking anyone to reproduce anything. That only works if the id is on
the response **whether or not the request succeeded** — an id that appears only
on errors we anticipated is missing for the ones we did not.

Accepts an inbound ``X-Request-ID`` so a trace started at the edge (a load
balancer, the frontend's own retry wrapper) survives into our logs, and
generates one otherwise. Inbound values are length-capped and character-filtered
before use: this string reaches structlog, the error body and potentially a log
aggregator's index, and none of those should take an unbounded attacker-supplied
value on trust.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

import structlog

#: Set per request, read by the exception handler and the log processor without
#: either needing the request object passed down to it.
_request_id: ContextVar[str] = ContextVar("request_id", default="")

HEADER = "HTTP_X_REQUEST_ID"
RESPONSE_HEADER = "X-Request-ID"

#: Conservative on purpose. Anything else is discarded and replaced.
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._@:-]{1,64}$")


def get_request_id() -> str:
    """The current request's id, or empty outside a request."""
    return _request_id.get()


def set_request_id(value: str) -> None:
    _request_id.set(value)


def _clean(candidate: str | None) -> str:
    if candidate and SAFE_REQUEST_ID.match(candidate):
        return candidate
    return uuid.uuid4().hex


class RequestIDMiddleware:
    """Assign a request id, bind it to the logger, echo it on the response."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = _clean(request.META.get(HEADER))
        set_request_id(request_id)
        request.request_id = request_id

        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = self.get_response(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")

        response[RESPONSE_HEADER] = request_id
        return response
