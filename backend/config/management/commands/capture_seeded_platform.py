"""Capture real API responses from the seeded platform, for the frontend tests.

`frontend/src/test/seeded-platform.json` is the only fixture in the frontend
suite that nobody wrote. Every hand-written one was shaped by the assertion it
was made for, so the shapes that break a page are exactly the ones no fixture
has; this file arrives with whatever properties the seed happens to produce.

It was captured by hand once, and that is the problem this command fixes. A
hand capture is a photograph of the API on one afternoon: when a serializer is
corrected, the capture keeps serving the old answer, and the frontend test that
runs against it stays green while asserting the bug. That is exactly what
happened -- the capture carried `"stay_months": 12` for a three-day stay, taken
before the stay-length fix, and it outlived the fix by two rounds.

So: regeneration is a command, not a procedure somebody remembers.

    ./manage.py capture_seeded_platform --flush

Deterministic: it seeds with a fixed `--seed` and writes the same bytes given
the same code. `--check` re-captures and diffs instead of writing, which is
what CI runs -- a capture that has drifted from the API is a frontend suite
testing a shape the backend no longer returns.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count, Q
from django.test import Client, override_settings

#: Fixed, so the capture is reproducible rather than merely recent.
CAPTURE_SEED = 20260828

DESTINATION = (
    Path(__file__).resolve().parents[4] / "frontend" / "src" / "test" / "seeded-platform.json"
)


class Command(BaseCommand):
    help = "Seed the platform and capture real API responses for the frontend tests."

    def add_arguments(self, parser):
        parser.add_argument(
            "--check",
            action="store_true",
            help="Re-capture and diff against the committed file instead of writing it. "
            "Non-zero if they differ.",
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Flush before seeding. Without it the seed runs on top of whatever "
            "is there, and the capture is not reproducible.",
        )

    def handle(self, *args, **options):
        if options["flush"]:
            call_command("seed_platform", seed=CAPTURE_SEED, flush=True, verbosity=0)
        else:
            self.stdout.write(
                "Capturing without --flush: the result is only reproducible if the "
                "database already holds exactly this seed."
            )

        # The capture speaks to the app over subdomains the dev settings do not
        # list. Widened here rather than in the settings: a permanent wildcard
        # to make one command work is how a host check stops being a check.
        with override_settings(ALLOWED_HOSTS=["*"]):
            captured = self.capture()
        rendered = json.dumps(captured, indent=2, sort_keys=False, ensure_ascii=False) + "\n"

        if options["check"]:
            existing = DESTINATION.read_text() if DESTINATION.exists() else ""
            if existing != rendered:
                raise CommandError(
                    f"{DESTINATION.name} is stale: the API returns something other than "
                    f"what the frontend suite tests against. Re-run without --check."
                )
            self.stdout.write(self.style.SUCCESS("The capture matches the API."))
            return

        DESTINATION.write_text(rendered)
        self.stdout.write(self.style.SUCCESS(f"Wrote {DESTINATION}"))

    # -- the capture itself --------------------------------------------------

    def capture(self) -> dict[str, Any]:
        from django.urls import reverse

        from properties.models import Property
        from universities.models import University

        # The subdomain the seed's richest catalogue lives under. Requests go
        # through the real stack -- middleware, tenant resolution, permissions,
        # serializers -- because a capture that bypassed them would be a
        # hand-written fixture again, just a more expensive one.
        #
        # Routes come from `reverse`, not from literals. A capture holding
        # hard-coded paths keeps returning 200 from a URL the app has moved,
        # or starts failing for a reason that has nothing to do with the data.
        kyu = University.objects.get(subdomain="kyu")
        host = f"{kyu.subdomain}.localhost"
        client = Client()

        # Which property to capture is the whole question.
        #
        # "The one with the most reviews" was the obvious rule and it is wrong:
        # it selects for a *complete* property, and the shapes the frontend
        # tests need are the incomplete ones -- a unit whose vacancy nobody has
        # ever stated, a campus join the routing job has not reached. Picking
        # the best-looking row rebuilds by accident the fixture this file
        # exists to avoid.
        #
        # So: score candidates on the awkward branches they carry, and break
        # ties on reviews. Then assert the result actually has them, below.
        candidates = (
            Property.all_objects.filter(
                campus_distances__campus__university=kyu, status="published"
            )
            .annotate(
                never_stated=Count(
                    "units", filter=Q(units__vacant_count_updated_at__isnull=True), distinct=True
                ),
                no_route=Count(
                    "campus_distances",
                    filter=Q(campus_distances__walking_minutes__isnull=True),
                    distinct=True,
                ),
                reviews=Count("units__tenancies__review", distinct=True),
            )
            .order_by("-never_stated", "-no_route", "-reviews", "id")
        )
        subject = candidates.first()
        if subject is None:
            raise CommandError("The seed produced no published property to capture.")
        if not (subject.never_stated and subject.no_route and subject.reviews):
            raise CommandError(
                "No seeded property carries all three branches the frontend tests need "
                f"(never-stated vacancy: {subject.never_stated}, missing walking route: "
                f"{subject.no_route}, reviews: {subject.reviews}). Capturing anyway would "
                "produce a file that renders only the happy path and looks like it "
                "proved something."
            )
        slug = subject.slug

        return {
            "listing": self.get(client, reverse("properties:property-list"), host),
            "detail": self.get(client, reverse("properties:property-detail", args=[slug]), host),
            "reviews": self.get(client, reverse("reviews:property-reviews", args=[slug]), host),
            "rating": self.get(client, reverse("reviews:property-rating", args=[slug]), host),
            "themes": {
                university.subdomain: self.get(
                    client,
                    reverse("universities:tenant-config"),
                    f"{university.subdomain}.localhost",
                )
                for university in University.objects.order_by("subdomain")
            },
        }

    def get(self, client: Client, path: str, host: str) -> Any:
        response = client.get(path, headers={"host": host})
        if response.status_code != 200:
            raise CommandError(f"{path} on {host} returned {response.status_code}, not 200.")
        return response.json()
