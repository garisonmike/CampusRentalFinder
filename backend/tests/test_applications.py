"""
Applications and the witnessed tenancy path (ADR-004 §1.1).

This is the primary control on dispute volume: an application accepted
on-platform produces a confirmed tenancy directly, with no claim, no
confirmation window and no queue entry. The tests that matter most here are the
ones asserting that **nothing** is created on that path.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from config.tenancy import TenantScopeError
from tenancies.constants import ApplicationStatus, ConfirmationSource, TenancyStatus
from tenancies.models import Application, Tenancy
from tenancies.services import (
    ApplicationNotDecidableError,
    accept_application,
    reject_application,
    withdraw_application,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


class TestApplication:
    def test_only_one_open_application_per_unit_and_applicant(
        self, application_factory, unit_factory
    ):
        unit = unit_factory()
        first = application_factory(unit=unit)

        with pytest.raises(IntegrityError), transaction.atomic():
            application_factory(unit=unit, applicant=first.applicant)

    def test_a_closed_application_leaves_room_to_apply_again(
        self, application_factory, unit_factory, landlord
    ):
        """A rejected application must not bar the unit for ever."""
        unit = unit_factory()
        first = application_factory(unit=unit)
        reject_application(first, decided_by=landlord)

        again = application_factory(unit=unit, applicant=first.applicant)

        assert again.pk != first.pk

    def test_a_decision_needs_an_author(self, application_factory):
        """Accepting is what creates a tenancy, so it must be attributable."""
        application = application_factory()
        application.decided_at = timezone.now()

        with pytest.raises(IntegrityError), transaction.atomic():
            application.save()

    def test_intended_months_must_be_positive(self, application_factory):
        with pytest.raises(IntegrityError), transaction.atomic():
            application_factory(intended_months=0)


# ---------------------------------------------------------------------------
# The witnessed path
# ---------------------------------------------------------------------------


class TestAcceptingAnApplication:
    def test_acceptance_creates_a_confirmed_tenancy(self, application_factory, landlord):
        application = application_factory()

        tenancy = accept_application(application, decided_by=landlord)

        assert tenancy.confirmation_source == ConfirmationSource.APPLICATION
        assert tenancy.status == TenancyStatus.ACTIVE
        assert tenancy.confirmed_by == landlord
        assert tenancy.confirmed_at is not None

    def test_the_on_platform_path_produces_no_claim(self, application_factory, landlord):
        """ADR-004 §1.1, and the reason the dispute queue is bounded at all.

        The platform holds the application, the acceptance, the actor and the
        timestamp. A second confirmation would add latency and a dispute
        surface for nothing.
        """
        from django.apps import apps

        accept_application(application_factory(), decided_by=landlord)

        # Asserted through the app registry so this keeps meaning once
        # TenancyClaim exists, rather than passing because it does not.
        try:
            claim_model = apps.get_model("tenancies", "TenancyClaim")
        except LookupError:
            claim_model = None

        if claim_model is not None:
            assert claim_model.all_objects.count() == 0

    def test_the_tenancy_carries_no_dispute(self, application_factory, landlord):
        tenancy = accept_application(application_factory(), decided_by=landlord)

        assert tenancy.was_disputed is False

    def test_it_is_marked_witnessed(self, application_factory, landlord):
        tenancy = accept_application(application_factory(), decided_by=landlord)

        assert tenancy.is_witnessed() is True

    def test_dates_and_rent_default_from_the_application_and_unit(
        self, application_factory, unit_factory, landlord
    ):
        unit = unit_factory(rent_kes=Decimal("11500.00"))
        application = application_factory(unit=unit)

        tenancy = accept_application(application, decided_by=landlord)

        assert tenancy.start_date == application.move_in_date
        assert tenancy.monthly_rent_kes == Decimal("11500.00")

    def test_dates_and_rent_can_be_overridden(self, application_factory, landlord):
        """The agreed terms may differ from what was applied for."""
        application = application_factory()
        agreed_start = dt.date.today() + dt.timedelta(days=30)

        tenancy = accept_application(
            application,
            decided_by=landlord,
            start_date=agreed_start,
            monthly_rent_kes=Decimal("8000.00"),
        )

        assert tenancy.start_date == agreed_start
        assert tenancy.monthly_rent_kes == Decimal("8000.00")

    def test_the_application_records_the_decision(self, application_factory, landlord):
        application = application_factory()

        accept_application(application, decided_by=landlord, note="Confirmed by phone")
        application.refresh_from_db()

        assert application.status == ApplicationStatus.ACCEPTED
        assert application.decided_by == landlord
        assert application.decision_note == "Confirmed by phone"

    def test_accepting_twice_is_refused(self, application_factory, landlord):
        application = application_factory()
        accept_application(application, decided_by=landlord)

        with pytest.raises(ApplicationNotDecidableError):
            accept_application(application, decided_by=landlord)

    def test_a_rejected_application_cannot_be_accepted(self, application_factory, landlord):
        application = application_factory()
        reject_application(application, decided_by=landlord)

        with pytest.raises(ApplicationNotDecidableError):
            accept_application(application, decided_by=landlord)

    def test_acceptance_is_atomic(self, application_factory, landlord, monkeypatch):
        """An accepted application with no tenancy is a stay the platform
        witnessed and cannot vouch for — the exact gap Tenancy exists to close.
        """
        application = application_factory()

        def explode(*args, **kwargs):
            raise RuntimeError("tenancy creation failed")

        monkeypatch.setattr(Tenancy.all_objects, "create", explode)

        with pytest.raises(RuntimeError), transaction.atomic():
            accept_application(application, decided_by=landlord)

        application.refresh_from_db()
        assert application.status == ApplicationStatus.SUBMITTED
        assert Tenancy.all_objects.count() == 0


class TestRejectingAndWithdrawing:
    def test_rejection_creates_nothing(self, application_factory, landlord):
        application = application_factory()

        reject_application(application, decided_by=landlord)

        assert application.status == ApplicationStatus.REJECTED
        assert Tenancy.all_objects.count() == 0

    def test_withdrawal_needs_no_decider(self, application_factory):
        """Withdrawing is the applicant's own act, not a decision about them."""
        application = application_factory()

        withdraw_application(application)
        application.refresh_from_db()

        assert application.status == ApplicationStatus.WITHDRAWN
        assert application.decided_by is None
        assert application.decided_at is None


