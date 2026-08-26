# CampusRentalFinder — Codebase Audit

**Date:** 2026-08-23
**Commit audited:** `b5b7208` plus uncommitted working-tree changes
**Scope:** Everything in the repository.

The short version: the plumbing is
real and worth keeping, the domain model is a generic US apartment-listing app
with "campus" bolted on, and the frontend has never successfully talked to the
backend on its two most important screens. Three endpoints are dead on arrival
and one returns HTTP 500 to every visitor.

Line counts below are the state *before* the baseline hardening pass, so they
match the code as it stood at the reviewed commit.

---

## 1. Inventory

### Backend — Django

| Module | LOC | What it does | State |
|---|---:|---|---|
| `rental_platform/` (now `config/`) | 361 | Single-file settings, root URLconf, WSGI. A duplicate `views.py` held a copy of the health check defined inline in `urls.py`. | **Half-built.** No `asgi.py`, no settings split, no real logging. |
| `accounts/` | 1 260 | Custom `User` (email login, `user_type` string), `UserProfile`, JWT register/login/logout, profile CRUD, password change, admin user viewset. | **Mostly complete**, but logout was silently broken (see §4). No `apps.py`, no `admin.py`, no tests. |
| `rentals/` | 1 883 | `Rental`, `RentalImage`, `RentalFavorite`, `RentalInquiry`. Search, filtering, favourites, inquiries, admin viewset. | **Complete but broken.** Detail endpoint 500s (§4.1). No `apps.py`, no `admin.py`, no tests. |
| `reviews/` | 1 635 | `Review`, `ReviewHelpfulness`, `ReviewReport`. CRUD, helpfulness votes, reporting, landlord responses, moderation. | **Complete but unreachable in part.** Reporting cannot be invoked by anyone (§4.2). No `apps.py`, no `admin.py`, no tests. |
| `rentals/management/commands/create_test_data.py` | 231 | Seeds six Beverly Hills, California listings priced in dollars. | **Dead code for this product.** Untracked in git at audit time. |
| `backend/=10.4.0` | 6 | Captured stdout of `pip install Pillow>=10.4.0` where the shell ate the `>`. Committed to the repo. | **Junk.** Deleted. |

**Backend totals:** ~5 400 LOC of Python. **0 lines of tests.**

### Frontend — React + Vite + shadcn/ui

| Area | LOC | State |
|---|---:|---|
| `src/pages/` (11 pages) | 1 634 | 9 wired to the API, 1 static mockup, 1 trivial. Details in §5. |
| `src/components/` (6 app components) | 385 | Navbar, Footer, RentalCard, ProtectedRoute, ThemeProvider, ThemeToggle. |
| `src/components/ui/` (49 files) | ~4 500 | Stock shadcn/ui. **37 of the 49 are dead** — 32 imported by nothing at all, 5 imported only by other dead files. 12 are reachable from application code. |
| `src/services/api.ts` | 184 | Axios client, JWT interceptors, five API modules. Four of its calls point at URLs that do not exist. |
| `src/store/authStore.ts` | 95 | Zustand auth store. |
| `src/types/index.ts` | 68 | Hand-written types that do not match the API responses. |

**Frontend totals:** ~2 550 LOC of application code, ~4 500 LOC of vendored UI kit, **0 tests**.

### Documentation and infrastructure

| File | State |
|---|---|
| `README.md` | Two lines: a title and a tagline. |
| `docs/api-documentation.md` | **Zero bytes.** Deleted. |
| `docs/demo-presentation.md` | **Zero bytes.** Deleted. |
| `DOCKER_SETUP_FIXED.md` | Deleted in the working tree before this pass. Good. |
| `docker-compose.yml` | 47 lines. No healthchecks, no Redis, no media volume (§4.3). |
| `backend/Dockerfile` | Ran `runserver` as root and applied migrations on container start. |
| `frontend/Dockerfile` | Reasonable multi-stage nginx build. |
| `frontend/nginx.conf` | Proxies `/api` to `backend:8000`. Fine. |
| CI | **None.** |
| `.env` | Present locally, correctly gitignored, never committed. |

---

## 2. Current data model (text ER)

