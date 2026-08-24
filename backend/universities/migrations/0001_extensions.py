"""PostgreSQL extensions the schema depends on.

Kept in its own migration, ahead of any model, because creating an extension
needs database superuser rights and a deploy may have to run it separately.
"""

from django.contrib.postgres.operations import BtreeGistExtension
from django.db import migrations


class Migration(migrations.Migration):
    initial = True

    dependencies: list[tuple[str, str]] = []

    operations = [
        # Required by the ExclusionConstraint on Tenancy (ADR-004), which stops
        # two confirmed tenancies overlapping on the same unit. A serializer
        # cannot see a concurrent insert; the constraint can.
        BtreeGistExtension(),
    ]
