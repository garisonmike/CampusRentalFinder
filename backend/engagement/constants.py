"""Reference data for saved properties and inquiries."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _

MAX_INQUIRY_LENGTH = 1000
MAX_RESPONSE_LENGTH = 1000


class InquiryStatus(models.TextChoices):
    """Where an inquiry stands.

    Deliberately small. An inquiry is a question, not a workflow, and every
    state here is one a student or landlord would recognise from the screen.
    """

    SENT = "sent", _("Sent")
    ANSWERED = "answered", _("Answered")
    #: The student got what they needed and moved on, or the landlord closed
    #: it. Not a rejection: an inquiry is not an application.
    CLOSED = "closed", _("Closed")
    #: Nobody answered within the window. Recorded rather than left `sent` for
    #: ever, so "the landlord never replied" is a fact the student can see.
    EXPIRED = "expired", _("Expired unanswered")


#: Statuses in which an inquiry is still awaiting a reply.
OPEN_INQUIRY_STATUSES = (InquiryStatus.SENT,)