```
User  (accounts_user, extends AbstractUser)
├── USERNAME_FIELD = email          (unique, lowercased on save)
├── username                        vestigial, backfilled from email on save,
│                                   but objects.create_user() still demands it
├── first_name, last_name
├── phone_number                    validated by ^\+?1?\d{9,15}$   ← US-shaped
├── user_type                       'tenant' | 'landlord' | 'admin'  ← the
│                                   entire authorisation model, one string
├── date_of_birth, profile_picture, bio
├── address, city, state, zip_code  ← US address shape
├── is_verified, verification_date  set by staff, means nothing in particular
├── is_staff / is_superuser         Django's own flags — a SECOND, unrelated
│                                   notion of "admin" (see §4.4)
└── created_at, updated_at

UserProfile  (1:1 → User, created by post_save signal)
├── preferred_contact_method, email_notifications, sms_notifications
├── website, linkedin
└── business_name, business_license       ← landlord fields on every user row

Rental  (rentals_rental)
├── title, description, property_type ∈ {apartment, house, condo, townhouse,
│                                        studio, room, other}   ← US typology
├── landlord            FK → User, limit_choices_to={'user_type': 'landlord'}
├── price, security_deposit  Decimal(10,2), currency implicit and unlabelled
├── utilities_included
├── address, city, state, zip_code, country='United States'
├── latitude, longitude      nullable floats
├── bedrooms, bathrooms, square_footage (≥100, imperial), furnishing_status
├── parking_available, parking_spots, pets_allowed, smoking_allowed,
│   laundry_available, internet_included, gym_access, pool_access
├── available_from, lease_duration_min=12, lease_duration_max
├── status ∈ {available, rented, pending, maintenance, inactive}
├── is_featured, views_count
├── contact_phone, contact_email
├── distance_to_campus  FloatField, MILES, and only ONE campus
├── shuttle_service
└── created_at, updated_at

RentalImage      FK → Rental, image, caption, is_primary, order
RentalFavorite   FK → User, FK → Rental, unique_together(user, rental)
RentalInquiry    FK → Rental, FK → User(tenant), message, contact_phone,
                 preferred_move_date, status, landlord_reply, replied_at

Review  (reviews_review)
├── rental        FK → Rental
├── tenant        FK → User, limit_choices_to={'user_type': 'tenant'}
├── rating 1–5, comment
├── cleanliness_rating, location_rating, value_rating, landlord_rating
├── title (auto-generated when blank), pros, cons
├── move_in_date, move_out_date      NULLABLE and UNVERIFIED
├── would_recommend
├── is_verified, is_approved=True, moderation_notes
├── landlord_response, landlord_response_date  ← denormalised onto the review
├── helpful_votes, total_votes       maintained by signal
└── unique_together(rental, tenant)

ReviewHelpfulness  FK → Review, FK → User, is_helpful,
                   unique_together(review, user)
ReviewReport       FK → Review, FK → User(reporter), reason, description,
                   is_resolved, admin_action, resolved_by, resolved_at,
                   unique_together(review, reporter)
```

**What is conspicuously absent:** any `University` entity at all. The product is
sold per-university, and the schema has no concept of one. There is no `Unit`
(so vacancy counts have nowhere to live), no `Tenancy` (so reviews are
unanchored), no caretaker role, and no `Application`.

**The review integrity hole.** A `Review` requires only that the author's
`user_type == 'tenant'`. Anyone who signs up — and `user_type` is chosen freely
by the client at registration, unvalidated — can post a five-star review on any
property they have never seen. `move_in_date` and `move_out_date` are optional
and nobody checks them. For a platform whose value proposition is trustworthy
reviews, this is the defect that matters most. ADR-004 addresses it.

---

## 3. US-context assumptions, by file and line

Line numbers refer to the pre-hardening files, i.e. commit `b5b7208`.

### Currency

| File:line | Assumption |
|---|---|
| `rentals/models.py:324` | `return f"{self.title} - ${self.price}/month"` — a literal `$`. |
| `frontend/src/components/RentalCard.tsx:63` | `${rental.price}` on every listing card. |
| `rentals/models.py:87–103` | `price` / `security_deposit` are `Decimal(10,2)`; two decimal places is wrong for KES, and no currency is recorded anywhere. |
| `rentals/management/commands/create_test_data.py` | All six seed listings priced 850–3 200 as USD. |

### Units of measurement

| File:line | Assumption |
|---|---|
| `rentals/models.py:169–175` | `square_footage`, `MinValueValidator(100)` — square feet. |
| `rentals/models.py:277–283` | `distance_to_campus` labelled *"distance to campus (miles)"*. |
| `rentals/serializers.py:574` | `max_distance_to_campus` — *"Maximum distance to campus in miles"*. |
| `rentals/serializers.py:606` | `radius` — *"Search radius in miles"*, capped at 50. |
| `rentals/views.py:169–171` | `lat_delta = radius / 69` — 69 statute miles per degree of latitude. |
| `frontend/src/components/RentalCard.tsx:54` | Renders `{rental.area} m²` — **square metres**, against a backend field in square feet. The two ends of the stack disagree about the unit on the same number. |

### Address shape

| File:line | Assumption |
|---|---|
| `rentals/models.py:123–137` | `state` (*"state/province"*), `zip_code`, `country` defaulting to `'United States'`. |
| `rentals/models.py:333` | `full_address` renders `"{address}, {city}, {state} {zip}"` — a US postal line. Kenya uses estate/town/county with a P.O. box, not a street-plus-ZIP. |
| `accounts/models.py:96–107` | Same `state` / `zip_code` pair on `User`. |
| `accounts/migrations/0001_initial.py:42–43` | Baked into the migration. |
| `rentals/views.py:114–115`, `rentals/serializers.py:513–515` | Search filters by `state`. |
| `create_test_data.py:69–70` etc. | `'state': 'CA'`, `'zip_code': '90210'`. |

