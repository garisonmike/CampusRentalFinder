# ADR-002: Properties link to universities through a join model

**Status:** Accepted
**Date:** 2026-08-23
**Amended:** 2026-08-24 — distance population resolved; equator bug recorded
**Deciders:** Tech lead

## Context

The draft schema has `Rental.distance_to_campus`, a nullable float in miles,
with no indication of *which* campus. It assumes one property serves exactly
one institution, and that the institution is implied.

Neither assumption holds. Around Nairobi, Juja and Eldoret, campuses cluster:
a hostel on Thika Road is a fifteen-minute walk from one campus and a
twenty-minute matatu ride from another. Students at both would consider it.
Under a single FK, the landlord must either pick one university and lose the
other market, or create duplicate listings that immediately drift apart in
vacancy count, price and photos.

Distance is also not a property of the property. It is a property of the
*pair* — and so is walking time, which is what students actually ask about and
which does not follow straight-line distance in a place with a river, a
motorway or a fence in the way.

## Decision

A **join model, `PropertyCampusDistance`**, connects `Property` to `University`
and carries the attributes of the relationship:

```
PropertyCampusDistance
├── property             FK → Property   (on_delete=CASCADE)
├── university           FK → University (on_delete=PROTECT)
├── campus_name          the specific campus, e.g. "Main Campus"
├── straight_line_km     Decimal(5,2), NOT NULL — haversine, computed on save
├── walking_distance_km  Decimal(5,2), NULL until routed
├── walking_minutes      PositiveSmallInteger, NULL until routed
├── routed_at            DateTimeField, NULL — when the routing job last ran
├── is_primary           one per property: the campus it is marketed against
├── created_at, updated_at
└── UNIQUE (property, university, campus_name)
```

### Straight-line now, routed later, never faked

Design review flagged that the original single `distance_km` field said nothing
about *how* it was populated, and that the three plausible answers have very
different trust properties: landlord-entered numbers are gamed immediately;
haversine systematically understates real walking distance where a river, a
motorway or a fence forces a detour; and routing costs money per call.

**Resolved: the field is split, and the two halves have different rules.**

- **`straight_line_km` is always present.** Computed by haversine on save from
  the property and campus coordinates. Free, deterministic, testable, and it is
  an honest lower bound.
- **`walking_distance_km` and `walking_minutes` are nullable and are populated
  *only* by an asynchronous routing job.** They stay null until that job runs.
- **Walking time is never derived from straight-line distance.** Not by dividing
  by 5 km/h, not by any fudge factor. A null walking time renders as "—"; a
  fabricated one erodes exactly the trust the platform is selling.
- **Any UI showing straight-line distance must label it as such** — "1.2 km
  direct", not "1.2 km". The two numbers mean different things and the interface
  must not let a reader conflate them.

The first routing provider is **OpenRouteService**, whose free tier covers the
expected volume. The job talks to a `RouteProvider` interface with a single
`route(origin, destination) -> RouteResult | None` method, so swapping to
Mapbox, Google or a self-hosted OSRM is a settings change and one new class.
A provider returning nothing leaves the fields null, which is a supported state
rather than an error.

The job runs on the queue adopted in ADR-007.

### The bounding-box longitude term

The draft's ad-hoc radius search computed `lon_delta = radius / (69 * abs(lat / 90))`.
That is wrong twice: 69 is statute miles per degree, and the latitude correction
should be the cosine of the latitude, not a linear ratio. As written it
**divides by zero at the equator**, which is where Kenya is.

The correct form, in kilometres:

```python
lat_delta = radius_km / 111.32
lon_delta = radius_km / (111.32 * math.cos(math.radians(latitude)))
```

See ADR-006, which also notes that at Kenyan latitudes the cosine is ≈ 0.996,
so the correction is nearly a no-op — the bug was never going to be caught by
a plausible-looking result.

### Query shape

One property can carry as many rows as there are campuses it plausibly serves.
The tenant-scoped property queryset (ADR-001) filters through this join:

```python
Property.objects.filter(campus_distances__university=request.university)
```

