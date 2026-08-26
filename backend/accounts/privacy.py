"""
Subject access and erasure (ADR-008, Data Protection Act 2019).

Kenya's DPA gives a data subject the right to obtain what is held about them
and the right to have it erased. Both are built now rather than retrofitted,
because retrofitting erasure into a schema that assumed nobody would ever ask
is where the deletes-that-cascade-into-nothing bugs come from.

**Erasure anonymises; it does not cascade.** The account's identifying fields
are overwritten in place and every foreign key survives. That is not a
convenience:

> If erasure deleted reviews, anyone could remove criticism of a property by
> deleting the account that wrote it. A landlord who wanted a bad review gone
> would need one cooperating student and one support ticket. **The right to be
> forgotten is not a right to unpublish what you said about someone else.**

What a subject *is* entitled to is that the review can no longer be traced back
to them, and that is what tombstoning achieves: the text stays, the author
becomes "a former student", and no field on the account still names a person.

See ADR-008 for the full table of what is retained and why.
"""

from __future__ import annotations

import datetime as dt
import secrets
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django.utils import timezone

from .models import User

#: The domain tombstoned accounts land on. Reserved by RFC 2606, so it can
#: never be routable and a stray email cannot reach a real person.
ERASED_EMAIL_DOMAIN = "erased.invalid"

#: What a tombstoned author is called wherever a name would be shown.
TOMBSTONE_NAME = "Former student"


# ---------------------------------------------------------------------------
# Subject access
# ---------------------------------------------------------------------------


def _profile_export(user: User) -> dict[str, Any] | None:
    profile = getattr(user, "student_profile", None)
    if profile is None:
        return None

    return {
        "university": profile.university.name,
        "student_email": profile.student_email,
        "verification_status": profile.verification_status,
        "verification_method": profile.verification_method,
        "verified_at": profile.verified_at,
        "rejection_reason": profile.rejection_reason,
        "year_of_study": profile.year_of_study,
        "course": profile.course,
        "created_at": profile.created_at,
    }


def _verification_export(user: User) -> list[dict[str, Any]]:
    """Verification **outcomes**, never the images.

    The image is deleted by retention long before most access requests arrive,
    and returning it would mean re-exposing an identity document to whatever
    channel the export travels over. The decision is the record; the document
    was only ever how it was reached.
    """
    profile = getattr(user, "student_profile", None)
    if profile is None:
        return []

    return [
        {
            "submitted_at": request.created_at,
            "status": request.status,
            "decision_reason": request.decision_reason,
            "reviewed_at": request.reviewed_at,
            # Deliberately NOT reviewed_by. Naming the member of staff who
            # refused a student's ID, in a document handed to that student, is
            # how a policy decision becomes a personal one.
            "attempt": request.attempt,
            "document_deleted_at": request.document.deleted_at,
        }
        for request in profile.verification_requests.select_related("document").order_by(
            "created_at"
        )
    ]


def _tenancy_export(user: User) -> list[dict[str, Any]]:
    from tenancies.models import Tenancy

    return [
        {
            "unit": str(tenancy.unit),
            "property": str(tenancy.unit.property),
            "start_date": tenancy.start_date,
            "end_date": tenancy.end_date,
            "monthly_rent_kes": tenancy.monthly_rent_kes,
            "status": tenancy.status,
            # Derived, not stored. A subject reading their own export should
            # see whether a stay is current, and the stored status no longer
            # answers that.
            "is_current": tenancy.is_current(),
            "terminated_early": tenancy.terminated_early,
            "confirmation_source": tenancy.confirmation_source,
            "was_disputed": tenancy.was_disputed,
        }
        for tenancy in Tenancy.all_objects.filter(tenant=user)
        .select_related("unit__property")
        .order_by("start_date")
    ]