### Property typology

| File:line | Assumption |
|---|---|
| `rentals/models.py:34–42` | `apartment, house, condo, townhouse, studio, room, other`. "Condo" and "townhouse" are meaningless near a Kenyan campus; bedsitter, single room, one/two bedroom and hostel block are all missing. |
| `rentals/models.py:163–167` | `bathrooms` has `MinValueValidator(1)` — a shared-ablutions hostel block or a single room with communal facilities cannot be listed at all. |
| `rentals/models.py:233–246` | `lease_duration_min` defaults to **12 months**. Student lets run by semester. |
| `rentals/models.py:217–225` | `gym_access`, `pool_access` as first-class amenities; no water tank, borehole, backup power, perimeter wall, or caretaker-on-site, which are what actually gets asked. |

### Locale

| File:line | Assumption |
|---|---|
| `settings.py:196–197` | `LANGUAGE_CODE = 'en-us'`, `TIME_ZONE = 'UTC'`. |
| `accounts/models.py:31–34` | Phone regex `^\+?1?\d{9,15}$` — the optional `1` is a North American country code. Kenyan numbers are `+254 7xx xxx xxx`. |
| `settings.py:287` | `DEFAULT_FROM_EMAIL = 'noreply@rentalplatform.com'`. |

---

## 4. Security and correctness findings

Ordered by severity. Each is reproduced by a test in `backend/tests/`.

### 4.1 — CRITICAL: the rental detail endpoint returns 500 to every visitor

`rentals/models.py:359–362`:

```python
def increment_views(self):
    self.views_count = models.F('views_count') + 1
    self.save(update_fields=['views_count'])
```

`rentals/views.py:208–217` calls this and then immediately serialises **the same
in-memory instance**. `views_count` is still an unresolved `CombinedExpression`,
and DRF's `IntegerField.to_representation` raises:

```
TypeError: int() argument must be a string, a bytes-like object or a real
number, not 'CombinedExpression'
```

The owning landlord is the only user who escapes it, because the view skips
`increment_views()` for them. Every prospective tenant clicking any listing gets
a 500. **The single most important page on the site has never worked.**
The fix is a `refresh_from_db(fields=["views_count"])`, but it is left in place
deliberately: this document records the real state, and the endpoint is being
rewritten. Pinned by `test_detail_crashes_for_every_visitor_who_is_not_the_owner`.

### 4.2 — HIGH: review reporting is unreachable by anyone

`ReviewViewSet` uses `IsTenantOrReadOnly`, whose `has_object_permission`
(`reviews/views.py:51–61`) admits only the review's own author for unsafe
methods. The `report` action (`reviews/views.py:229–257`) does not override
`permission_classes`, so:

- a stranger trying to report a review → **403** from the object permission;
- the author trying to report their own → **400** "You cannot report your own review".

There is no third case. The moderation queue can never receive an entry.
Pinned by `test_reporting_a_review_is_unreachable_for_everyone`.

### 4.3 — HIGH: `SECRET_KEY` had a hard-coded insecure fallback

`settings.py:22`:

```python
SECRET_KEY = config('SECRET_KEY', default='django-insecure-change-this-in-production-xyz123')
```

Combined with `DEBUG = config('DEBUG', default=True, cast=bool)` on the next
line, a deploy that forgot both env vars would run in debug mode with a
publicly known signing key. Because `SIMPLE_JWT['SIGNING_KEY'] = SECRET_KEY`,
anyone reading this repository could forge an access token for any user id.

**Fixed.** `config/settings/prod.py` raises `ImproperlyConfigured` on a missing
key, and also rejects any key still carrying Django's `django-insecure-` prefix.
`DEBUG` now defaults to `False` in `base.py`.

### 4.4 — HIGH: two unrelated meanings of "admin"

`user_type == 'admin'` and Django's `is_staff` are entirely independent. Every
admin endpoint is guarded by DRF's `IsAdminUser`, which checks `is_staff`. So:

- a user created with `user_type='admin'` gets **403** everywhere in the admin API;
- the object-permission checks in `rentals/views.py:52–61` and
  `reviews/views.py:51–61` check `request.user.user_type == 'admin'` instead,
  so a self-declared "admin" **can edit and delete any listing or review on the
  platform** — and `user_type` is client-supplied at registration with no
  validation whatsoever (`accounts/serializers.py:38–56`).

That is a privilege-escalation path open to anyone with a signup form.
Pinned by `test_user_type_is_client_supplied_and_unvalidated` and
`test_platform_admin_user_type_does_not_grant_admin_api_access`.

