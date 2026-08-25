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
