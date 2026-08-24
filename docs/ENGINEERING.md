# CampusRentalFinder — Engineering Guide

Students find rentals near their campus without walking around town hunting.
Landlords and caretakers post listings with photos and vacancy counts; former
tenants leave reviews. Sold per-university, each with its own branding.

**Launch market is Kenya.** KES, county/town/estate, kilometres, and property
types that exist here (bedsitter, single room, one/two bedroom, hostel block).
If you find dollars, miles, ZIP codes or `state` in new code, it is wrong.

---

## Stack

**Backend** — Python 3.13, Django 5.2 LTS, DRF 3.16, PostgreSQL 16, Redis 7.
JWT via `djangorestframework-simplejwt` (with `token_blacklist`), OpenAPI via
`drf-spectacular`, filtering via `django-filter`, logging via `structlog`.

**Frontend** — React 18, TypeScript, Vite 6, Tailwind + a small set of shadcn
primitives, Zustand, TanStack Query, axios, React Router 6. API types are
**generated** from the OpenAPI schema by openapi-typescript.

**Queue** — django-rq on the same Redis (ADR-007). Four jobs depend on it:
tenancy auto-confirmation, campus routing, verification-document retention, and
image variants.

**Tooling** — `ruff` (lint **and** format — no black, no isort, no flake8),
`mypy` (non-strict), `pytest` + `pytest-django` + `factory_boy`, `vitest` +
Testing Library, `pre-commit`, GitHub Actions.

> Django was moved from the original 4.2.7 pin to 5.2 LTS during the hardening
> pass: 4.2 does not support Python 3.13 and `psycopg2-binary` 2.9.9 has no
> 3.13 wheel. The driver is now `psycopg[binary]` 3.x. See `docs/AUDIT.md` §6.

---

## Layout

```
backend/
  config/                    project package (was rental_platform)
    settings/{base,dev,prod,test}.py
    urls.py  wsgi.py  asgi.py  health.py  logging_config.py
  accounts/  rentals/  reviews/     one apps.py + admin.py each
  tests/                     conftest.py, factories.py, test_*.py
  requirements/{base,dev,prod}.txt
  pyproject.toml             ruff + mypy + pytest + coverage config
frontend/
  src/
    api/          client.ts (axios, JWT, single-flight refresh, pagination)
                  tokens.ts, types.ts
                  schema.yaml + schema.d.ts   GENERATED — never hand-edit
    app/          App.tsx, router.tsx, guards.tsx, ErrorBoundary.tsx
                  layout/   RootLayout, Header, Footer, SkipLink
                  routes/   one file per route, lazily imported
    stores/       auth.ts
    theme/        TenantThemeProvider.tsx, tokens.ts   (ADR-005)
    components/ui/  four vendored shadcn primitives — re-generate, do not edit
    lib/          utils.ts, errors.ts
    test/         setup.ts, utils.tsx, msw/
docs/
  AUDIT.md  DOMAIN_MODEL.md  ENGINEERING.md  OPERATIONS.md  adr/
```

`DJANGO_SETTINGS_MODULE` defaults to `config.settings.dev` in `manage.py`,
`wsgi.py` and `asgi.py`. Tests pin `config.settings.test` via `pyproject.toml`.
`config.settings.prod` **raises at import** if `SECRET_KEY`, `ALLOWED_HOSTS` or
`CORS_ALLOWED_ORIGINS` are missing — that is deliberate, do not add a fallback.

---

## Running things

```bash
docker compose up --build          # db + redis + backend + frontend
docker compose exec backend python manage.py migrate
docker compose exec backend python manage.py createsuperuser
```

Probes: `/health/live/` (no dependencies) and `/health/ready/` (checks
PostgreSQL and Redis; 503 when either is down).
API docs: `/api/docs/`, schema at `/api/schema/`.

### Backend, without Docker

```bash
cd backend
python -m venv ../.venv && ../.venv/bin/pip install -r requirements/dev.txt
export DATABASE_URL=postgres://postgres:postgres@localhost:5432/campus_rental
python manage.py migrate
python manage.py runserver
```

