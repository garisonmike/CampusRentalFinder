"""
Edge caching for the public listing reads (ADR-001).

**Why this exists as a decorator rather than as nginx configuration.** The
public listing endpoints are the product: a student opens the site, reads a
catalogue, and leaves. Serving that from the origin means every one of them
costs a database round trip on a connection that is already slow, and it means
a lecture hall on one campus egress address arrives as one client to the
throttle.

Two things follow, and only one of them is fixed here:

**Cacheable.** A published listing changes when a landlord edits it, which is
rarely. `s-maxage` lets a CDN answer most reads without touching the origin;
`stale-while-revalidate` lets it keep answering while it refreshes, which is
the difference between a slow page and a spinning one when the origin is busy.

**The throttle key is the open question.** DRF's anonymous throttle keys on IP,
and Kenyan campus wifi puts hundreds of students behind one address. A
`public_read` rate that is generous for one person is that rate divided by four
hundred for a hall, and the failure lands on the busiest day of the intake.
Caching removes most of that traffic from the origin and therefore from the
throttle -- it does not make the keying correct, and `docs/PRE_LAUNCH.md`
carries it as an open item with what would have to be measured.
"""

from __future__ import annotations

from functools import wraps

from django.conf import settings
from django.utils.cache import patch_cache_control


def cacheable_at_the_edge(view_func):
    """Mark a **public, anonymous** read as cacheable by a shared cache.

    `public` is deliberate and load-bearing: without it a CDN must treat a
    response as private and store nothing. It is only correct because these
    endpoints are anonymous reads of published data -- an authenticated
    response marked `public` is one user's data in everybody's cache, which is
    why this decorator refuses to mark a response for a request that carried
    credentials rather than trusting the caller to only apply it in the right
    place.
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        response = view_func(request, *args, **kwargs)

        if request.method not in ("GET", "HEAD"):
            return response
        if getattr(request, "user", None) is not None and request.user.is_authenticated:
            # A signed-in reader gets the same body today, but marking it
            # `public` would put it in a shared cache keyed by URL alone, and
            # the first personalised field added later would leak silently.
            patch_cache_control(response, private=True, max_age=0)
            return response

        patch_cache_control(
            response,
            public=True,
            max_age=settings.PUBLIC_READ_MAX_AGE_SECONDS,
            s_maxage=settings.PUBLIC_READ_SHARED_MAX_AGE_SECONDS,
            stale_while_revalidate=settings.PUBLIC_READ_STALE_SECONDS,
        )
        return response

    return wrapper