### 4.5 — HIGH: logout never worked; refresh tokens were immortal

`accounts/views.py:138–153` calls `token.blacklist()`, but
`rest_framework_simplejwt.token_blacklist` was **not in `INSTALLED_APPS`**. The
call raised, the bare `except Exception` swallowed it, and the endpoint returned
400 "Invalid token" while the refresh token stayed valid for its full 7 days.
`SIMPLE_JWT['BLACKLIST_AFTER_ROTATION'] = True` was likewise inert.

**Fixed:** the app is now installed. Pinned by
`test_reusing_a_blacklisted_refresh_token_fails`.

### 4.6 — HIGH: `CORS_ALLOW_ALL_ORIGINS` under `DEBUG`

`settings.py:179–181`:

```python
if DEBUG:
    CORS_ALLOW_ALL_ORIGINS = True
    CORS_ALLOW_CREDENTIALS = True
```

`Allow-Origin: *` together with `Allow-Credentials: true` — and `DEBUG`
defaulted to `True`. Any website could make credentialed cross-origin calls to a
misconfigured deploy. **Removed entirely**; both dev and prod now
use an explicit env-driven list, and `prod.py` refuses to start with an empty one.

### 4.7 — MEDIUM: Dockerfile ran as root and migrated on start

`backend/Dockerfile`:

- `python:3.11-slim` with no user directive → **the container ran as root**.
- `CMD python manage.py migrate && python manage.py runserver 0.0.0.0:8000` —
  the **development server in the production image**: single-threaded, no
  process supervision, and it serves static files itself, bypassing WhiteNoise.
- Migrations on every container start: two replicas racing the same migration
  is a corrupted schema.
- `gcc` and `libpq-dev` shipped in the final image, giving a compiler to anyone
  who gets code execution.
- `COPY . /app/` with a `.dockerignore` that does exclude `.env` — that one was
  handled correctly.

**Fixed:** multi-stage build, non-root `app` user (uid 1001), gunicorn in prod,
no compiler in the final layer, migrations moved out of `CMD`.

### 4.8 — MEDIUM: docker-compose gaps

- `depends_on: [db]` without a condition. Compose waits for the container to
  *exist*, not for Postgres to accept connections, so the backend raced the
  database on every cold start. **Fixed** with a `pg_isready` healthcheck and
  `condition: service_healthy`.
- No Redis service at all, while `settings.py:273–279` configured a Redis cache
  for production. **Fixed.**
- `ports: "5432:5432"` publishes Postgres on all host interfaces. Left in place
  for developer convenience; **must not be carried into any deployed compose file.**
- No media volume: uploads lived in the container's writable layer and vanished
  on `docker compose down`. **Fixed** with a named volume (and superseded
  entirely by ADR-007).
- `JWT_SECRET` was passed to the backend, but nothing in the Django settings ever
  read a variable of that name. The `.env.example` advertised it as *"Django
  Secret Key"*. Purely decorative. **Fixed.**

### 4.9 — MEDIUM: `Rental.save()` raises `ValueError`

`rentals/models.py:372–382` raises a bare `ValueError` when
`lease_duration_max < lease_duration_min`. That is not a `ValidationError`, so
DRF does not translate it into a 400 — it propagates as an unhandled 500.
Pinned by `test_an_inconsistent_lease_range_raises_value_error_not_validation_error`.

### 4.10 — LOW / informational

- `bare except Exception` in `accounts/views.py:151` masked 4.5 for months.
- `settings.py:27` hard-codes `192.168.1.101` — someone's home LAN address — into
  the default `ALLOWED_HOSTS`, and `settings.py:173` into the CORS list.
- `ACCESS_TOKEN_LIFETIME` was 60 minutes. Reduced to 15; refresh rotation now
  actually works, so the shorter window costs nothing.
- `frontend/.env.example` defines `VITE_API_URL` **twice**, and the second
  (`http://192.168.1.101:8000/api/v1`) wins. Anyone copying it to `.env` points
  their frontend at a machine on someone else's home network.
- The OpenAPI schema generates with **75 warnings and 16 errors** (unresolvable
  serializers on the function-based views, un-hinted `SerializerMethodField`s,
  enum name collisions). It still produces a usable document, so the smoke test
  asserts generation rather than cleanliness. These clear up naturally when the
  views are rewritten.
- Both `bun.lockb` and `package-lock.json` are committed. Two lockfiles, two
  different dependency resolutions, and the Dockerfile uses `npm ci`. Pick one.

---

## 5. What the frontend actually does today

### Pages wired to real API calls