### Frontend

```bash
cd frontend && npm ci && npm run dev     # :8080
```

### API types are generated

`src/api/schema.yaml` comes from the backend; `src/api/schema.d.ts` comes from
it. Both are committed, and CI fails if either drifts. After any change to a
serializer, view or URL:

```bash
cd backend  && python manage.py spectacular --file ../frontend/src/api/schema.yaml
cd frontend && npm run generate:types
```

Nothing under `src/api/` describes a response shape by hand. The previous
client's hand-written types were wrong in six places, one of which silently
disabled navigation.

---

## Tests

Every PR needs tests. Not a guideline — a merge requirement.

```bash
cd backend
pytest                      # needs PostgreSQL; coverage floor is 70%
pytest -m smoke             # just the boot/migration/schema checks
pytest tests/test_api_contract.py -k review

cd frontend
npm run test                # watch
npm run test:run            # once, what CI runs
npm run test:coverage
```

**Backend fixtures** (`backend/tests/conftest.py`): `api_client` (anonymous),
`authenticate(user)` → a fresh authenticated client, `tenant`, `landlord`,
`platform_admin`, `staff_user`, `tenant_client`, `landlord_client`,
`staff_client`, `rental`, `review`.

`authenticate` returns a **new** client each call, deliberately: sharing one
would silently authenticate the "anonymous" client in any test using both, and
hide the authorization bugs the test exists to catch.

**Factories** are in `backend/tests/factories.py`. Use them; do not hand-build
model instances.

**Frontend**: `renderWithProviders` from `src/test/utils.tsx` wraps a component
in the same providers `App.tsx` uses. Pass `route` and `path` for pages reading
`useParams`, and `withTenant: false` when a test drives the theme provider
itself.

API calls are intercepted by **MSW** (`src/test/msw/`), configured with
`onUnhandledRequest: "error"` — an un-stubbed request fails the test rather
than escaping to the network.

A guard test must render a real route table, not a catch-all. `<Route path="*">`
makes a guard's redirect land back on the guard, which loops forever.

The tests that matter most are the ones covering behaviour that is painful to
debug from a bug report: the single-flight refresh queue, both guards, token
derivation, theme application, and a vitest-axe assertion on the shell.

### A note on the contract tests

`backend/tests/test_api_contract.py` pins the API **as it is today**, including
several genuinely broken behaviours (a 500 on the rental detail endpoint,
unreachable review reporting, a client-settable `user_type`). Those tests assert
the broken behaviour and say so in their docstrings.

**Inverting those assertions is part of the schema rewrite's definition of
done.** If one starts failing unexpectedly, read the docstring before assuming
the test is wrong.

---

## Checks that must pass

```bash
cd backend
ruff check . && ruff format --check .
mypy .
python manage.py makemigrations --check --dry-run
pytest

cd ../frontend
npm run lint && npm run typecheck && npm run test:run && npm run build
```

CI (`.github/workflows/ci.yml`) runs all of this on every push and PR against a
Postgres and Redis service container. `pre-commit install` runs the backend
subset locally.

---

## Conventions

### Python

- Line length 100. `ruff format` decides layout; do not argue with it.
- Double quotes, `from __future__ import annotations` in new modules.
- Explicit `on_delete` on every FK. Default to `PROTECT`; `CASCADE` only where
  the child is meaningless without its parent.
- **Invariants that matter go in database constraints**, not only serializer
  validation. A serializer is bypassed by the admin, the shell, a management
  command and any future endpoint. This is not stylistic — see ADR-004.
- Money is `Decimal`, never float. Distances are kilometres, sizes are m².
- Never `except Exception: pass`. It hid a broken logout for months
  (`docs/AUDIT.md` §4.5).
- Query-count assertions on list endpoints. The joins in ADR-002 make N+1 easy.

### TypeScript

- No `any`. Catch `unknown` and use `getErrorMessage` from `@/lib/errors`.
- **API types are generated.** Never hand-write a request or response shape;
  import from `@/api/types`.
- **List responses are envelopes**, never arrays. Use `getPage` and read
  `.results`.
