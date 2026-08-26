"""Rebuild rating aggregates from `Review` (ADR-004)."""

from __future__ import annotations

from django.core.management.base import BaseCommand

from reviews.recompute import (
    recompute_all,
    recompute_landlord,
    recompute_property,
    recompute_unit,
)


class Command(BaseCommand):
    help = "Recompute rating aggregates from Review. Rebuilds fully by default."

    def add_arguments(self, parser):
        parser.add_argument("--property", type=int, help="Recompute one property.")
        parser.add_argument("--unit", type=int, help="Recompute one unit.")
        parser.add_argument("--landlord", type=int, help="Recompute one landlord.")

    def handle(self, *args, **options):
        # Deliberately the same functions the job calls. One implementation,
        # two entry points -- otherwise the rebuild and the incremental update
        # drift and only one of them is right, with no way to tell which.
        targeted = False

        for key, recompute in (
            ("property", recompute_property),
            ("unit", recompute_unit),
            ("landlord", recompute_landlord),
        ):
            if options.get(key):
                recompute(options[key])
                self.stdout.write(f"Recomputed {key} {options[key]}.")
                targeted = True

        if targeted:
            return

        counts = recompute_all()
        self.stdout.write(
            self.style.SUCCESS(
                "Recomputed {units} unit(s), {properties} propert(ies), "
                "{landlords} landlord(s).".format(**counts)
            )
        )