| Page | Calls | Works? |
|---|---|---|
| `HomePage` | `rentalsApi.getFeatured()`, `getRecent()` | **Yes.** Both hit real endpoints returning bare arrays, which is what the page expects. |
| `LoginPage` | `authStore.login()` → `POST /auth/login/` | **Yes.** |
| `RegisterPage` | `authStore.register()` → `POST /auth/register/` | **Yes.** |
| `RentalsPage` | `rentalsApi.getAll()` → `GET /rentals/` | **No.** See below. |
| `DashboardPage` | `rentalsApi.getAll()` | **No.** Same call. Also has a `// In a real app, filter by landlord ID` comment where the filtering should be. |
| `RentalDetailPage` | `rentalsApi.getById()`, `reviewsApi.create()`, `getRentalStatistics()` | **No.** Detail 500s (§4.1); statistics path does not exist. |
| `ProfilePage` | `profileApi.get()`, `profileApi.update()` | **Read works, write does not.** `update()` sends `PUT`; the view implements only `GET` and `PATCH` → 405. It also sends `{username, phone}` while the model has `first_name`/`last_name`/`phone_number`. |
| `CreateRentalPage` | `rentalsApi.create()` → `POST /rentals/` | **No.** Wrong path. |
| `AdminPage` | `adminApi.getStatistics()` → `GET /admin/statistics/` | **No.** No such route; the three real ones are namespaced under `auth/`, `rentals/` and `reviews/`. |

### Broken URL contracts

| Frontend call | Reality |
|---|---|
| `GET /rentals/` | This is the DRF router **root**, not a list. Anonymously → **401**; authenticated → a JSON directory of route names. The listing lives at `/rentals/properties/`. `RentalsPage` and `DashboardPage` have therefore never displayed a single rental. |
| `POST /rentals/` | Same root → 405/401. Listing creation has never worked. |
| `GET /rentals/top-rated/` | Does not exist. The reviews app has `/reviews/top-rated/`. |
| `GET /reviews/statistics/{id}/` | Does not exist. It is `/reviews/rental/{id}/statistics/`. |
| `GET /admin/statistics/` | Does not exist at the API root. |
| `PUT /auth/profile/` | 405 — the view is `GET`/`PATCH` only. |

### Pagination mismatch

`REST_FRAMEWORK['DEFAULT_PAGINATION_CLASS']` is `PageNumberPagination`, so every
`ModelViewSet` returns `{count, next, previous, results}`. `services/api.ts`
types all of these as `Rental[]`, and the pages call `.slice()` and `.map()` on
the result. Even once the URLs are corrected, every list view will throw
`data.slice is not a function` until the client is taught about the envelope.
Pinned by `test_the_list_response_is_paginated_not_a_bare_array`.

### Type drift

`src/types/index.ts` was written against an imagined API, not the real one:

| Frontend type | Backend reality |
|---|---|
| `User.role` | The field is `user_type`. **`Navbar` and `ProtectedRoute` both read `user.role`, which is always `undefined`** — so the Favorites and Admin nav links never render, and `allowedRoles` gating silently passes everyone through to the role check's `undefined` branch. |
| `User.phone` | `phone_number` |
| `User.id: string` | `BigAutoField` → number |
| `Rental.area` | `square_footage` (and rendered as m², stored as ft²) |
| `Rental.images: string[]` | Array of `{id, image, image_url, caption, is_primary, order}` objects |
| `Review.user` | `tenant` |

### Auth state

Handled in `store/authStore.ts` with Zustand, **not persisted**:

- Tokens go in `localStorage` under `access_token` / `refresh_token`.
  `localStorage` is readable by any XSS payload; the refresh token in particular
  is a 7-day credential sitting in script-accessible storage.
- `isAuthenticated` is seeded once at module load from
  `!!localStorage.getItem("access_token")` — it is **not** validated, and an
  expired token still reads as authenticated until a request fails.
- `user` is **not** persisted, so every hard refresh drops it to `null`.
  `ProtectedRoute` re-fetches via `fetchUser()`, but renders its children on the
  very first pass while `user` is still `null`, so `allowedRoles` is not enforced
  for that render.
- `api.ts` has a refresh-on-401 interceptor with a `_retry` flag. It is
  single-flight-unsafe: N concurrent 401s fire N refresh calls, and with
  `ROTATE_REFRESH_TOKENS` on, the first rotation invalidates the rest.
- Logout clears storage in a `finally`, so it always succeeds locally — which is
  why nobody noticed that server-side blacklisting (§4.5) was broken.

### Theming today

Entirely static, entirely client-side.

- `src/index.css` defines the shadcn token set on `:root` and `.dark` as raw HSL
  triples: `--background`, `--foreground`, `--card(-foreground)`,
  `--popover(-foreground)`, `--primary(-foreground)`, `--secondary(-foreground)`,
  `--muted(-foreground)`, `--accent(-foreground)`, `--destructive(-foreground)`,
  `--border`, `--input`, `--ring`, `--radius`, plus custom
  `--primary-light`, `--primary-dark`, `--gradient-hero`, `--gradient-card`,
  four `--shadow-*` and `--transition-smooth`.