- `src/components/ui/*` is vendored shadcn. Re-add from the shadcn CLI rather
  than hand-editing, and only what a screen actually uses.
- Accessibility is not a later pass: semantic landmarks, a visible focus ring on
  every interactive element, labelled form controls, and one `<h1>` per page.

### Theming

`src/theme/tokens.ts` owns the ADR-005 token derivation. It emits exactly nine
custom properties and deliberately never touches backgrounds, borders or
`--destructive`. If a colour in the browser does not match `index.css`, it is
because `TenantThemeProvider` set an inline value on `:root`.

### Migrations

Reset, do not chain, until the schema rewrite lands — there is no production
data. After that, normal rules apply.

---

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/).

```
<type>(<scope>): <subject>

[body]
[BREAKING CHANGE: ...]
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`,
`ci`, `chore`, `revert`. Scopes: `accounts`, `rentals`, `reviews`, `config`,
`api`, `frontend`, `docker`, `ci`, `deps`.

Subject in the imperative, no trailing full stop, ≤ 72 characters.

```
feat(rentals): add PropertyCampusDistance join model
fix(accounts): install token_blacklist so logout invalidates refresh tokens
test(reviews): pin the unreachable report action
```

### Pull requests

- Every PR needs tests. A PR that only removes tests needs an explicit reason.
- CI green — all of it, including the coverage floor.
- An architectural change needs an ADR, or an amendment to one.
- Reference the ADR when implementing a decision from it.

---

## Architecture decisions

Full text in `docs/adr/`. All seven were amended on 2026-08-24 to record the
resolutions from design review. Summaries, so the reasoning travels with the
code:

**ADR-001 — Multi-tenancy: shared database, shared schema.** `University` is
the tenant, resolved from the subdomain. **Public listing pages are canonical
at `www/listings/<slug>`;** branded subdomains emit `rel=canonical` at the
neutral host. Writes and authenticated reads stay strictly subdomain-scoped.
The `X-University` dev fallback **must raise `ImproperlyConfigured` at import
time in prod** — absence is not enough. Session cookies are scoped to the exact
subdomain, never the parent domain.

**ADR-002 — Properties link to universities through a join model.**
`PropertyCampusDistance` carries an always-present `straight_line_km`
(haversine on save) and nullable `walking_distance_km` / `walking_minutes`
populated only by an async routing job behind a swappable `RouteProvider`
(OpenRouteService first). **Walking time is never derived from straight-line
distance**, and UI showing the straight-line figure must label it. The draft's
bounding-box longitude term divided by zero at the equator; the correct factor
is `cos(radians(lat))`.

**ADR-003 — Authorization is object-level.** `User` holds identity and auth
only; `LandlordProfile`, `CaretakerAssignment`, `StudentProfile` and
`UniversityStaffProfile` carry capability. A caretaker may manage units,
vacancy, photos, availability, tenancy claims and inquiries — **not** delete a
property, transfer ownership, grant assignments, touch payout fields, or post a
`ReviewResponse`. Student verification is **per-university policy, off by
default**, earning a badge rather than gating access. `signup_policy` replaces
the old boolean and **cannot be set to `verification_required` unless the
university already has at least one verified student** — so a school that
enabled verification but has not issued addresses cannot lock out its own
intake. Policy applies at signup only; existing users are prompted, never
blocked. **ID documents are
regulated personal data under Kenya's Data Protection Act 2019:** private
bucket only, signed URLs, scheduled deletion after
`id_review_retention_days`, every read logged, byte-level content-type
validation on upload.

