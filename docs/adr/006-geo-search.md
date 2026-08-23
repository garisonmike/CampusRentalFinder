# ADR-006: Geo search stays simple initially

**Status:** Accepted
**Date:** 2026-08-23
**Amended:** 2026-08-24 — no change to the decision; distance field split per ADR-002
**Deciders:** Tech lead

## Context

The primary query in the product is "properties near my campus, cheapest
first". A student does not draw a radius on a map; they pick a campus and a
budget.

The draft attempts a radius search anyway, in `rentals/views.py:160–175`:

```python
lat_delta = radius / 69                       # 69 statute miles per degree
lon_delta = radius / (69 * abs(lat / 90))     # ← divides by zero at the equator
```

Three problems: it works in miles, it is a bounding box presented as a radius
(corners are 1.41× the stated distance), and the longitude correction is
mathematically wrong — the factor should be `cos(latitude)`, not `lat / 90`.
As written, it **divides by zero on the equator**, which is where Kenya is.

PostGIS would fix all of that. It also adds a required PostgreSQL extension, a
GDAL/GEOS/PROJ dependency chain in the Docker image, `django.contrib.gis` with
its geometry field types, and a class of migration that is awkward to reverse.

## Decision

**Plain `latitude` / `longitude` floats on `Property`, plus the precomputed
`straight_line_km` in `PropertyCampusDistance` (ADR-002). No PostGIS.**

The decision itself is unchanged on review. Two notes carried over from ADR-002:
the distance column is now split into an always-present `straight_line_km` and
nullable routed fields, and the equator division-by-zero in the draft's
bounding-box maths is corrected below.

The campus-proximity query — the one that matters — becomes an indexed range
scan on a stored column with no geometry involved:

```python
Property.objects.filter(
    campus_distances__university=university,
    campus_distances__straight_line_km__lte=2.0,
).order_by("campus_distances__straight_line_km")
```

with `Index(fields=["university", "straight_line_km"])` on the join table.

`Property.latitude` / `longitude` are stored for two purposes: rendering a pin
on a map, and computing `straight_line_km` when the property or a campus moves.
**They are not queried directly.**

Where an ad-hoc radius search is genuinely needed, compute the bounding box
correctly and filter in Python for the exact distance:

```python
lat_delta = radius_km / 111.32
lon_delta = radius_km / (111.32 * math.cos(math.radians(latitude)))
```

`cos(radians(lat))`, not `lat / 90`. At Kenyan latitudes (−4.7° to 5.5°) the
cosine is ≈ 0.996, so this is very nearly a square grid — which is another
reason the exact geometry does not earn its keep here yet.

## Consequences

### What this buys us

- **No PostGIS.** The Docker image stays `python:3.13-slim` plus `libpq5`
  rather than pulling GDAL, GEOS and PROJ. The test database is a plain
  `postgres:16-alpine` with no extension to create, which keeps CI simple and
  fast.
- **The main query is a B-tree range scan** on a precomputed number. It is
  faster than a spatial index would be for this access pattern, because the work
  was done at write time.
- Any Postgres will do — including managed instances that do not offer PostGIS.
- `straight_line_km` is a plain number, so it is trivially testable and
  reviewable. Nobody has to reason about SRIDs.

### What it costs us

- **Distances are stale until recomputed.** Correcting a campus's coordinates
  requires re-deriving every join row that references it. This needs the
  `recompute_campus_distances` management command from ADR-002 to actually be
  written, and a signal on campus coordinate change to call it. Without that,
  the data rots silently — the worst failure mode, because nothing errors.
- **No true radius search on arbitrary points.** "Everything within 3 km of
  where I am standing" is a bounding box plus a Python filter, which does not
  scale past a few thousand candidate rows.
- **No spatial operations at all**: no polygons, no "inside this
  neighbourhood", no nearest-neighbour ordering, no distance in a SQL
  `ORDER BY` for arbitrary origins.
- **Straight-line distance under-states reality.** ADR-002 discusses this;
  a river or a motorway between the property and the gate makes 1.2 km "as the
  crow flies" a twenty-five-minute walk. This is a data-quality limit, not a
  technology one — PostGIS would give the same wrong answer faster.

### What would trigger a move to PostGIS

Concretely. Any one of these is sufficient:

1. **Map-viewport search.** The moment the product shows a map and filters to
   "properties in the visible rectangle" as the user pans, bounding box plus
   Python filtering stops being adequate — the candidate set is unbounded.