# ---------------------------------------------------------------------------
# Tenancy constraints
# ---------------------------------------------------------------------------


class TestTenancyConstraints:
    def test_end_date_cannot_precede_start(self, application_factory, landlord):
        tenancy = accept_application(application_factory(), decided_by=landlord)
        tenancy.end_date = tenancy.start_date - dt.timedelta(days=1)

        with pytest.raises(IntegrityError), transaction.atomic():
            tenancy.save()

    def test_an_ongoing_tenancy_may_have_no_end_date(self, application_factory, landlord):
        tenancy = accept_application(application_factory(), decided_by=landlord)

        assert tenancy.end_date is None

    def test_rent_must_be_positive(self, application_factory, landlord):
        with pytest.raises(IntegrityError), transaction.atomic():
            accept_application(
                application_factory(), decided_by=landlord, monthly_rent_kes=Decimal("0.00")
            )

    def test_an_application_sourced_tenancy_must_name_its_application(
        self, unit_factory, tenant, landlord
    ):
        """The two paths must not blur (ADR-004)."""
        with pytest.raises(IntegrityError), transaction.atomic():
            Tenancy.all_objects.create(
                unit=unit_factory(),
                tenant=tenant,
                application=None,
                confirmation_source=ConfirmationSource.APPLICATION,
                confirmed_by=landlord,
                confirmed_at=timezone.now(),
                start_date=dt.date.today(),
                monthly_rent_kes=Decimal("9000.00"),
            )

    def test_an_unattributed_source_must_have_no_actor(
        self, application_factory, landlord, unit_factory, tenant
    ):
        """`auto` and `dispute_timeout` have no human actor, by definition."""
        application = application_factory()

        with pytest.raises(IntegrityError), transaction.atomic():
            Tenancy.all_objects.create(
                unit=application.unit,
                tenant=tenant,
                application=application,
                confirmation_source=ConfirmationSource.AUTO,
                confirmed_by=landlord,
                confirmed_at=timezone.now(),
                start_date=dt.date.today(),
                monthly_rent_kes=Decimal("9000.00"),
            )

    def test_an_attributed_source_must_have_an_actor(self, application_factory, tenant):
        application = application_factory()

        with pytest.raises(IntegrityError), transaction.atomic():
            Tenancy.all_objects.create(
                unit=application.unit,
                tenant=tenant,
                application=application,
                confirmation_source=ConfirmationSource.APPLICATION,
                confirmed_by=None,
                confirmed_at=timezone.now(),
                start_date=dt.date.today(),
                monthly_rent_kes=Decimal("9000.00"),
            )

    def test_a_tenant_cannot_be_deleted_out_from_under_a_tenancy(
        self, application_factory, landlord
    ):
        """PROTECT: a deleted user must not take the review evidence with them."""
        tenancy = accept_application(application_factory(), decided_by=landlord)

        with pytest.raises(IntegrityError), transaction.atomic():
            tenancy.tenant.delete()


# ---------------------------------------------------------------------------
# Tenant scoping
# ---------------------------------------------------------------------------


class TestTenancyScoping:
    def test_unqualified_queries_raise(self, application_factory, landlord):
        accept_application(application_factory(), decided_by=landlord)

        with pytest.raises(TenantScopeError):
            list(Application.objects.all())
        with pytest.raises(TenantScopeError):
            list(Tenancy.objects.all())

    def test_they_scope_through_the_unit(
        self,
        application_factory,
        unit_factory,
        property_factory,
        campus_factory,
        campus_distance_factory,
        university,
        university_factory,
        landlord,
    ):
        prop = property_factory()
        campus_distance_factory(
            property=prop, university=university, campus=campus_factory(university=university)
        )
        application = application_factory(unit=unit_factory(property=prop))
        tenancy = accept_application(application, decided_by=landlord)

        assert application in Application.objects.for_tenant(university)
        assert tenancy in Tenancy.objects.for_tenant(university)
        assert tenancy not in Tenancy.objects.for_tenant(university_factory())
