# ADR-001: Multi-tenancy via shared database, shared schema

**Status:** Accepted
**Date:** 2026-08-23
**Amended:** 2026-08-24 — canonical host and header fallback resolved
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

The header fallback is gated on `TENANT_HEADER_FALLBACK_ENABLED`, `True` in
`dev.py` and `test.py`. In production it is not merely disabled but impossible
— see below.

### Public reads are canonical on a tenant-neutral host

Design review raised a case the first draft of this decision did not settle: a
property serving two nearby campuses is reachable at two hostnames, each showing
a different distance figure, and share-a-link behaviour then depends on which
subdomain the sender happened to be browsing. Canonical URLs, sitemaps and
search indexing all degrade when the same property has N addresses.

**Resolved: the tenant boundary sits in a different place for public reads than
for everything else.**

- **Public listing pages are canonical at `www.<domain>/listings/<slug>`.** That
  host is tenant-neutral. It is what sitemaps advertise and what search engines
  index.
- **University subdomains still serve the branded experience.** A student
  arriving at `kyu.example.co.ke/listings/wendani-hostel-c` gets KyU's colours,
  logo and campus distances. That page emits
  `<link rel="canonical" href="https://www.example.co.ke/listings/wendani-hostel-c">`
  so the neutral URL accumulates ranking and is the one shared onward.
- **Every write endpoint and every authenticated read stays strictly
  subdomain-scoped.** Nothing here relaxes the boundary where it carries
  security weight. The neutral host serves published public listing content and
  nothing else.

The subdomain is therefore a *presentation and default-filter* layer for
anonymous browsing, and an *access boundary* for everything touching user data.

### The header fallback must be impossible in production

`X-University` exists so `localhost:8080` and the test suite can name a tenant.
On a deployed host it would let any client set the header and read another
tenant's scoped data.

**Resolved: absence of the setting is not sufficient.**
`config/settings/prod.py` raises `ImproperlyConfigured` at import time if
`TENANT_HEADER_FALLBACK_ENABLED` is true, in the same way it already refuses to
start without a real `SECRET_KEY`. A container misconfigured this way must fail
to boot rather than serve traffic with a bypass available.

**Querysets** go through a manager, not through ad-hoc `.filter()` calls in
views:

```python
Property.objects.for_tenant(request.university)
```

Views that need cross-tenant access (platform staff tooling) use an explicitly
named escape hatch — `Property.objects.across_tenants()` — so that every such
site is greppable in one search.

## Constraint: session and CSRF cookies are host-only

**`SESSION_COOKIE_DOMAIN` and `CSRF_COOKIE_DOMAIN` must remain unset.** A
domain-wide cookie across the tenant subdomains contradicts the strict
subdomain scoping this ADR already requires, and it is refused at startup by a
system check rather than left as a convention.

The first reason is this ADR's own. Tenants are separated by subdomain, and the
separation is the product's security boundary: a session that is valid on
`kyu.` and `jkuat.` alike is a session that has stopped distinguishing the
thing the subdomains exist to distinguish. The pressure to set it is real —
somebody will want single sign-on across a student's two universities, or will
be debugging a login loop — and the answer is that cross-tenant identity is a
feature to design, not a cookie attribute to widen.

The second reason is the media host, and it is the one that turns a
questionable convenience into a vulnerability. Public listing photos are served
from object storage (ADR-007). Today that is R2's own hostname, so it shares no
cookies with the application by construction. Two ordinary changes remove that:

1. a branded media domain (`media.campusrentalfinder.co.ke`) configured as
   `custom_domain` on the storage backend, for CDN and appearance; and
2. `SESSION_COOKIE_DOMAIN = ".campusrentalfinder.co.ke"`, to share sessions
   across tenant subdomains.

Either alone is harmless. Together, the media host sits inside the
application's cookie scope, and any file that can be served with an
active content type — `image/svg+xml` carries script — becomes stored XSS
against the app, with the session cookie in reach.

Neither change is exotic and neither would look dangerous in review. So the
constraint is enforced at boot (`config.security_checks`), and the storage
layer refuses to generate a key whose extension could be served as active
content (ADR-007).

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

### Constraint: session and CSRF cookies are host-only

**`SESSION_COOKIE_DOMAIN` and `CSRF_COOKIE_DOMAIN` must remain unset.** A
domain-wide cookie across the tenant subdomains contradicts the strict
subdomain scoping this ADR already requires, and it is refused at startup by a
system check rather than left as a convention.

The first reason is this ADR's own. Tenants are separated by subdomain, and the
separation is the product's security boundary: a session that is valid on
`kyu.` and `jkuat.` alike is a session that has stopped distinguishing the
thing the subdomains exist to distinguish. The pressure to set it is real —
somebody will want single sign-on across a student's two universities, or will
be debugging a login loop — and the answer is that cross-tenant identity is a
feature to design, not a cookie attribute to widen.

The second reason is the media host, and it is the one that turns a
questionable convenience into a vulnerability. Public listing photos are served
from object storage (ADR-007). Today that is R2's own hostname, so it shares no
cookies with the application by construction. Two ordinary changes remove that:

1. a branded media domain (`media.campusrentalfinder.co.ke`) configured as
   `custom_domain` on the storage backend, for CDN and appearance; and
2. `SESSION_COOKIE_DOMAIN = ".campusrentalfinder.co.ke"`, to share sessions
   across tenant subdomains.

Either alone is harmless. Together, the media host sits inside the
application's cookie scope, and any file that can be served with an
active content type — `image/svg+xml` carries script — becomes stored XSS
against the app, with the session cookie in reach.

Neither change is exotic and neither would look dangerous in review. So the
constraint is enforced at boot (`config.security_checks`), and the storage
layer refuses to generate a key whose extension could be served as active
content (ADR-007).

## Consequences of the canonical-host resolution

- **Two URL shapes to maintain.** `www` for public listings, subdomains for
  everything else. The router and every link-building helper must know which
  host a route belongs to, and getting it wrong on a write endpoint is a
  security bug rather than a cosmetic one. Centralise host selection in one
  helper; never build these URLs by string concatenation at call sites.
- **The neutral host needs its own tenant story.** A property page on `www` has
  no subdomain to resolve from, so it cannot say "1.2 km from your campus". It
  shows every campus distance it holds, each labelled with its institution, and
  offers a "view as <university>" link into the branded subdomain.
- **Session cookies must be scoped to the exact subdomain, never to
  `.example.co.ke`.** A parent-domain cookie would be readable by every tenant
  subdomain, which is precisely the isolation failure this ADR exists to avoid.
- **Wildcard TLS and DNS are still required** for the branded hosts, plus a
  certificate covering `www`.
- The `rel=canonical` tag is easy to forget on a new page type. Assert it in a
  test for every public listing route.

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
