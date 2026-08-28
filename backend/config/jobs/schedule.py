"""
The scheduled-job table (docs/OPERATIONS.md).

Every job here fails **silently** when the worker or the scheduler stops. That
is the reason this table exists as data rather than as a list of
``scheduler.cron(...)`` calls scattered across app modules: the set of things
that must be running is a fact about the system, and it should be readable in
one place by a person trying to work out what stopped.

The cadences are the ones docs/OPERATIONS.md commits to. Changing one here
without changing it there is a drift the architecture test catches.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScheduledJob:
    """One recurring job."""

    #: Dotted path, so the table stays importable without importing the world.
    func: str
    cron: str
    queue: str
    #: What breaks when it stops. Written for whoever is reading this at 2am.
    on_failure: str

    #: Registered but not installed. A job is held when running it would do
    #: harm the code cannot prevent -- not when it is merely unfinished.
    enabled: bool = True

    #: Why it is held, and what would unblock it. Required when ``enabled`` is
    #: False, because a disabled job with no reason gets re-enabled by the next
    #: person who notices it is off and assumes somebody forgot.
    held_because: str = ""


SCHEDULE: tuple[ScheduledJob, ...] = (
    ScheduledJob(
        func="tenancies.jobs.sweep_overdue_claims",
        cron="0 * * * *",
        queue="default",
        on_failure=(
            "Claims sit pending for ever and it looks identical to landlords "
            "simply not confirming -- the exact behaviour the timeout defeats. "
            "The failure restores the bug."
        ),
    ),
    ScheduledJob(
        func="tenancies.jobs.sweep_overdue_disputes",
        cron="0 * * * *",
        queue="default",
        on_failure=(
            "Escalated disputes accumulate and the reviews behind them stay "
            "impossible. The platform silently vetoes reviews on behalf of the "
            "landlords who disputed them."
        ),
    ),
    ScheduledJob(
        func="tenancies.jobs.sweep_overdue_terminations",
        cron="0 * * * *",
        queue="default",
        on_failure=(
            "Early terminations sit pending for ever and the stays behind them "
            "keep reading as current -- so a landlord's vacancy list is wrong "
            "and a student who moved out months ago still shows as living "
            "there. Silence stops being a signal and becomes a veto."
        ),
    ),
    ScheduledJob(
        func="accounts.retention.sweep_due_erasures",
        cron="30 * * * *",
        queue="default",
        on_failure=(
            "Erasure requests enter cooling-off and never execute. The subject "
            "was told a date, the date passed, and the record still says "
            "cooling_off -- a Data Protection Act breach that looks like "
            "nothing at all, because nothing errored and the request is right "
            "there in the table looking orderly."
        ),
    ),
    ScheduledJob(
        func="accounts.retention.sweep_expired_documents",
        cron="0 2 * * *",
        queue="default",
        on_failure=(
            "National ID documents accumulate in object storage indefinitely. "
            "Nothing in the product changes and nothing errors. The first "
            "signal is a subject access request, a breach, or an audit -- all "
            "of which arrive after the exposure. THE ONE TO WIRE UP FIRST."
        ),
    ),
    ScheduledJob(
        func="reviews.jobs.reconcile_rating_aggregates",
        cron="0 3 * * *",
        queue="default",
        on_failure=(
            "Ratings drift from their source and nobody finds out. Every page "
            "still renders a number; the number is simply wrong, which on a "
            "platform selling trustworthy ratings is the worst failure there is."
        ),
    ),
    ScheduledJob(
        func="engagement.services.expire_stale_inquiries",
        cron="0 4 * * *",
        queue="default",
        on_failure=(
            "Unanswered inquiries sit in `sent` for ever, so a student cannot "
            "tell 'the landlord has not replied yet' from 'the landlord will "
            "never reply'. The screen looks identical either way."
        ),
    ),
    ScheduledJob(
        func="properties.jobs.prompt_stale_vacancies",
        cron="0 9 * * 1",
        queue="default",
        on_failure=(
            "Vacancy counts age and nobody is asked to refresh them. The "
            "listings quietly become misleading -- advertising last term's "
            "free rooms -- with no error anywhere, because a stale number is "
            "indistinguishable from a current one to everything except its "
            "own timestamp."
        ),
    ),
    ScheduledJob(
        func="properties.jobs.route_stale_distances",
        cron="*/15 * * * *",
        queue="default",
        on_failure=(
            "Walking times stop appearing. Listings still work; the number "
            "students actually care about is simply missing, and it looks "
            "identical to a provider with no route for that pair."
        ),
    ),
)