def _claim_export(user: User) -> list[dict[str, Any]]:
    from tenancies.models import TenancyClaim

    return [
        {
            "unit": str(claim.unit),
            "start_date": claim.start_date,
            "end_date": claim.end_date,
            "status": claim.status,
            "is_retrospective": claim.is_retrospective,
            "dispute_reason": claim.dispute_reason,
            # The disputer is another party. A claim's dispute is data about
            # the landlord as much as about the student.
            "escalation_reason": claim.escalation_reason,
            "created_at": claim.created_at,
            "resolved_at": claim.resolved_at,
        }
        for claim in TenancyClaim.all_objects.filter(claimant=user)
        .select_related("unit")
        .order_by("created_at")
    ]


def _application_export(user: User) -> list[dict[str, Any]]:
    from tenancies.models import Application

    return [
        {
            "unit": str(application.unit),
            "status": application.status,
            "move_in_date": application.move_in_date,
            "decision_note": application.decision_note,
            "created_at": application.created_at,
        }
        for application in Application.all_objects.filter(applicant=user)
        .select_related("unit")
        .order_by("created_at")
    ]


def _review_export(user: User) -> list[dict[str, Any]]:
    from reviews.models import Review

    return [
        {
            "unit": str(review.tenancy.unit),
            "rating": review.rating,
            "comment": review.comment,
            "would_recommend": review.would_recommend,
            "is_published": review.is_published,
            "created_at": review.created_at,
            "editable_until": review.editable_until,
        }
        for review in Review.all_objects.filter(tenancy__tenant=user)
        .select_related("tenancy__unit")
        .order_by("created_at")
    ]


def export_personal_data(user: User) -> dict[str, Any]:
    """Everything held about one data subject.

    Assembled per-model rather than by walking relations generically. A generic
    walk would follow `DocumentAccessLog` and hand a student the names of staff
    who looked at their ID, and would follow `Tenancy` into a landlord's own
    records — both of which are somebody else's personal data, and neither of
    which a subject access request entitles them to.
    """
    return {
        "generated_at": timezone.now(),
        "account": {
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "phone_number": user.phone_number,
            "date_joined": user.date_joined,
            "is_active": user.is_active,
            "erased_at": user.erased_at,
        },
        "student_profile": _profile_export(user),
        "verification_requests": _verification_export(user),
        "applications": _application_export(user),
        "tenancy_claims": _claim_export(user),
        "tenancies": _tenancy_export(user),
        "reviews": _review_export(user),
    }


# ---------------------------------------------------------------------------
# Erasure
# ---------------------------------------------------------------------------


@dataclass
class ErasureReport:
    """What erasure touched, for the confirmation sent to the subject."""

    erased_at: dt.datetime
    reviews_tombstoned: int = 0
    tenancies_retained: int = 0
    claims_retained: int = 0
    documents_already_deleted: int = 0
    access_log_rows_pseudonymised: int = 0
    retained: list[str] = field(default_factory=list)


class AlreadyErasedError(Exception):
    """This account has already been erased."""


def _tombstone_email(user: User) -> str:
    """A unique, unroutable address.

    Unique because `User.email` is the login identifier and carries a UNIQUE
    constraint; a shared placeholder would make the second erasure fail.
    """
    return f"erased-{secrets.token_hex(16)}@{ERASED_EMAIL_DOMAIN}"