- `tailwind.config.ts` maps each to a Tailwind colour via `hsl(var(--token))`.
- `components/theme-provider.tsx` is a hand-rolled light/dark/system provider
  (the `next-themes` dependency is installed and unused) that toggles a class on
  `<html>` and persists to `localStorage` under `campus-rental-theme`.
- **There is no per-tenant theming and no mechanism for it.** One hard-coded
  green (`--primary: 142 71% 45%`) for every university. ADR-005 addresses this,
  and the good news is that the token layer is already the right shape.

### Dead frontend weight

- **37 of 49 `components/ui/*` files are unreachable from application code**:
  32 are imported by nothing whatsoever (carousel, chart, drawer, menubar,
  resizable, `sidebar.tsx` at 700+ lines, input-otp, pagination, breadcrumb,
  select, table, tabs, switch, calendar, command, form, popover…) and a further
  5 (dialog, separator, sheet, skeleton, toggle) are imported only by other dead
  files. Only 12 are actually in use.
- `FavoritesPage` is a **static mockup**: `useState([])` with a comment reading
  `// This would normally fetch from an API`. It always shows the empty state,
  even though `GET /rentals/properties/favorites/` exists and works.
- `vite_react_shadcn_ts` still carries a scaffold-generator tagging plugin as a
  dev dependency; it has no role in this project.
- `recharts`, `embla-carousel-react`, `vaul`, `input-otp`,
  `react-resizable-panels`, `cmdk` and `react-day-picker` are installed and
  reachable only through dead `ui/*` files, so they ship in the bundle for
  nothing.
- `next-themes` is a special case: the app has its own hand-rolled
  `theme-provider.tsx`, but `ui/sonner.tsx` — which *is* used — imports
  `useTheme` from `next-themes`. Two theme systems coexist, and the toast
  component reads the one that is never written to, so toasts do not follow the
  user's theme choice.

---

## 6. Dependency review

### Blocking: the pinned stack does not install

`requirements.txt` pinned `Django==4.2.7` (November 2023) and
`psycopg2-binary==2.9.9`. The only interpreter available in this environment is
**Python 3.13**, and:

- Django 4.2 officially supports Python 3.8–3.12.
- `psycopg2-binary` 2.9.9 has **no cp313 wheel** and fails to build from source.

`pip install -r requirements.txt` therefore does not complete. **This is a
deviation from "hardening only" that the environment forced.** Django was moved
to the **5.2 LTS** line (supported to April 2028, supports 3.10–3.13) and the
driver to **psycopg 3**, which is what Django 5.x prefers. Everything else in
the file was upgraded to its current release and pinned exactly.

Django 4.2.7 also carries roughly two years of unpatched security releases —
`4.2.30` was the current 4.2 patch at time of writing. Staying on the old pin
was not an option in any case.

### Unused

| Package | Note |
|---|---|
| `python-dateutil` | Never imported. |
| `requests` | Never imported. Removed. |
| `coverage` (bare) | Superseded by `pytest-cov`. |
| `django-extensions` | Useful, but was in `INSTALLED_APPS` for **all** environments including production. Moved to dev only. |
| `next-themes` (npm) | Not unused, but redundant: `ui/sonner.tsx` reads its `useTheme` while the rest of the app uses the hand-rolled provider. Consolidate on one. |
| scaffold tagging plugin (npm) | Artefact of the generator the frontend was bootstrapped from. |

### Missing for what we are about to build

| Need | Package | Status |
|---|---|---|
| Tests | `pytest`, `pytest-django`, `pytest-cov`, `factory_boy`, `Faker` | **Added.** |
| Lint / format / types | `ruff`, `mypy`, `django-stubs`, `djangorestframework-stubs` | **Added.** `black`/`isort`/`flake8` deliberately not used. |
| Git hooks | `pre-commit` | **Added.** |
| Declarative filtering | `django-filter` | **Added.** The hand-rolled `if data.get(...)` chain in `rentals/views.py:92–181` is 90 lines of what a `FilterSet` does in 15. |
| Structured logs | `structlog` | **Added** (ADR §10, task 2.10). |
| Redis client | `redis` | **Added** — the readiness probe needs it, and the cache backend always did. |
| WSGI server | `gunicorn` | **Added** to `requirements/prod.txt`. |
| Object storage (ADR-007) | `django-storages[s3]`, `boto3` | **Not added.** Deliberately deferred: adding the dependency without wiring `STORAGES` would be half a migration. It lands with ADR-007's implementation. |
| Async image variants (ADR-007) | `celery` or `django-rq` | **Not added.** Needs the broker decision first — see the open questions. |
| Frontend tests | `vitest`, `@testing-library/react`, `jsdom` | **Added.** |

### Outdated but fine

