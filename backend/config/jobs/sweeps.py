"""
Shared sweep helpers.

One definition, imported by every app's jobs. A copy per app is the shape
`docs/OPERATIONS.md` calls out: two places, and eventually the wrong one wins.
"""

from __future__ import annotations

import datetime as dt

from django.utils import timezone


def oldest_overdue_age(queryset, field: str) -> dt.timedelta | None:
    """How long the oldest overdue row has been waiting.

    **The alerting signal for every sweep in the project.** A count tells you
    the queue is big, which it may legitimately be. This tells you whether
    something has been abandoned, which is the failure that matters when a
    worker dies quietly -- one document abandoned for six months is a worse
    breach than a thousand deleted on time.

    Returns ``None`` when nothing is overdue, which callers log as such rather
    than as zero: "nothing waiting" and "waiting no time at all" are different
    facts and only one of them is reassuring.
    """
    oldest = queryset.order_by(field).values_list(field, flat=True).first()
    return None if oldest is None else timezone.now() - oldest
