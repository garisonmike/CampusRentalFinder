"""Install the recurring jobs in :data:`config.jobs.schedule.SCHEDULE`."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from config.jobs.schedule import SCHEDULE


class Command(BaseCommand):
    help = "Register every scheduled job with rq-scheduler, idempotently."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be scheduled without touching Redis.",
        )

    def handle(self, *args, **options):
        import django_rq

        scheduler = None if options["dry_run"] else django_rq.get_scheduler("default")

        if scheduler is not None:
            # Idempotent: re-running after a deploy must not double up. Only
            # jobs this table owns are cleared.
            owned = {job.func for job in SCHEDULE}
            for existing in scheduler.get_jobs():
                if existing.func_name in owned:
                    scheduler.cancel(existing)

        for job in SCHEDULE:
            self.stdout.write(f"{job.cron}  {job.func}  [{job.queue}]")
            if scheduler is not None:
                scheduler.cron(job.cron, func=job.func, queue_name=job.queue)

        verb = "Would schedule" if options["dry_run"] else "Scheduled"
        self.stdout.write(self.style.SUCCESS(f"{verb} {len(SCHEDULE)} job(s)."))