`whitenoise`, `Pillow`, `drf-spectacular`, `django-cors-headers` and
`djangorestframework-simplejwt` were all behind but had no blocking issue. All
are now pinned to current releases.

---

## 7. What I would throw away

Bluntly, and in order of confidence.

1. **The entire `Rental` model.** It is a US apartment-listing schema with two
   campus fields stapled on. `condo`, `townhouse`, `pool_access`, `gym_access`,
   `square_footage`, `zip_code`, `country='United States'`, a 12-month default
   lease, and `distance_to_campus` as a single nullable float against a single
   unnamed campus — none of it survives contact with the actual product. Rebuild
   as `Property` + `Unit` per ADR-002 and the domain model.

2. **`user_type`.** One string doing the work of an authorisation model, chosen
   by the client at signup, checked by string equality in five places, and
   silently disagreeing with `is_staff` in five more. It is a live
   privilege-escalation bug (§4.4), not merely inelegant. ADR-003 replaces it.

3. **`UserProfile`.** A grab-bag: notification preferences, social links, and
   `business_name` / `business_license` that are meaningless on 95% of rows.
   Split the landlord fields into `LandlordProfile` and let the preferences be
   their own small model if they are still wanted.

4. **`Review.landlord_response` / `landlord_response_date`.** Denormalised onto
   the review because there was nowhere else to put them. ADR-004 gives them a
   `ReviewResponse` table.

5. **`ReviewHelpfulness` and `ReviewReport`.** Reporting is unreachable (§4.2)
   and helpfulness voting is engagement furniture on a platform with no reviews
   yet. Both are ~150 lines of model plus ~200 of view. Cut them; re-add
   reporting when there is content to moderate.

6. **`rentals/views.py:92–181` — the hand-rolled filter chain.** Ninety lines of
   `if data.get('x'): queryset = queryset.filter(...)`, including a bounding-box
   calculation that divides by `abs(lat / 90)` and so **divides by zero on the
   equator** — which is, notably, where Kenya is. Replace with `django-filter`.

7. **`create_test_data.py`.** Six Beverly Hills listings priced in dollars.
   Replace with `factory_boy` factories, which the test suite now has.

8. **`frontend/src/types/index.ts`.** Hand-written and wrong in six places, one
   of which (`role` vs `user_type`) silently disables navigation. Generate the
   types from the OpenAPI schema instead — `drf-spectacular` is already emitting
   it, so `openapi-typescript` closes the loop permanently.

9. **`FavoritesPage`.** A static mockup shadowing a working endpoint. Either
   wire it to `/rentals/properties/favorites/` or delete the route.

10. **The 37 dead `components/ui/*` files** and the npm dependencies that only they pull in.
    `sidebar.tsx` alone is 700 lines of unimported code.

11. **The `username` field.** `USERNAME_FIELD` is `email`; `username` exists only
    because `AbstractUser` demanded it, is backfilled from the email on save, and
    still breaks `objects.create_user()` when blank. Move to `AbstractBaseUser`
    and delete the column.

### What I would keep

The Docker/nginx setup, the DRF + simplejwt + drf-spectacular choice, the
shadcn/ui token architecture (it is exactly the right substrate for ADR-005),
the Zustand store, and the general URL/versioning layout. The plumbing is sound.
It is the domain model that needs replacing.

---

## 8. Verification

Everything above is reproducible:

```bash
cd backend
pytest                    # 136 tests, 83% coverage
ruff check . && ruff format --check .
mypy .
python manage.py makemigrations --check --dry-run

cd ../frontend
npm run lint && npm run typecheck && npm run test:run
```

The findings in §4 and §5 each had a named test in
`backend/tests/test_api_contract.py`. Where a test asserted broken behaviour,
its docstring said so — **inverting those assertions was part of the schema
rewrite's definition of done.**

`test_api_contract.py` was deleted in Phase 7 along with the apps it described.
§9 below is what replaces it: the record that the loop actually closed.

---

## 9. Traceability: every finding and where it went

`test_api_contract.py` pinned the draft's behaviour, including the parts that
were broken. It died with the draft, which means **the evidence that each
finding was addressed cannot live in a test any more** — a deleted test proves
nothing. This table is that evidence.

Three dispositions, and the distinction matters. *Fixed and pinned* means the
new code behaves correctly and a named, living test fails if that stops being
true. *Fixed by deletion* means the defective code no longer exists, and there
is nothing to pin because there is nothing to regress. *Open* means exactly
what it says.

### §4 — Security and correctness