Distances are **computed once and stored**, not calculated per request. See
ADR-006 for why no spatial extension is involved.

## Consequences

### What this buys us

- A landlord lists a property **once**. Vacancy, price and photos have exactly
  one home, so they cannot disagree between institutions.
- "Within 2 km of my campus" is a plain indexed range query on
  `straight_line_km`, filtered by the tenant — the single most important query
  in the product, and it is cheap.
- Walking time, when present, is a routed value rather than a client-side guess
  from a straight line. On terrain with a river between the property and the
  gate, this is the difference between a useful number and a misleading one —
  and when it is absent, the interface says so rather than inventing one.
- `campus_name` lets a multi-campus university distinguish its sites, which
  matters for institutions whose campuses are in different towns entirely.

### What it costs us

- **Every property query now needs a join.** Cheap with an index on
  `(university, straight_line_km)`, but it is one more thing to get wrong: a
  forgotten `select_related`/`prefetch_related` here produces N+1 queries on
  the busiest page in the application. Assert query counts in the list-view
  tests.
- **Stored distances can go stale.** If a campus's coordinates are corrected,
  every join row referencing it needs recomputing. A management command
  (`recompute_campus_distances`) plus a signal on `Campus` coordinate change
  covers this; without one, the data quietly rots.
- **The join can produce duplicate rows in a listing.** A property serving two
  campuses of the *same* university matches twice in the filter above. Every
  such queryset needs `.distinct()`, and `.distinct()` interacts badly with
  `ORDER BY` on a joined column. Prefer filtering by the primary join row, or
  annotate with `Min('campus_distances__straight_line_km')` and order on the
  annotation.
- **Nothing in the schema forces a property to have any join row at all.** A
  property with zero `PropertyCampusDistance` rows is invisible to every tenant
  — a listing the landlord created that nobody can see. Enforce at least one
  row at creation time in the serializer, and add a monitoring query for
  orphans; a database constraint cannot express "at least one related row".
- **`is_primary` needs a constraint, not a convention.** Use a
  `UniqueConstraint` with `condition=Q(is_primary=True)` on `property` so a
  second primary is a database error rather than a display bug.

### Consequences of the distance split

- **Two distance columns to keep straight**, and a UI that must never print the
  wrong one. Name them explicitly at every layer — no bare `distance` anywhere
  in serializers, query params or component props.
- **Most rows will have null walking figures for some time.** Every consumer
  must handle null, and the primary sort stays on `straight_line_km` because it
  is the only field guaranteed present.
- **A routing quota is now an operational concern.** OpenRouteService's free
  tier is generous but finite; the job needs backoff, a per-day cap, and a
  metric so exhaustion is visible rather than silent.
- **`straight_line_km` can go stale** when a campus's coordinates are corrected.
  The `recompute_campus_distances` management command plus a signal on campus
  coordinate change covers this. Without it the data rots quietly, which is the
  worst failure mode because nothing errors.
- Routing results also age — a new footbridge changes the answer. `routed_at`
  exists so a periodic refresh can find the oldest rows first.

## Alternatives considered

### Single FK `Property.university` — rejected

What the draft effectively has. Forces duplicate listings for shared
properties, and duplicates drift. Also puts a relationship attribute
(distance) on an entity, which is the modelling error that caused the problem.

### `ManyToManyField` without a `through` model — rejected

Solves multiplicity but has nowhere to store the distance fields or
`campus_name`. Django would let us add a `through` model later, but not
without a migration that rebuilds the table — cheaper to start with the
explicit model.

### JSON column of campus distances on `Property` — rejected

`{"kyu": {"km": 1.2, "walk": 15}}`. No referential integrity, no index for the
range query that matters most, and no way to constrain the shape. Would make
the platform's primary filter a sequential scan.

### Computing distance on the fly with PostGIS — deferred, see ADR-006

Correct in the long run, and premature now. The join model is compatible with
it: when PostGIS arrives, `straight_line_km` becomes a cached denormalisation
of a `ST_Distance` call rather than the source of truth, and the column stays.
