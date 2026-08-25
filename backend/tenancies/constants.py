"""Reference data for applications, claims and tenancies (ADR-004)."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class ApplicationStatus(models.TextChoices):
    SUBMITTED = "submitted", _("Submitted")
    UNDER_REVIEW = "under_review", _("Under review")
    ACCEPTED = "accepted", _("Accepted")
    REJECTED = "rejected", _("Rejected")
    WITHDRAWN = "withdrawn", _("Withdrawn")
    EXPIRED = "expired", _("Expired")


#: Statuses in which an application is still live, so only one may exist per
#: applicant per unit.
OPEN_APPLICATION_STATUSES = (ApplicationStatus.SUBMITTED, ApplicationStatus.UNDER_REVIEW)


class ConfirmationSource(models.TextChoices):
    """How a tenancy came to be confirmed (ADR-004).

    ``APPLICATION`` is the volume control: the platform witnessed the agreement,
    so no claim, no confirmation window and no dispute surface. Everything else
    arrived through a ``TenancyClaim``.
    """

    APPLICATION = "application", _("Accepted on-platform application")
    LANDLORD = "landlord", _("Confirmed by the landlord")
    CARETAKER = "caretaker", _("Confirmed by a caretaker")
    AUTO = "auto", _("Auto-confirmed: the confirmation window elapsed")
    ADMIN = "admin", _("Resolved by a platform administrator")
    DISPUTE_TIMEOUT = "dispute_timeout", _("Auto-resolved: the dispute window elapsed")


#: Sources with no human actor, so ``confirmed_by`` must be null.
UNATTRIBUTED_SOURCES = (ConfirmationSource.AUTO, ConfirmationSource.DISPUTE_TIMEOUT)


class TenancyStatus(models.TextChoices):
    ACTIVE = "active", _("Active")
    ENDED = "ended", _("Ended")