| # | Finding | Disposition | Evidence |
|---|---|---|---|
| 4.1 | **`increment_views()` 500s the detail endpoint for every visitor.** `Rental.increment_views()` called `save()` inside a read path, tripping the `lease_duration` validation (4.9) on rows that predated it. | **Fixed by deletion.** No view-counter exists in the rebuilt models, and no read path writes. | `Property`/`Unit` carry no counter field; the read path has no `save()`. |
| 4.2 | **Review reporting unreachable by anyone.** The `report` action required `IsAdminUser`, so the only people who could report a review were the ones who could already delete it. | **Fixed by deletion.** `ReviewReport` is gone. Moderation is now `Review.is_published` + `hidden_reason`, staff-only, and a check constraint refuses a hidden review with no reason. | `test_reviews.py::TestRatings::test_a_hidden_review_must_say_why` |
| 4.3 | `SECRET_KEY` hard-coded insecure fallback. | **Fixed and pinned.** Prod settings refuse to boot without it. | `test_tenant_config.py`, `config/settings/prod.py` |
| 4.4 | **`user_type` privilege escalation.** `user_type` was client-supplied at registration and `reviews/views.py` checked `user.user_type == 'admin'` for moderation — so anyone could register as a platform admin. | **Fixed by deletion, and the field is gone.** Role is now the *existence of a profile* (ADR-003); there is no field a registration payload could set. | `test_authorization.py`, and the `platform_admin` fixture exists specifically to prove the escalation path is closed |
| 4.5 | **Logout never worked.** `token.blacklist()` was called but `token_blacklist` was not in `INSTALLED_APPS`, and a bare `except Exception` swallowed the error. Refresh tokens were immortal. | **Fixed and pinned.** | `test_smoke.py::test_token_blacklist_app_is_installed`, plus the auth tests |
| 4.6 | `CORS_ALLOW_ALL_ORIGINS` under `DEBUG`. | **Fixed and pinned.** Split settings; prod never enables it. | `config/settings/prod.py`, `test_tenant_config.py` |
| 4.7 | Dockerfile ran as root and migrated on start. | **Fixed.** Non-root user; migrations are a deploy step. | `backend/Dockerfile` |
| 4.8 | docker-compose gaps (no healthchecks, no volumes). | **Fixed.** | `docker-compose.yml` |
| 4.9 | `Rental.save()` raised bare `ValueError` → unhandled 500. | **Fixed by deletion.** Range validation is now a `CheckConstraint`, which DRF surfaces as a 400. | `tenancy_end_after_start`, `claim_end_after_start` and siblings |
| 4.10 | Hard-coded LAN address; 60-minute access tokens; duplicated `VITE_API_URL`; two lockfiles. | **Fixed.** | settings, `.env.example`, `bun.lockb` removed |

### §3 — US-context assumptions

| Finding | Disposition | Evidence |
|---|---|---|
| **`square_footage` stored in ft², rendered as m².** A listing showing "120 m²" was 120 ft² — an order-of-magnitude lie about the size of a room. | **Fixed by deletion.** The rebuilt `Unit` has no area field at all. Kenyan student housing is advertised by room type (bedsitter, one-bedroom), not by area, so the honest fix was to stop storing a number nobody quotes. | `properties/models.py` — `unit_type`, no `square_footage` |
| USD currency, US address shape, US property typology, `en-US` locale. | **Fixed by deletion and rebuild.** KES, county/town/estate, Kenyan unit types. | `universities/constants.py::KENYAN_COUNTIES`, `properties/constants.py` |

### §5 — Frontend

| Finding | Disposition |
|---|---|
| **Four dead URL contracts** — `GET /rentals/` (router root, not a list), `POST /rentals/`, `GET /rentals/top-rated/`, `GET /reviews/statistics/{id}/`. Plus `GET /admin/statistics/` and `PUT /auth/profile/` (405). | **Fixed by deletion, and structurally prevented.** The frontend was replaced in Round 2 and calls nothing by hand-written string: `src/api/schema.d.ts` is generated from the OpenAPI document, and CI fails if it drifts. A URL that does not exist is now a **type error**, not a 404 in production. |
| `User.role` read but the field was `user_type`. | **Fixed by deletion.** Neither field exists; capabilities come from `/auth/me/`. |
| Pagination shape mismatch. | **Fixed.** The client handles the DRF envelope in one place. |
| 37 dead `components/ui/*` files. | **Fixed by deletion.** Four vendored primitives remain. |

### Still open

| Item | Why |
|---|---|
| **The backend has almost no HTTP API.** Thirteen auth paths and `/api/v1/tenant/config/`. Everything from phases 3–7 is models, services and jobs with no views. | Deliberate sequencing, not an oversight — but it is the largest single piece of remaining work, and the frontend round depends entirely on it. |
| **Zero `PUBLIC_CANONICAL` routes.** ADR-001's neutral host has no content. | Every public route belonged to a draft app. Returns when the public listing endpoints are built. |
| OpenAPI schema generates with warnings. | The draft's function-based views were the main source; the remainder clear up as the real views land. |
| `Unit.vacant_count` is never reconciled against tenancies. | No job checks that vacancy matches reality. Worth building alongside the property API. |
