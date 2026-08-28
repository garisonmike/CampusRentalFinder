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
            # jobs this table owns are cleared -- including held ones, so
            # holding a job actually removes it from a scheduler that already
            # has it rather than merely declining to add it again.
            owned = {job.func for job in SCHEDULE}
            for existing in scheduler.get_jobs():
                if existing.func_name in owned:
                    scheduler.cancel(existing)

        installed = 0

        for job in SCHEDULE:
            if not job.enabled:
                # Printed loudly, with the reason. A held job that installs
                # silently as a no-op is indistinguishable from a job nobody
                # wrote, and the next person to read the table would enable it
                # without knowing what it does when it runs.
                self.stdout.write(self.style.WARNING(f"HELD      {job.func}"))
                self.stdout.write(f"          {job.held_because}")
                continue

            self.stdout.write(f"{job.cron}  {job.func}  [{job.queue}]")
            installed += 1
            if scheduler is not None:
                scheduler.cron(job.cron, func=job.func, queue_name=job.queue)

        held = len(SCHEDULE) - installed
        verb = "Would schedule" if options["dry_run"] else "Scheduled"
        summary = f"{verb} {installed} job(s)."
        if held:
            summary += f" {held} held and NOT installed -- see the reasons above."
        self.stdout.write(self.style.SUCCESS(summary))