@transaction.atomic
def erase_personal_data(user: User, *, now: dt.datetime | None = None) -> ErasureReport:
    """Anonymise a subject in place. **Nothing cascades.**

    Every foreign key survives, so a review keeps its text and its tenancy
    keeps its evidence — but no field on the account still names a person, and
    nothing links the two back together.

    The account is deactivated rather than deleted. Deleting the row would
    cascade into `StudentProfile` and be refused by `PROTECT` on `Tenancy`
    anyway; keeping it is what lets the tombstone exist at all.
    """
    from reviews.models import Review
    from tenancies.models import Tenancy, TenancyClaim

    now = now or timezone.now()

    if user.erased_at is not None:
        raise AlreadyErasedError(f"user {user.pk} was erased at {user.erased_at}")

    report = ErasureReport(
        erased_at=now,
        reviews_tombstoned=Review.all_objects.filter(tenancy__tenant=user).count(),
        tenancies_retained=Tenancy.all_objects.filter(tenant=user).count(),
        claims_retained=TenancyClaim.all_objects.filter(claimant=user).count(),
        documents_already_deleted=0,
        retained=[
            "Reviews you wrote, with your name replaced by a tombstone.",
            "The tenancy records those reviews rest on, which are also the landlord's records.",
            "The record that verification happened, without the document.",
            "The log of who accessed your verification document, with every "
            "link to you removed. It records that someone opened a case, when "
            "and why, and can no longer say whose case it was.",
        ],
    )

    profile = getattr(user, "student_profile", None)
    if profile is not None:
        report.documents_already_deleted = profile.verification_requests.filter(
            document__deleted_at__isnull=False
        ).count()

        report.access_log_rows_pseudonymised = _pseudonymise_access_log(profile)

        profile.student_email = ""
        profile.course = ""
        profile.year_of_study = None
        profile.rejection_reason = ""
        profile.save(
            update_fields=[
                "student_email",
                "course",
                "year_of_study",
                "rejection_reason",
                "updated_at",
            ]
        )

    user.email = _tombstone_email(user)
    user.first_name = TOMBSTONE_NAME
    user.last_name = ""
    user.phone_number = ""
    user.avatar_url = ""
    user.phone_verified = False
    user.email_verified = False
    user.is_active = False
    user.erased_at = now
    user.set_unusable_password()
    user.save()

    return report


def _pseudonymise_access_log(profile) -> int:
    """Cut every link from the access log to the person (ADR-008 §2.1).

    The trail is an audit record of **our staff's** actions, so deleting it
    would destroy evidence a regulator is entitled to and the subject has no
    right to remove. Keeping the foreign keys, though, would leave the subject
    reachable from it -- so neither extreme is the answer.

    What survives: ``subject_token``, the reviewer, the timestamp, the request
    id and the purpose. The trail still answers *"who opened this case, when,
    and why"*. It can no longer answer *"whose case was it"*, and that is
    deliberate and **irreversible by design** -- the token is random, so there
    is nothing to reverse.
    """
    from .documents import DocumentAccessLog

    return DocumentAccessLog.objects.filter(verification_request__profile=profile).update(
        document=None, verification_request=None
    )


def is_tombstoned(user: User) -> bool:
    """Whether this account has been erased.

    A method on the module rather than a property on the model, so the check
    reads the same from a serializer, a template and a job.
    """
    return user.erased_at is not None


def display_name_for(user: User) -> str:
    """What to show wherever a person's name would appear.

    The one place tombstoning becomes visible. A review by an erased student
    reads "Former student" and a dormant listing's owner reads "Former
    landlord" -- the content stays, the person does not. The stored first_name
    already holds the right tombstone, so this needs no branch on role.
    """
    if is_tombstoned(user):
        return user.first_name or TOMBSTONE_NAME
    return user.get_full_name()


# ---------------------------------------------------------------------------
# Landlord erasure (ADR-008 §2.2)
# ---------------------------------------------------------------------------


TOMBSTONE_LANDLORD_NAME = "Former landlord"


class ActiveTenanciesError(Exception):
    """This landlord still has students living in their property."""


@dataclass
class LandlordErasureReport:
    """What a landlord erasure touched."""

    erased_at: dt.datetime
    properties_made_dormant: int = 0
    units_retained: int = 0
    reviews_retained: int = 0
    retained: list[str] = field(default_factory=list)