2. **Arbitrary-origin radius search at scale.** "Near me", "near this matatu
   stage", "near this landmark", where the origin is not a campus and so
   nothing can be precomputed. Once that is a real feature over more than
   ~5 000 properties, a GiST index is the right tool.
3. **Polygon queries.** Estate or ward boundaries — "show me everything in
   Kahawa Wendani" — where the shape is genuinely a polygon and not a radius.
   `ST_Contains` has no reasonable substitute.
4. **Distance ordering from a non-campus origin in SQL.** Needing
   `ORDER BY distance` for an arbitrary point, with pagination. Ordering in
   Python breaks pagination; this is the sharpest of the four triggers.
5. **Property count past roughly 50 000**, where the write-time precomputation
   in ADR-002 becomes the bottleneck instead of the optimisation.

The first three are product decisions. The last two are scale thresholds. **The
map viewport (1) is the most likely to arrive first** — it is a natural product
request and it is the one that most directly defeats this design.

### What that migration would involve

Deliberately spelled out, because the point of choosing the simple option is
knowing the exit cost.

1. **Database.** `CREATE EXTENSION postgis;` — a superuser operation, so a
   managed instance must support it. Add a Django migration with
   `django.contrib.postgres.operations.CreateExtension("postgis")`.
2. **Docker image.** `libgdal-dev`, `libgeos-dev`, `libproj-dev` in the build
   stage and their runtime libraries in the final layer. Expect the backend
   image to grow by roughly 150–250 MB, and the build to slow noticeably.
   The CI Postgres service becomes `postgis/postgis:16-3.4`.
3. **Settings.** `django.contrib.gis` in `INSTALLED_APPS`; database `ENGINE`
   changes to `django.contrib.gis.db.backends.postgis`. Note that psycopg 3 is
   supported by GeoDjango from Django 4.2 onwards, so the driver choice in the
   current requirements does not block this.
4. **Model.** Add `location = gis_models.PointField(geography=True, srid=4326,
   null=True)` **alongside** the existing floats. Do not replace them in the
   same step.
5. **Data migration.** Populate `location` from the existing pair:
   `Point(longitude, latitude, srid=4326)` — note the order, longitude first;
   getting it backwards is the classic GeoDjango bug and puts every Kenyan
   property in the Indian Ocean. Verify with a spot check against known
   coordinates before proceeding.
6. **Index.** `GistIndex(fields=["location"])`.
7. **Queries.** Rewrite radius searches to
   `filter(location__distance_lte=(origin, D(km=3)))` and ordering to
   `annotate(distance=Distance("location", origin)).order_by("distance")`.
   With `geography=True`, distances are in metres on a spheroid and are correct
   without projection juggling.
8. **`PropertyCampusDistance.straight_line_km` stays.** It becomes a cached
   denormalisation rather than the source of truth — still the fastest way to
   serve the primary query, now with a way to verify it. Add a periodic
   consistency check comparing the stored value to `ST_Distance`.
9. **Drop the float columns** only after everything reads `location`, in a
   separate release.

Realistically: two to three days of work, most of it in steps 2 and 5. The
cost is genuine but bounded, and it does not require redesigning the schema —
which is precisely why deferring is defensible rather than merely cheap.

## Alternatives considered

### PostGIS from the start — rejected

Correct, and premature. The dominant query is campus proximity, which is
precomputable, so the spatial index would sit unused while its dependencies
slowed every build. The migration path above is well-trodden, so the option
stays open at a known price.

### `django-postgres-extensions` / cube+earthdistance — rejected

PostgreSQL's `earthdistance` extension gives radius search without full
PostGIS. Lighter, but still an extension, still a GiST index, and it stops at
radius queries — no polygons, no viewport. It buys the smaller half of PostGIS
for most of the operational cost.

### Geohash prefix matching — rejected

Encode lat/lng as a geohash string, index it, prefix-match for proximity. No
extensions and it works on any database. Rejected because the accuracy is
awkward (cells are rectangular and vary with latitude), points near a cell
boundary are missed unless neighbours are also queried, and the resulting code
is harder to read than either alternative. Precomputed distances give a better
answer with less machinery.

### External geo service (Elasticsearch, Algolia Places) — rejected

Another service to run and pay for, another data sync to keep consistent, and
another failure mode on the platform's primary query. Not at this scale.