**ADR-004 — Review integrity via a `Tenancy` record.** **An accepted
`Application` creates a confirmed `Tenancy` directly** — the platform witnessed
it, so no claim and no dispute surface. `TenancyClaim` exists only for stays the
platform did not witness; that is the primary control on dispute volume and must
not be "simplified" away. For claims: the tenant initiates, the landlord and
caretakers have 7 days, and silence auto-confirms. **Disputes are typed** —
`dates_incorrect` resolves between the parties, `duplicate` auto-resolves, and
only `never_tenanted` plus unresolved counters reach an admin. **The timeout is
symmetric:** an escalated dispute we have not resolved in 14 days auto-resolves
for the tenant, and the review carries a neutral `disputed_by_landlord`
annotation. `Tenancy` records `confirmation_source` across
`application | landlord | caretaker | auto | admin | dispute_timeout`. See
`docs/OPERATIONS.md` for the SLA and alerting.
Overlapping confirmed tenancies are blocked by an `ExclusionConstraint`
(`btree_gist`); one open claim per user per unit; claims are rate-limited. The
30-day minimum stay cannot be a `CheckConstraint` for an ongoing tenancy, so it
lives in `settings.REVIEW_MINIMUM_STAY_DAYS` behind the single service function
`assert_tenancy_is_reviewable()`. Everything else stays a database constraint.

**ADR-005 — Per-university theming via database-stored design tokens.**
`University` holds primary/secondary/accent as HSL triples. A public
unauthenticated endpoint returns the tenant config; the React app applies it to
`:root`. **Three tokens overridden, four derived** (`--*-foreground` by WCAG
contrast, `--ring`), **two adjusted** (`--primary-light/-dark`). The other 17 —
backgrounds, borders, `--destructive` — are deliberately untouched.
`--gradient-hero` and `--gradient-card` now reference `var(--primary)`.

**ADR-006 — Geo search stays simple.** lat/lng floats plus precomputed campus
distances; no PostGIS. Triggers for revisiting, and the full migration path,
are in the ADR.

**ADR-007 — Media on S3-compatible object storage, django-rq for jobs.**
Cloudflare R2 in production, MinIO locally, never local disk. **Cloudflare
Images was rejected**: a queue is now required by three jobs unrelated to
images, so removing the image subsystem no longer removes the queue.
**Two buckets** — public media and a private documents bucket with its own
storage backend class. Never merge them.

## Schema rewrite progress

| Phase | State |
|---|---|
| 1 — tenancy foundation | **Done.** btree_gist, `University`, `Campus`, the resolution middleware, the scoped manager, the public tenant config endpoint |
| 2 — identity | **Done.** `User` on `AbstractBaseUser`, the three profile models, relationship-based permissions, capabilities on `/auth/me/` |
| 3 — properties | Next. `Property`, `Unit`, `PropertyCampusDistance`, then storage, then `UnitPhoto` |
| 4 — storage and queue | django-rq, MinIO, the two buckets |
| 5 — the trust property | `Application`, `TenancyClaim`, `Tenancy`, `Review` |
| 6 — verification | The two student paths and document retention |
| 7 — cleanup | Remove the draft apps, rebuild the frontend pages |

`CaretakerAssignment` is defined by its foreign key to `Property`, so it lands
in phase 3 rather than with the other role models.

### Architecture tests

`backend/tests/test_architecture.py` enforces the structural rules that would
otherwise erode. Each fails on **addition**, so a new route or model forces the
decision:

- every route carries an explicit host class in `config/hosts.py`
- no `PUBLIC_CANONICAL` route serves an unsafe method
- absolute URLs come only from `config.hosts.build_absolute_url`
- every local model is tenant-scoped or exempt **with a reason**

When one of these fails on your branch, the fix is to make the decision it is
asking for, not to widen the exemption list.

## Where things stand

The backend plumbing (Docker, DRF, JWT, the settings split, CI) is sound and is
being kept. The **frontend has been rebuilt** on a generated API contract; only
the shell exists, deliberately, because the API contract changes with the
schema rewrite.

The **domain model is still the original draft** — a US apartment-listing schema
with "campus" bolted on — and is being replaced per `docs/DOMAIN_MODEL.md`.

**Read `docs/AUDIT.md` before touching the draft apps.** Two of its findings
are still live and look like your bug: the rental detail endpoint raises on an
unresolved `F()` expression for every non-owner, and review reporting is
unreachable by anybody. Both are pinned by tests in
`tests/test_api_contract.py` and are fixed by their phase of the rewrite.

The `user_type` escalation path is closed as of phase 2.