def landlord_erasure_blockers(user: User, *, today: dt.date | None = None) -> list[str]:
    """Why this landlord cannot complete erasure yet.

    A landlord with students currently living in their property is a party to
    a running contract. Erasing their contact details mid-tenancy would leave
    those students with a leaking roof and nobody to call, which is not a
    privacy outcome — it is an outage with a legal basis attached.

    **Flagged, never silently partial.** Erasing the fields that happen to be
    safe and leaving the rest is the worst option: the subject believes they
    are erased, the platform believes it complied, and neither is true.
    """
    from properties.models import Property
    from tenancies.models import Tenancy

    profile = getattr(user, "landlord_profile", None)
    if profile is None:
        return []

    property_ids = Property.all_objects.filter(landlord=profile).values("pk")
    running = (
        Tenancy.all_objects.filter(unit__property__in=property_ids).current(today=today).count()
    )

    if running:
        return [
            f"{running} tenancy(ies) are currently running in this landlord's "
            f"property. Erasure can complete once they end."
        ]
    return []


@transaction.atomic
def erase_landlord_data(
    user: User, *, now: dt.datetime | None = None, today: dt.date | None = None
) -> LandlordErasureReport:
    """Erase a landlord's personal data, keeping the business record.

    A landlord is **two things at once**: a natural person, and a counterparty
    to contracts other people are still relying on. The line falls between
    them, and ADR-008 §2.2 records the balancing test.

    Erased: name, phone, personal email, national ID, KRA PIN, payout phone,
    avatar, business name.

    Retained: properties, units, photos, and every tenancy, review and
    aggregate resting on them. Those are other people's records — deleting a
    landlord's properties would erase students' tenancy history and their
    reviews, which is not the landlord's right to exercise.

    Properties do not cascade. They move to ``DORMANT``: unlisted,
    unsearchable, closed to new applications, existing tenancies and reviews
    intact and attributed to a tombstone.
    """
    from properties.constants import PropertyStatus
    from properties.models import Property, Unit
    from reviews.models import Review

    now = now or timezone.now()

    if user.erased_at is not None:
        raise AlreadyErasedError(f"user {user.pk} was erased at {user.erased_at}")

    blockers = landlord_erasure_blockers(user, today=today)
    if blockers:
        raise ActiveTenanciesError("; ".join(blockers))

    profile = getattr(user, "landlord_profile", None)
    property_ids: list[int] = []

    if profile is not None:
        property_ids = list(
            Property.all_objects.filter(landlord=profile).values_list("pk", flat=True)
        )

        profile.business_name = ""
        profile.kra_pin = ""
        profile.national_id = ""
        # The ID document itself is removed by the retention job on its own
        # deadline; clearing the key here stops anything reaching it meanwhile.
        profile.id_document_key = ""
        profile.payout_phone = ""
        profile.save(
            update_fields=[
                "business_name",
                "kra_pin",
                "national_id",
                "id_document_key",
                "payout_phone",
                "updated_at",
            ]
        )

    report = LandlordErasureReport(
        erased_at=now,
        properties_made_dormant=len(property_ids),
        units_retained=Unit.all_objects.filter(property_id__in=property_ids).count(),
        reviews_retained=Review.all_objects.filter(
            tenancy__unit__property_id__in=property_ids
        ).count(),
        retained=[
            "Your property and unit records, which other people's tenancies and reviews depend on.",
            "Reviews of those properties, attributed to a former landlord.",
            "Your rating history, under the same tombstone.",
        ],
    )

    # Dormant, not deleted. Unlisted and unsearchable, with everything resting
    # on them intact.
    Property.all_objects.filter(pk__in=property_ids).update(
        status=PropertyStatus.DORMANT, published_at=None
    )
    Unit.all_objects.filter(property_id__in=property_ids).update(is_active=False)

    user.email = _tombstone_email(user)
    user.first_name = TOMBSTONE_LANDLORD_NAME
    user.last_name = ""
    user.phone_number = ""
    user.avatar_url = ""
    user.phone_verified = False
    user.email_verified = False
    user.is_active = False
    user.erased_at = now
    user.set_unusable_password()
    user.save()

    return report
