"""
Saved properties and inquiries.

`Inquiry` replaces the draft's `RentalInquiry`, which is deleted with the rest
of `rentals` and had no successor.

The two properties that matter most here are not obvious from the model:

**Inquiries carry no contact details, and are not a thread.** If the
conversation could move to WhatsApp on the first message, the `Application`
would stop happening — and an accepted application is what creates a confirmed
tenancy with no claim, no confirmation window and no dispute surface
(ADR-004 §1.1). Every conversation that leaves early comes back later as a
claim, in the queue the whole design exists to bound.

**Rate limits are part of the feature.** An inquiry is an unsolicited message
to a stranger; a messaging feature without limits is a spam feature with extra
steps.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone

from accounts.capabilities import CaretakerPermission
from config.tenancy import TenantScopeError
from engagement.constants import InquiryStatus
from engagement.models import Inquiry, SavedProperty
from engagement.services import (
    InquiryNotAnswerableError,
    InquiryRateLimitError,
    PropertyNotContactableError,
    close_inquiry,
    expire_stale_inquiries,
    may_respond_to,
    respond_to_inquiry,
    save_property,
    send_inquiry,
    unsave_property,
)
from properties.constants import PropertyStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def listed_unit(property_factory, unit_factory, landlord_profile):
    """A published unit somebody can actually be asked about."""
    prop = property_factory(
        landlord=landlord_profile, status=PropertyStatus.PUBLISHED, published_at=timezone.now()
    )
    return unit_factory(property=prop)


# ---------------------------------------------------------------------------
# SavedProperty
# ---------------------------------------------------------------------------


class TestSavedProperty:
    def test_a_student_can_save_a_property(self, tenant, property_factory):
        saved = save_property(tenant, property_factory())

        assert saved.pk is not None

    def test_saving_twice_is_idempotent(self, tenant, property_factory):
        """A double tap on a phone, not an error worth surfacing."""
        prop = property_factory()

        first = save_property(tenant, prop)
        second = save_property(tenant, prop)

        assert first.pk == second.pk
        assert SavedProperty.all_objects.count() == 1

    def test_the_database_refuses_a_duplicate(self, tenant, property_factory):
        """What makes the idempotence above safe rather than hopeful."""
        prop = property_factory()
        save_property(tenant, prop)

        with pytest.raises(IntegrityError), transaction.atomic():
            SavedProperty.all_objects.create(user=tenant, property_saved=prop)

    def test_two_students_may_save_the_same_property(
        self, tenant, student_profile, property_factory
    ):
        prop = property_factory()

        save_property(tenant, prop)
        save_property(student_profile.user, prop)

        assert SavedProperty.all_objects.count() == 2

    def test_a_note_is_the_students_own(self, tenant, property_factory):
        """Never shown to the landlord: "too far from the matatu stage" is for
        the person deciding, not the person selling."""
        saved = save_property(tenant, property_factory(), note="Too far from the stage.")

        assert saved.note == "Too far from the stage."

    def test_unsaving_removes_it(self, tenant, property_factory):
        prop = property_factory()
        save_property(tenant, prop)

        unsave_property(tenant, prop)

        assert SavedProperty.all_objects.count() == 0

    def test_unsaving_something_unsaved_is_not_an_error(self, tenant, property_factory):
        assert unsave_property(tenant, property_factory()) == 0

    def test_there_is_no_notification_machinery(self):
        """A bookmark that emails you is a subscription nobody asked for."""
        fields = {field.name for field in SavedProperty._meta.get_fields()}

        assert not {"notify", "notify_on_price_change", "alert_enabled"} & fields

    def test_it_is_tenant_scoped(self, tenant, property_factory):
        save_property(tenant, property_factory())

        with pytest.raises(TenantScopeError):
            list(SavedProperty.objects.all())

    def test_it_scopes_through_the_property(
        self,
        tenant,
        property_factory,
        campus_factory,
        campus_distance_factory,
        university,
        university_factory,
    ):
        prop = property_factory()
        campus_distance_factory(
            property=prop, university=university, campus=campus_factory(university=university)
        )
        saved = save_property(tenant, prop)

        assert saved in SavedProperty.objects.for_tenant(university)
        assert saved not in SavedProperty.objects.for_tenant(university_factory())


# ---------------------------------------------------------------------------
# Sending an inquiry
# ---------------------------------------------------------------------------


class TestSendingAnInquiry:
    def test_a_student_can_ask_about_a_unit(self, listed_unit, tenant):
        inquiry = send_inquiry(
            unit=listed_unit, sender=tenant, message="Is water metered separately?"
        )

        assert inquiry.status == InquiryStatus.SENT
        assert inquiry.response == ""

    def test_a_preferred_move_in_date_is_optional(self, listed_unit, tenant):
        """A student who has not decided when they are moving still has
        questions worth asking."""
        inquiry = send_inquiry(unit=listed_unit, sender=tenant, message="Is there parking?")

        assert inquiry.preferred_move_in_date is None

    def test_a_preferred_move_in_date_is_kept_when_given(self, listed_unit, tenant):
        when = dt.date.today() + dt.timedelta(days=45)

        inquiry = send_inquiry(
            unit=listed_unit, sender=tenant, message="Available then?", preferred_move_in_date=when
        )

        assert inquiry.preferred_move_in_date == when

    def test_an_empty_message_is_refused(self, listed_unit, tenant):
        with pytest.raises(ValidationError):
            send_inquiry(unit=listed_unit, sender=tenant, message="   ")

    def test_the_database_refuses_an_empty_message(self, listed_unit, tenant):
        with pytest.raises(IntegrityError), transaction.atomic():
            Inquiry.all_objects.create(unit=listed_unit, sender=tenant, message="")

    def test_only_one_open_inquiry_per_unit_and_sender(self, listed_unit, tenant):
        send_inquiry(unit=listed_unit, sender=tenant, message="First question?")

        with pytest.raises(IntegrityError), transaction.atomic():
            Inquiry.all_objects.create(unit=listed_unit, sender=tenant, message="Second question?")

    def test_a_closed_inquiry_leaves_room_to_ask_again(self, listed_unit, tenant, landlord):
        first = send_inquiry(unit=listed_unit, sender=tenant, message="Is water metered?")
        respond_to_inquiry(first, responder=landlord, response="Yes, separately.")

        again = send_inquiry(unit=listed_unit, sender=tenant, message="And electricity?")

        assert again.pk != first.pk

    def test_a_dormant_listing_cannot_be_asked(self, listed_unit, tenant):
        """It belongs to an erased landlord (ADR-008), so an inquiry there is a
        message into a void the platform knows about in advance."""
        prop = listed_unit.property
        prop.status = PropertyStatus.DORMANT
        prop.published_at = None
        prop.save(update_fields=["status", "published_at"])

        with pytest.raises(PropertyNotContactableError):
            send_inquiry(unit=listed_unit, sender=tenant, message="Still available?")

    def test_a_draft_listing_cannot_be_asked(self, unit_factory, draft_property_factory, tenant):
        unit = unit_factory(property=draft_property_factory())

        with pytest.raises(PropertyNotContactableError):
            send_inquiry(unit=unit, sender=tenant, message="Still available?")


class TestNoContactDetailsAndNoThread:
    """ADR-004 §1.1 depends on the conversation staying on-platform."""

    def test_the_model_carries_no_contact_fields(self):
        """A phone number field would move the conversation to WhatsApp on the
        first message, and the Application would stop happening."""
        fields = {field.name for field in Inquiry._meta.get_fields()}

        assert not {"phone", "phone_number", "email", "contact_email", "whatsapp"} & fields

    def test_an_inquiry_is_not_a_thread(self, listed_unit, tenant, landlord):
        """One response, and the exchange closes. A thread would be a
        messaging product, and a messaging product is where the conversation
        stops producing an application."""
        inquiry = send_inquiry(unit=listed_unit, sender=tenant, message="Is water metered?")
        respond_to_inquiry(inquiry, responder=landlord, response="Yes.")

        with pytest.raises(InquiryNotAnswerableError):
            respond_to_inquiry(inquiry, responder=landlord, response="Actually, no.")

    def test_an_application_can_name_the_inquiry_it_grew_from(
        self, listed_unit, tenant, application_factory
    ):
        """The traceable on-platform path: inquiry, application, acceptance,
        confirmed tenancy, review."""
        inquiry = send_inquiry(unit=listed_unit, sender=tenant, message="Available in May?")

        application = application_factory(unit=listed_unit, applicant=tenant, inquiry=inquiry)

        assert application.inquiry == inquiry
        assert inquiry.applications.count() == 1

    def test_the_link_is_optional(self, application_factory):
        """An application is valid whether or not a question preceded it."""
        assert application_factory().inquiry is None

    def test_deleting_the_inquiry_leaves_the_application(
        self, listed_unit, tenant, application_factory
    ):
        """SET_NULL: losing the question must not lose the application."""
        from tenancies.models import Application

        inquiry = send_inquiry(unit=listed_unit, sender=tenant, message="Available?")
        application = application_factory(unit=listed_unit, applicant=tenant, inquiry=inquiry)

        inquiry.delete()

        assert Application.all_objects.filter(pk=application.pk).exists()


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    def send_from(self, tenant, unit_factory, property_factory, landlord_profile, count):
        for index in range(count):
            prop = property_factory(
                landlord=landlord_profile,
                status=PropertyStatus.PUBLISHED,
                published_at=timezone.now(),
            )
            send_inquiry(
                unit=unit_factory(property=prop), sender=tenant, message=f"Question {index}?"
            )

    def test_a_student_may_ask_up_to_the_cap(
        self, tenant, unit_factory, property_factory, landlord_profile
    ):
        with override_settings(INQUIRY_MAX_PER_USER=3, INQUIRY_MAX_PER_UNIT=5):
            self.send_from(tenant, unit_factory, property_factory, landlord_profile, 3)

    def test_the_per_user_cap_bites(self, tenant, unit_factory, property_factory, landlord_profile):
        """Per unit alone would let one account paper every listing in a
        town."""
        with override_settings(INQUIRY_MAX_PER_USER=2, INQUIRY_MAX_PER_UNIT=5):
            self.send_from(tenant, unit_factory, property_factory, landlord_profile, 2)

            with pytest.raises(InquiryRateLimitError):
                self.send_from(tenant, unit_factory, property_factory, landlord_profile, 1)

    def test_the_per_unit_cap_bites(self, listed_unit, tenant, landlord):
        """Per user alone would let one account hammer a single landlord."""
        with override_settings(INQUIRY_MAX_PER_USER=50, INQUIRY_MAX_PER_UNIT=1):
            first = send_inquiry(unit=listed_unit, sender=tenant, message="One?")
            respond_to_inquiry(first, responder=landlord, response="Yes.")

            with pytest.raises(InquiryRateLimitError):
                send_inquiry(unit=listed_unit, sender=tenant, message="Two?")

    def test_the_cap_is_per_sender(
        self, tenant, student_profile, unit_factory, property_factory, landlord_profile
    ):
        with override_settings(INQUIRY_MAX_PER_USER=1):
            self.send_from(tenant, unit_factory, property_factory, landlord_profile, 1)
            self.send_from(
                student_profile.user, unit_factory, property_factory, landlord_profile, 1
            )

    def test_the_window_rolls(self, tenant, unit_factory, property_factory, landlord_profile):
        self.send_from(tenant, unit_factory, property_factory, landlord_profile, 1)
        Inquiry.all_objects.update(created_at=timezone.now() - dt.timedelta(days=30))

        with override_settings(INQUIRY_MAX_PER_USER=1):
            self.send_from(tenant, unit_factory, property_factory, landlord_profile, 1)

    def test_the_refusal_says_what_to_do(
        self, tenant, unit_factory, property_factory, landlord_profile
    ):
        with (
            override_settings(INQUIRY_MAX_PER_USER=0),
            pytest.raises(InquiryRateLimitError) as caught,
        ):
            self.send_from(tenant, unit_factory, property_factory, landlord_profile, 1)

        assert "reply" in str(caught.value).lower()


# ---------------------------------------------------------------------------
# Responding
# ---------------------------------------------------------------------------


class TestResponding:
    def test_the_landlord_may_respond(self, listed_unit, tenant, landlord):
        inquiry = send_inquiry(unit=listed_unit, sender=tenant, message="Is water metered?")

        answered = respond_to_inquiry(inquiry, responder=landlord, response="Yes, separately.")

        assert answered.status == InquiryStatus.ANSWERED
        assert answered.responded_by == landlord
        assert answered.responded_at is not None

    def test_a_caretaker_with_the_permission_may_respond(
        self, listed_unit, tenant, caretaker_assignment_factory
    ):
        """Per the permission set (ADR-003). A caretaker is who actually knows
        whether the water is metered."""
        assignment = caretaker_assignment_factory(
            property=listed_unit.property,
            permissions=[CaretakerPermission.RESPOND_INQUIRIES],
        )
        inquiry = send_inquiry(unit=listed_unit, sender=tenant, message="Is water metered?")

        answered = respond_to_inquiry(
            inquiry, responder=assignment.user, response="Yes, separately."
        )

        assert answered.responded_by == assignment.user

    def test_a_caretaker_without_it_may_not(
        self, listed_unit, tenant, caretaker_assignment_factory
    ):
        assignment = caretaker_assignment_factory(
            property=listed_unit.property,
            permissions=[CaretakerPermission.MANAGE_PHOTOS],
        )
        inquiry = send_inquiry(unit=listed_unit, sender=tenant, message="Is water metered?")

        assert may_respond_to(assignment.user, inquiry) is False

        with pytest.raises(InquiryNotAnswerableError):
            respond_to_inquiry(inquiry, responder=assignment.user, response="Yes.")

    def test_a_revoked_assignment_may_not(self, listed_unit, tenant, caretaker_assignment_factory):
        """A caretaker who has left keeps nothing. `has_permission` reads
        `is_active`, so revocation closes this path without a second check."""
        assignment = caretaker_assignment_factory(
            property=listed_unit.property,
            permissions=[CaretakerPermission.RESPOND_INQUIRIES],
            is_active=False,
            revoked_at=timezone.now(),
        )
        inquiry = send_inquiry(unit=listed_unit, sender=tenant, message="Is water metered?")

        assert may_respond_to(assignment.user, inquiry) is False

    def test_a_stranger_may_not(self, listed_unit, tenant, student_profile):
        inquiry = send_inquiry(unit=listed_unit, sender=tenant, message="Is water metered?")

        assert may_respond_to(student_profile.user, inquiry) is False

    def test_another_landlord_may_not(self, listed_unit, tenant, landlord_profile_factory):
        other = landlord_profile_factory()
        inquiry = send_inquiry(unit=listed_unit, sender=tenant, message="Is water metered?")

        assert may_respond_to(other.user, inquiry) is False

    def test_an_empty_response_is_refused(self, listed_unit, tenant, landlord):
        inquiry = send_inquiry(unit=listed_unit, sender=tenant, message="Is water metered?")

        with pytest.raises(InquiryNotAnswerableError):
            respond_to_inquiry(inquiry, responder=landlord, response="  ")

    def test_the_database_refuses_an_unattributed_response(self, listed_unit, tenant):
        """ "Who answered" matters when a caretaker makes a commitment the owner
        did not."""
        inquiry = send_inquiry(unit=listed_unit, sender=tenant, message="Is water metered?")
        inquiry.response = "Yes."

        with pytest.raises(IntegrityError), transaction.atomic():
            inquiry.save()

    def test_the_database_refuses_an_answered_inquiry_with_no_response(self, listed_unit, tenant):
        inquiry = send_inquiry(unit=listed_unit, sender=tenant, message="Is water metered?")
        inquiry.status = InquiryStatus.ANSWERED

        with pytest.raises(IntegrityError), transaction.atomic():
            inquiry.save()

    def test_closing_is_not_a_rejection(self, listed_unit, tenant):
        """An inquiry is not an application, so there is nothing to reject."""
        inquiry = send_inquiry(unit=listed_unit, sender=tenant, message="Is water metered?")

        close_inquiry(inquiry)

        assert inquiry.status == InquiryStatus.CLOSED
        assert inquiry.status != InquiryStatus.EXPIRED


class TestExpiry:
    def test_an_unanswered_inquiry_expires(self, listed_unit, tenant):
        """Recorded rather than left `sent` for ever, so "the landlord never
        replied" is a fact the student can see -- not a screen identical to one
        still waiting."""
        inquiry = send_inquiry(unit=listed_unit, sender=tenant, message="Is water metered?")
        Inquiry.all_objects.filter(pk=inquiry.pk).update(
            created_at=timezone.now() - dt.timedelta(days=90)
        )

        assert expire_stale_inquiries() == 1
        inquiry.refresh_from_db()
        assert inquiry.status == InquiryStatus.EXPIRED

    def test_a_recent_inquiry_does_not(self, listed_unit, tenant):
        send_inquiry(unit=listed_unit, sender=tenant, message="Is water metered?")

        assert expire_stale_inquiries() == 0

    def test_an_answered_inquiry_does_not(self, listed_unit, tenant, landlord):
        inquiry = send_inquiry(unit=listed_unit, sender=tenant, message="Is water metered?")
        respond_to_inquiry(inquiry, responder=landlord, response="Yes.")
        Inquiry.all_objects.filter(pk=inquiry.pk).update(
            created_at=timezone.now() - dt.timedelta(days=90)
        )

        assert expire_stale_inquiries() == 0

    def test_an_expired_inquiry_frees_the_slot(self, listed_unit, tenant):
        inquiry = send_inquiry(unit=listed_unit, sender=tenant, message="Is water metered?")
        Inquiry.all_objects.filter(pk=inquiry.pk).update(
            created_at=timezone.now() - dt.timedelta(days=90)
        )
        expire_stale_inquiries()

        again = send_inquiry(unit=listed_unit, sender=tenant, message="Still available?")

        assert again.pk != inquiry.pk


class TestInquiryScoping:
    def test_unqualified_queries_raise(self, listed_unit, tenant):
        send_inquiry(unit=listed_unit, sender=tenant, message="Is water metered?")

        with pytest.raises(TenantScopeError):
            list(Inquiry.objects.all())

    def test_it_scopes_through_the_unit(
        self,
        listed_unit,
        tenant,
        campus_factory,
        campus_distance_factory,
        university,
        university_factory,
    ):
        campus_distance_factory(
            property=listed_unit.property,
            university=university,
            campus=campus_factory(university=university),
        )
        inquiry = send_inquiry(unit=listed_unit, sender=tenant, message="Is water metered?")

        assert inquiry in Inquiry.objects.for_tenant(university)
        assert inquiry not in Inquiry.objects.for_tenant(university_factory())
