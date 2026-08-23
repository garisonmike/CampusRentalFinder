# ADR-002: Properties link to universities through a join model

**Status:** Accepted
**Date:** 2026-08-23
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
├── property          FK → Property   (on_delete=CASCADE)
├── university        FK → University (on_delete=PROTECT)
├── campus_name       the specific campus, e.g. "Main Campus", "Karen Campus"
├── distance_km       Decimal(5,2), straight-line or routed — see below
├── walking_minutes   PositiveSmallInteger, nullable
├── is_primary        one per property: the campus it is marketed against
├── created_at, updated_at
└── UNIQUE (property, university, campus_name)
```

One property can carry as many rows as there are campuses it plausibly serves.
The tenant-scoped property queryset (ADR-001) filters through this join:

```python
Property.objects.filter(campus_distances__university=request.university)
```

Distance and walking time are **computed once and stored**, not calculated per
request. See ADR-006 for how.

## Consequences

### What this buys us

- A landlord lists a property **once**. Vacancy, price and photos have exactly
  one home, so they cannot disagree between institutions.
- "Within 2 km of my campus" is a plain indexed range query on `distance_km`,
  filtered by the tenant — the single most important query in the product, and
  it is cheap.
- Walking time is a first-class stored value rather than a client-side guess
  from a straight line. On terrain with a river between the property and the
  gate, this is the difference between a useful number and a misleading one.
- `campus_name` lets a multi-campus university distinguish its sites, which
  matters for institutions whose campuses are in different towns entirely.

### What it costs us

- **Every property query now needs a join.** Cheap with an index on
  `(university, distance_km)`, but it is one more thing to get wrong: a
  forgotten `select_related`/`prefetch_related` here produces N+1 queries on
  the busiest page in the application. Assert query counts in the list-view
  tests.
- **`distance_km` can go stale.** If a campus's coordinates are corrected, every
  join row referencing it needs recomputing. A management command
  (`recompute_campus_distances`) plus a signal on `Campus` coordinate change
  covers this; without one, the data quietly rots.
- **The join can produce duplicate rows in a listing.** A property serving two
  campuses of the *same* university matches twice in the filter above. Every
  such queryset needs `.distinct()`, and `.distinct()` interacts badly with
  `ORDER BY` on a joined column. Prefer filtering by the primary join row, or
  annotate with `Min('campus_distances__distance_km')` and order on the
  annotation.
- **Nothing in the schema forces a property to have any join row at all.** A
  property with zero `PropertyCampusDistance` rows is invisible to every tenant
  — a listing the landlord created that nobody can see. Enforce at least one
  row at creation time in the serializer, and add a monitoring query for
  orphans; a database constraint cannot express "at least one related row".
- **`is_primary` needs a constraint, not a convention.** Use a
  `UniqueConstraint` with `condition=Q(is_primary=True)` on `property` so a
  second primary is a database error rather than a display bug.

### A flaw worth stating plainly

The ADR specifies `distance_km` and `walking_minutes` without saying **how they
are populated**, and that gap will surface on day one of implementation. Three
options, with different costs:

1. **Landlord-entered.** Zero infrastructure, and immediately gamed — every
   listing becomes "5 minutes from the gate".
2. **Computed straight-line (haversine) on save.** Free, deterministic,
   testable. Systematically *under*-states real distance, and badly so where a
   river or a motorway forces a detour. `walking_minutes` derived from it
   (÷ 5 km/h) inherits the error.
3. **Routing API** (Google/Mapbox/OSRM). Accurate, costs money per call, adds
   an external dependency to the write path.

**Recommendation:** compute haversine `distance_km` on save as the reliable
floor, leave `walking_minutes` **null** rather than deriving it from a straight
line, and populate walking time asynchronously from a routing API where budget
allows. A null walking time renders as "—"; a wrong one erodes exactly the
trust the platform is selling. **This needs a decision before the schema
rewrite starts**, because option 3 implies a job queue, which is also
ADR-007's open question.

## Alternatives considered

### Single FK `Property.university` — rejected

What the draft effectively has. Forces duplicate listings for shared
properties, and duplicates drift. Also puts a relationship attribute
(`distance`) on an entity, which is the modelling error that caused the problem.

### `ManyToManyField` without a `through` model — rejected

Solves multiplicity but has nowhere to store `distance_km`, `walking_minutes`
or `campus_name`. Django would let us add a `through` model later, but not
without a migration that rebuilds the table — cheaper to start with the
explicit model.

### JSON column of campus distances on `Property` — rejected

`{"kyu": {"km": 1.2, "walk": 15}}`. No referential integrity, no index for the
range query that matters most, and no way to constrain the shape. Would make
the platform's primary filter a sequential scan.

### Computing distance on the fly with PostGIS — deferred, see ADR-006

Correct in the long run, and premature now. The join model is compatible with
it: when PostGIS arrives, `distance_km` becomes a cached denormalisation of a
`ST_Distance` call rather than the source of truth, and the column stays.
