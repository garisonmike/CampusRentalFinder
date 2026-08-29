"""
Join published properties to campuses they are near but not joined to.

**An operator action, deliberately, rather than a signal on campus creation.**

The absence is real: `route_stale_distances` walks the rows that exist, so a
campus created after a property was published never gets one -- no row, no
routing, and the listing is invisible to that campus permanently, with nothing
erroring. That is the silent-invisibility failure the publish gate exists to
prevent, arriving through a different door.

Repairing it automatically was the obvious move and it is the wrong one,
because **the repair changes who can see what**. A join makes a property
visible to a university's students; creating one on every nearby campus save
turns tenant visibility into a function of geography that nobody decided. Two
universities 12 km apart would begin sharing every listing between them the
moment a campus row was saved, and the first anyone would know is a landlord
asking why their listing appears on a site they never heard of.

So: the reconciler counts the absences and alerts (docs/OPERATIONS.md), and
this command repairs them when somebody has looked. `--dry-run` first, which is
the default, because the interesting output is the list.

The property side is different and is automatic: `publish()` joins the campuses
that already exist, because that is the landlord's own act on their own
property, and without it the publish gate was unsatisfiable by any available
action.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from properties.models import Property
from properties.services import (
    backfill_campus_joins,
    join_radius_for,
    properties_missing_a_join_to,
)
from universities.models import Campus


class Command(BaseCommand):
    help = "Report, and optionally create, missing property-to-campus joins."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Create the rows. Without it this only reports, which is the "
            "output worth reading: the repair changes who can see what.",
        )
        parser.add_argument(
            "--campus",
            type=int,
            help="One campus id. Without it, every campus is examined.",
        )

    def handle(self, *args, **options):
        campuses = Campus.all_objects.select_related("university")
        if options["campus"]:
            campuses = campuses.filter(pk=options["campus"])

        self.stdout.write(
            f"Default join radius: {settings.CAMPUS_JOIN_RADIUS_KM} km "
            f"(CAMPUS_JOIN_RADIUS_KM). Campuses may set their own.\n"
        )

        total = 0
        for campus in campuses:
            missing = properties_missing_a_join_to(campus)
            if not missing:
                continue

            total += len(missing)
            # The campus's own radius, named, because a reader deciding
            # whether to apply this needs to know which number produced the
            # list -- the platform default or a choice somebody made here.
            radius = join_radius_for(campus)
            source = "campus" if campus.join_radius_km is not None else "default"
            self.stdout.write(
                self.style.WARNING(
                    f"{campus.university.name} / {campus.name} "
                    f"[{radius:g} km, {source}]: "
                    f"{len(missing)} published propert"
                    f"{'y' if len(missing) == 1 else 'ies'} in range with no join"
                )
            )
            for name, slug in Property.all_objects.filter(pk__in=missing).values_list(
                "name", "slug"
            )[:10]:
                self.stdout.write(f"    {slug}  ({name})")

            if options["apply"]:
                created = backfill_campus_joins(campus)
                self.stdout.write(self.style.SUCCESS(f"    created {created} join(s)"))

        if total == 0:
            self.stdout.write(self.style.SUCCESS("Every published property in range is joined."))
        elif not options["apply"]:
            self.stdout.write(
                "\nNothing was changed. Re-run with --apply once you have "
                "decided these properties should be visible to these campuses."
            )
