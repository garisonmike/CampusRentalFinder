# ADR-001: Multi-tenancy via shared database, shared schema

**Status:** Accepted
**Date:** 2026-08-23
**Deciders:** Tech lead

## Context

CampusRentalFinder is sold per-university. Each institution gets its own
branded entry point (`kyu.example.co.ke`, `jkuat.example.co.ke`) and sees a
catalogue scoped to properties near its campuses. The current schema has no
concept of a university at all — the tenant boundary does not exist in code.

Three facts shape the decision:

1. **The team is small.** One or two developers. Operational overhead has to
   stay near zero.
2. **Properties are shared between tenants.** Nairobi has universities whose
   campuses sit a few kilometres apart. A hostel between them is genuinely
   relevant to students at both, and the landlord must list it once.
3. **The data is not sensitive in the regulated sense.** Rental listings,
   reviews and enquiries. There is no clinical or financial data forcing
   physical isolation.

## Decision

A **`University` model is the tenant.** Every tenant-scoped row carries a route
to a university, and every tenant-scoped queryset is filtered by the university
resolved for the current request.

**Resolution order**, implemented by a middleware that populates request state:

1. **Subdomain** of the request host — `kyu.example.co.ke` → the `University`
   whose `subdomain` field is `kyu`. This is the production path.
2. **`X-University` header** carrying a subdomain slug — the fallback for local
   development, where `localhost:8080` has no usable subdomain, and for the
   automated test suite.
3. **Unresolved** — the request proceeds with no tenant. Only endpoints
   explicitly marked tenant-optional (the theming config lookup in ADR-005,
   health probes, the schema, auth) may serve such a request; every
   tenant-scoped view returns 400.

The header fallback **must be disabled in production settings.** If it were
honoured on a deployed host, any client could set `X-University: <anything>`
and read another tenant's scoped data. It is gated on a setting that is `True`
in `dev.py` and `test.py` and `False` in `prod.py`.

**Querysets** go through a manager, not through ad-hoc `.filter()` calls in
views:

```python
Property.objects.for_tenant(request.university)
```

Views that need cross-tenant access (platform staff tooling) use an explicitly
named escape hatch — `Property.objects.across_tenants()` — so that every such
site is greppable in one search.

## Consequences

### What this buys us

- One database, one migration run, one connection pool, one backup. A new
  university is a row, provisioned in seconds, not a schema migration.
- Cross-tenant reporting ("how many listings platform-wide?") is a plain query.
- A property genuinely near two campuses is one row with two join records
  (ADR-002), which is the behaviour the product needs.

### What it costs us

- **Isolation is enforced by application code, so a single missing filter leaks
  data across tenants.** This is the central risk of the approach and it is
  real. Mitigations, all of which are load-bearing:
  - The tenant-scoped manager's default `get_queryset()` raises rather than
    returning unfiltered rows, so forgetting to scope is a loud error, not a
    silent leak.
  - A test that walks every registered DRF viewset, asserts its queryset model
    is either tenant-scoped or explicitly allow-listed, and fails on new models
    that are neither. Without this, the guarantee decays the first time someone
    adds a model in a hurry.
  - Every tenant-scoped endpoint gets a cross-tenant negative test. This is
    non-negotiable in review.
- **A noisy tenant affects everyone.** One university with an unusual query
  volume shares the connection pool with the rest. Acceptable at the scale we
  are planning for; revisit past roughly 50 institutions.
- **`request.university` is ambient state.** It is convenient and it is also
  the kind of implicit context that makes a function's behaviour depend on
  something not in its signature. Keep the middleware thin: it resolves and
  stores, nothing more. Service functions take the university as an explicit
  argument rather than reaching for the request.
- **Deleting a university is dangerous.** With `on_delete=CASCADE` on the join
  model, removing a `University` row silently unlinks properties. Use
  `PROTECT`, and deactivate rather than delete.
- **Subdomains need a wildcard TLS certificate** (`*.example.co.ke`) and a
  wildcard DNS record. Cheap, but it is a prerequisite for the first deploy,
  not an afterthought.

### A flaw worth stating plainly

Resolving the tenant purely from the subdomain conflicts with the shared-property
requirement in an edge case the ADR does not settle. If a student on
`kyu.example.co.ke` opens a property that also serves JKUAT, the page shows
"1.2 km from KyU". Share that link with a JKUAT friend and they see the same
KyU-scoped page, because the subdomain in the URL — not the viewer — decides
the tenant. That is *probably* fine (the property is genuinely relevant to
both, and the distances are attributes of the join, not the property). But
canonical URLs, sitemaps and SEO all get messy when the same property is
reachable at N hostnames with N different distance figures.

**Recommendation, for the tech lead to rule on:** serve properties from a
tenant-neutral canonical host and treat the university subdomain as a
*preference* layer (branding, default filters, distance display) rather than an
access boundary for public listing pages. Keep strict subdomain scoping for
authenticated and write endpoints, where it matters. This is not a change to
the decision — it is a question about where the boundary sits for public reads,
and it should be answered before the URL structure is built, because it is
expensive to change afterwards.

## Alternatives considered

### django-tenants (schema-per-tenant) — rejected

PostgreSQL schema isolation, one schema per university, `search_path` switched
per request.

- **Migration pain.** Every `migrate` runs once per schema. At 20 universities
  a routine migration becomes 20 sequential runs, and a partial failure leaves
  tenants on different schema versions. For a one-to-two-person team this is
  the dominant cost.
- **It makes the shared-property case awkward, which is the killer.** A hostel
  between two campuses must be duplicated into both schemas, and the two copies
  then drift: two vacancy counts, two review sets, two photo sets, and no
  correct answer to "how many units are free?".
- Cross-tenant reporting needs a loop over schemas or a materialised
  cross-schema view.
- Connection pooling interacts badly with per-request `search_path` switching.

The isolation guarantee is genuinely stronger. It is not worth the two costs
above at this scale.

### Database-per-tenant — rejected

Strongest isolation, highest operational cost: N databases to provision, back
up, monitor and migrate. Untenable for this team, and it makes shared
properties impossible rather than merely awkward.

### Path-based tenancy (`/kyu/properties/`) — rejected

Avoids the wildcard-certificate requirement, but every URL in the application
grows a tenant segment, cookies are shared across tenants (so a session leaks
between them unless carefully scoped), and per-tenant branding on a shared
origin is harder to cache. Subdomains give each tenant a real origin, which is
the cleaner boundary.

### No tenancy; filter by university on each query — rejected

This is the status quo of the draft, extended. It is the same shared-schema
model with none of the enforcement, and it decays into the leak scenario
immediately. The manager and the middleware are what make the decision hold.
