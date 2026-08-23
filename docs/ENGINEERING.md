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

**Frontend** — React 18, TypeScript, Vite 6, Tailwind + shadcn/ui, Zustand,
TanStack Query, axios, React Router 6.

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
  src/{pages,components,services,store,types,lib,test}/
  src/components/ui/         vendored shadcn — do not hand-edit
docs/
  AUDIT.md  DOMAIN_MODEL.md  adr/
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
`useParams`.

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
- `src/components/ui/*` is vendored shadcn. Re-generate rather than hand-edit.
- Types should be generated from the OpenAPI schema, not hand-written —
  `src/types/index.ts` is currently hand-written and wrong in six places.

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

Full text in `docs/adr/`. Summaries, so the reasoning travels with the code:

**ADR-001 — Multi-tenancy: shared database, shared schema.** `University` is
the tenant, resolved from the subdomain (`kyu.example.co.ke`), falling back to
an `X-University` header **in dev and test only**. A middleware resolves it into
request context; tenant-scoped querysets go through a manager that filters by
it. Rejected `django-tenants` (migration pain for a small team; a property near
two campuses must appear under both, which schema isolation makes awkward).
*Open:* whether public listing pages should be served from a tenant-neutral
canonical host.

**ADR-002 — Properties link to universities through a join model.**
`PropertyCampusDistance` carries `distance_km` and `walking_minutes`; one
property serves many institutions. *Open:* how those two numbers are populated
— haversine understates real walking distance, and a routing API implies a job
queue.

**ADR-003 — Authorization is object-level.** `User` holds identity and auth
only; `LandlordProfile`, `CaretakerAssignment` (scoped to specific properties,
granted by a landlord) and `StudentProfile` carry capability. DRF permission
classes check relationships, not string equality. This closes a live
privilege-escalation path: `user_type` is currently client-settable at
registration and grants edit rights over every listing. *Open:* the exact
caretaker permission set.

**ADR-004 — Review integrity via a `Tenancy` record.** A `Tenancy` is created
when a landlord or caretaker confirms a move-in. `Review` has a required
`OneToOneField` to it, a 30-day minimum stay, a 14-day edit window, and one
landlord response via `ReviewResponse`. **Enforced by schema constraints, not
only serializers** — this is the platform's core trust property. *Open, and
important:* landlord-controlled confirmation lets a bad landlord suppress
reviews by never confirming. The ADR recommends a claim-with-timeout variant.

**ADR-005 — Per-university theming via database-stored design tokens.**
`University` holds primary/secondary/accent as HSL triples plus logo and
display name. A public unauthenticated endpoint returns the active tenant's
config; React applies it to `:root` before first paint. **Three tokens are
overridden** (`--primary`, `--secondary`, `--accent`), four derived
(`--*-foreground` by contrast, `--ring`), two adjusted
(`--primary-light/-dark`). The other 17 — backgrounds, borders, `--destructive`
— are deliberately left alone. `--gradient-hero` and `--gradient-card` must be
rewritten to reference `var(--primary)`; they currently hard-code the green.

**ADR-006 — Geo search stays simple.** lat/lng floats plus precomputed campus
distances; no PostGIS. Triggers for revisiting: map-viewport search,
arbitrary-origin radius at scale, polygon queries, SQL distance ordering from a
non-campus origin, or ~50k properties. The ADR documents the migration in full.
Note the draft's bounding-box maths divides by zero at the equator.

**ADR-007 — Media on S3-compatible object storage.** `django-storages`;
Cloudflare R2 in production (zero egress), MinIO in local Docker, never local
disk. Image variants generated asynchronously. *Open:* which queue — the ADR
recommends Cloudflare Images if the pricing works, `django-rq` otherwise. This
changes `UnitPhoto`'s shape, so decide before the rewrite.

---

## Where things stand

The plumbing (Docker, DRF, JWT, the shadcn token system) is sound and is being
kept. The domain model is a US apartment-listing schema with "campus" bolted
on, and it is being replaced per `docs/DOMAIN_MODEL.md`.

**Read `docs/AUDIT.md` before touching the existing apps.** Several endpoints
are broken in ways that look like your bug and are not.
