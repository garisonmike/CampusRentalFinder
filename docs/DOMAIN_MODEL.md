# Target Domain Model

**Status:** Partly implemented. Phases 1 to 4 have landed: `University`,
`Campus`, `User`, the three profile models, `CaretakerAssignment`, `Property`,
`Unit`, `PropertyCampusDistance` and `UnitPhoto` exist in code, on object
storage and with the queue behind them. `Application`, `TenancyClaim`,
`Tenancy`, `Review` and the verification models are still proposed.
**Date:** 2026-08-23
**Updated:** 2026-08-25 — signup policy enum, typed disputes, application-sourced
tenancies

This is the target schema described in ADR-001 through ADR-007. It is not what
the code contains today; `docs/AUDIT.md` §2 describes that.

**Context is Kenyan throughout.** Currency is KES, distances are kilometres,
addresses are county/town/estate, and the property types are the ones that
actually exist around a Kenyan campus.

## Conventions

- Every model has `id` (`BigAutoField`), `created_at` and `updated_at` unless
  stated otherwise.
- Money is `DecimalField(max_digits=10, decimal_places=2)` in **KES**. Two
  decimal places are kept for arithmetic exactness; the UI renders whole
  shillings (`KES 12,000`).
- Distances are `DecimalField(max_digits=5, decimal_places=2)` in **kilometres**.
- Phone numbers are E.164 (`+2547XXXXXXXX`), validated by
  `^\+254[17]\d{8}$`.
- `on_delete` is stated for every FK. The default is `PROTECT`; `CASCADE` is
  used only where the child has no meaning without its parent.
- Soft deletion via `is_active`, not row removal, wherever history matters.

---

## Reference data

### `University` — the tenant (ADR-001, ADR-005)

| Field | Type | Notes |
|---|---|---|
| `name` | `CharField(200)` | "Kenyatta University" |
| `display_name` | `CharField(50)` | "KyU" — used in the navbar |
| `slug` | `SlugField(50)` | unique |
| `subdomain` | `CharField(63)` | unique, indexed. `kyu` → `kyu.example.co.ke` |
| `domain` | `CharField(255)` | `ku.ac.ke` |
| `county` | `CharField(50)` | `choices=KENYAN_COUNTIES`, 47 entries |
| `town` | `CharField(100)` | "Nairobi", "Juja", "Eldoret" |
| `logo_url` | `URLField` | public media bucket (ADR-007) |
| `favicon_url` | `URLField` | blank |
| `primary_hsl` | `CharField(32)` | `"142 71% 45%"` — validated (ADR-005) |
| `secondary_hsl` | `CharField(32)` | |
| `accent_hsl` | `CharField(32)` | |
| `verification_methods_enabled` | `ArrayField(CharField(24))` | subset of `{email_domain, student_id_upload}`; **may be empty** |
| `student_email_domains` | `ArrayField(CharField(255))` | `["s.kyu.ac.ke"]` |
| `signup_policy` | `CharField(24)` | `open \| verification_encouraged \| verification_required`, default **`open`** |
| `verification_enforced_from` | `DateField` | null — the policy is inert before this date |
| `verification_grace_period_days` | `PositiveSmallIntegerField` | default **14** — how long a pending student may use gated actions |
| `verification_required_to_review` | `BooleanField` | default **`False`** |
| `id_review_retention_days` | `PositiveSmallIntegerField` | default **7** (ADR-003) |
| `is_active` | `BooleanField` | default `True` |

Verification is a per-university policy chosen at onboarding and changeable
without a deploy (ADR-003). By default it is off, and it earns a badge rather
than gating anything.

**Constraints / indexes**

- `UNIQUE (subdomain)`, `UNIQUE (slug)`
- `Index(fields=["subdomain", "is_active"])` — hit on every request by the
  tenancy middleware, so it must be covering
- `CheckConstraint` on each `*_hsl` field matching
  `^\d{1,3}(\.\d+)?\s+\d{1,3}(\.\d+)?%\s+\d{1,3}(\.\d+)?%$`
- `CheckConstraint` — `id_review_retention_days` between 1 and 90

**Not a constraint, because it spans tables:** `signup_policy` may not be set to
`verification_required` unless the university already has at least one
`StudentProfile` with `verification_status='verified'`. Enforced by
`assert_signup_policy_is_safe(university, policy)` — one named service function,
called by every write path, with a named test for the exact failure case
(methods enabled, zero verified students, attempt to require). See ADR-003.

Policy changes apply **at signup only**. Existing unverified users keep their
access and are prompted, never blocked.

### `Campus`

A university may have several. `PropertyCampusDistance` references a campus by
name (ADR-002), so this table gives those names a home and coordinates.

| Field | Type | Notes |
|---|---|---|
| `university` | FK → `University` | `CASCADE` |
| `name` | `CharField(100)` | "Main Campus", "Ruiru Campus" |
| `town`, `county` | `CharField` | |
| `latitude` | `FloatField` | −90..90 |
| `longitude` | `FloatField` | −180..180 |
| `is_main` | `BooleanField` | |

**Constraints:** `UNIQUE (university, name)`; `UniqueConstraint(fields=["university"], condition=Q(is_main=True), name="one_main_campus")`

---

## Identity and roles (ADR-003)

### `User`

Identity and authentication **only**. No `user_type`.

| Field | Type | Notes |
|---|---|---|
| `email` | `EmailField` | unique, `USERNAME_FIELD`, lowercased on save |
| `password` | | inherited |
| `first_name`, `last_name` | `CharField(100)` | |
| `phone_number` | `CharField(13)` | E.164 Kenyan, unique when non-blank |
| `phone_verified` | `BooleanField` | |
| `email_verified` | `BooleanField` | |
| `avatar_url` | `URLField` | blank |
| `is_active`, `is_staff`, `is_superuser` | `BooleanField` | `is_staff` is the **only** meaning of "platform admin" |
| `last_login`, `date_joined` | | inherited |

Built on `AbstractBaseUser` + `PermissionsMixin`, **not** `AbstractUser` — the
`username` column is gone (`docs/AUDIT.md` §7 item 11). Capability is derived in
`accounts.capabilities` and returned to the client as an explicit set on
`/auth/me/`; the client never re-derives it from model shapes.

**Constraints / indexes**

- `UNIQUE (email)`
- `UniqueConstraint(fields=["phone_number"], condition=~Q(phone_number=""), name="uniq_phone_when_set")`
- `Index(fields=["email"])`

### `LandlordProfile`

| Field | Type | Notes |
|---|---|---|
| `user` | `OneToOneField` → `User` | `CASCADE` |
| `business_name` | `CharField(200)` | blank |
| `kra_pin` | `CharField(11)` | blank; validated `^[AP]\d{9}[A-Z]$` |
| `national_id` | `CharField(20)` | blank, write-only in the API |
| `id_document_key` | `CharField(500)` | blank; key in the **private** documents bucket (ADR-007), never a URL |
| `verification_status` | `CharField` | `unverified \| pending \| verified \| rejected` |
| `verified_at` | `DateTimeField` | null |
| `verified_by` | FK → `User` | `SET_NULL`, staff only |
| `payout_phone` | `CharField(13)` | blank — M-Pesa number, for later |
| `claims_received_count` | `PositiveIntegerField` | denormalised, refreshed by the deadline job |
| `disputes_raised_count` | `PositiveIntegerField` | denormalised |
| `disputes_upheld_count` | `PositiveIntegerField` | denormalised |

`dispute_rate` and `dispute_upheld_rate` are computed from these (ADR-004).
Admin-visible from the start; a public trust signal only once the denominator
is large enough to mean something — below roughly ten claims, any ratio
misleads. A per-landlord rate limit on disputes raised per rolling 30 days is
configured by `settings.MAX_DISPUTES_PER_LANDLORD_PER_MONTH`.

**Indexes:** `Index(fields=["verification_status"])`

### `CaretakerAssignment`

A user authorised to manage **one** property, granted by that property's
landlord. Shipped with `Property`, because the foreign key to it is what
defines the model.

| Field | Type | Notes |
|---|---|---|
| `user` | FK → `User` | `CASCADE` |
| `property` | FK → `Property` | `CASCADE` |
| `granted_by` | FK → `User` | `PROTECT` — the landlord |
| `permissions` | `ArrayField(CharField(32))` | see below |
| `is_active` | `BooleanField` | revocation is a flag, not a delete |
| `revoked_at` | `DateTimeField` | null |
| `revoked_by` | FK → `User` | `SET_NULL` |

`permissions` values, fixed by ADR-003 and validated against
`accounts.capabilities.CaretakerPermission` on write:

`manage_units`, `manage_vacancy`, `manage_photos`, `set_availability`,
`resolve_tenancy_claims`, `respond_inquiries`.

Explicitly **not** available to a caretaker: deleting a property, transferring
ownership, creating or revoking assignments, editing the landlord profile or
any payout field, and posting a `ReviewResponse`.

`resolve_tenancy_claims` is safe to grant because the tenant initiates the
claim (ADR-004), so confirming is acknowledging someone else's assertion rather
than creating a record from nothing.

**Constraints / indexes**

- `UniqueConstraint(fields=["user", "property"], condition=Q(is_active=True), name="uniq_active_assignment")`
- `Index(fields=["property", "is_active"])` — the object-permission check
- `Index(fields=["user", "is_active"])`

### `UniversityStaffProfile`

A member of university staff, scoped to exactly one institution. Its only
capability today is that tenant's student verification queue (ADR-003).

| Field | Type | Notes |
|---|---|---|
| `user` | `OneToOneField` → `User` | `CASCADE` |
| `university` | FK → `University` | `PROTECT` |
| `job_title` | `CharField(120)` | blank |
| `can_review_verifications` | `BooleanField` | default `True` |
| `is_active` | `BooleanField` | |

**Constraints / indexes**

- `UNIQUE (user)`
- `Index(fields=["university", "is_active"])`

This role can read student ID documents for its own tenant, so it widens the
blast radius of a compromised account. Every document read is logged (see
`VerificationDocumentAccess`).

### `StudentProfile`

| Field | Type | Notes |
|---|---|---|
| `user` | `OneToOneField` → `User` | `CASCADE` |
| `university` | FK → `University` | `PROTECT` |
| `student_email` | `EmailField` | blank; must match a `University.student_email_domains` entry |
| `verification_status` | `CharField(16)` | `unverified \| pending \| verified \| rejected` |
| `verification_method` | `CharField(24)` | `email_domain \| student_id_upload`, blank when unverified |
| `verified_at` | `DateTimeField` | null |
| `verified_by` | FK → `User` | `SET_NULL`, null — null for the automated email path |
| `rejection_reason` | `CharField(255)` | blank |
| `grace_period_ends_at` | `DateTimeField` | null — set at signup when the school gates actions. Read access never depends on it (ADR-003) |
| `year_of_study` | `PositiveSmallIntegerField` | null, 1..8 |
| `course` | `CharField(200)` | blank |

**Constraints / indexes**

- `UNIQUE (user)`
- `UniqueConstraint(fields=["student_email"], condition=~Q(student_email=""), name="uniq_student_email")`
- `Index(fields=["university", "verification_status"])`
- `CheckConstraint` — `verification_status='verified'` requires a non-null
  `verification_method`
- `CheckConstraint` — `verification_status='rejected'` requires a
  `rejection_reason`

### `StudentEmailVerification`

The automated path. No human in the loop: the student confirms an address whose
domain matches the university's, and the status goes straight to `verified`.

| Field | Type | Notes |
|---|---|---|
| `student_profile` | FK → `StudentProfile` | `CASCADE` |
| `email` | `EmailField` | the address being proved |
| `token_hash` | `CharField(64)` | SHA-256 of a single-use signed token; the token itself is never stored |
| `expires_at` | `DateTimeField` | |
| `consumed_at` | `DateTimeField` | null |

**Constraints / indexes**

- `UNIQUE (token_hash)`
- `Index(fields=["student_profile", "consumed_at"])`
- `CheckConstraint` — `expires_at > created_at`

### `StudentVerificationRequest`

The manual path, for schools that issue no student addresses.

| Field | Type | Notes |
|---|---|---|
| `student_profile` | FK → `StudentProfile` | `CASCADE` |
| `document_key` | `CharField(500)` | key in the **private** documents bucket (ADR-007) |
| `document_content_type` | `CharField(64)` | validated against actual bytes, not the header |
| `document_byte_size` | `PositiveIntegerField` | capped |
| `submitted_at` | `DateTimeField` | |
| `status` | `CharField(16)` | `pending \| approved \| rejected \| withdrawn` |
| `reviewer` | FK → `User` | `SET_NULL`, null — university staff or platform staff |
| `decided_at` | `DateTimeField` | null |
| `notes` | `TextField` | blank, staff-visible only |
| `document_deleted_at` | `DateTimeField` | null — set by the retention job |

**Constraints / indexes**

- `UniqueConstraint(fields=["student_profile"], condition=Q(status="pending"), name="one_open_verification_request")`
- `Index(fields=["status", "submitted_at"])` — the queue, oldest first
- `Index(fields=["decided_at"], condition=Q(document_deleted_at__isnull=True))` —
  what the retention job scans
- `CheckConstraint` — a non-`pending` status requires `decided_at`

**Retention.** The document is deleted `University.id_review_retention_days`
after `decided_at` by a scheduled job. The decision is retained; the image is
not. `document_deleted_at` records that it happened, so a document past its
window with a null value here is an alertable condition — the retention promise
must be verified, not assumed.

### `VerificationDocumentAccess`

Every read of an ID document, logged. This is what makes the retention and
access promises in ADR-003 auditable.

| Field | Type | Notes |
|---|---|---|
| `request` | FK → `StudentVerificationRequest` | `CASCADE` |
| `reader` | FK → `User` | `PROTECT` |
| `accessed_at` | `DateTimeField` | |
| `ip_address` | `GenericIPAddressField` | null |
| `user_agent` | `CharField(255)` | blank |

**Indexes:** `Index(fields=["request", "-accessed_at"])`, `Index(fields=["reader", "-accessed_at"])`

---

## Properties (ADR-002)

### `Property`

A building or compound. Owned by one landlord; may serve many universities.

| Field | Type | Notes |
|---|---|---|
| `landlord` | FK → `LandlordProfile` | `PROTECT` |
| `name` | `CharField(200)` | "Wendani Hostel Block C" |
| `slug` | `SlugField(220)` | unique |
| `description` | `TextField` | |
| `property_type` | `CharField(20)` | see below |
| `county` | `CharField(50)` | `choices=KENYAN_COUNTIES` |
| `town` | `CharField(100)` | |
| `estate` | `CharField(100)` | "Kahawa Wendani" — the unit of local address |
| `street` | `CharField(200)` | blank; often absent in practice |
| `landmark` | `CharField(200)` | blank; "opposite Naivas" — how people navigate |
| `postal_address` | `CharField(50)` | blank; "P.O. Box 43844-00100" |
| `latitude`, `longitude` | `FloatField` | null; for map pins and distance computation only (ADR-006) |
| `has_water_tank` | `BooleanField` | |
| `has_borehole` | `BooleanField` | |
| `has_backup_power` | `BooleanField` | |
| `has_perimeter_wall` | `BooleanField` | |
| `has_security_guard` | `BooleanField` | |
| `has_cctv` | `BooleanField` | |
| `has_wifi` | `BooleanField` | |
| `has_parking` | `BooleanField` | |
| `caretaker_on_site` | `BooleanField` | |
| `status` | `CharField(20)` | `draft \| published \| suspended \| archived` |
| `published_at` | `DateTimeField` | null |
| `view_count` | `PositiveIntegerField` | updated with `F()` **and refreshed** — see `docs/AUDIT.md` §4.1 |

**`PROPERTY_TYPES`** — Kenyan typology, replacing the US list:

```python
PROPERTY_TYPES = [
    ("bedsitter",    "Bedsitter"),
    ("single_room",  "Single Room"),
    ("one_bedroom",  "One Bedroom"),
    ("two_bedroom",  "Two Bedroom"),
    ("three_bedroom","Three Bedroom"),
    ("hostel_block", "Hostel Block"),
    ("shared_house", "Shared House"),
    ("maisonette",   "Maisonette"),
    ("other",        "Other"),
]
```

No `condo`, no `townhouse`.

**Constraints / indexes**

- `UNIQUE (slug)`
- `Index(fields=["status", "published_at"])`
- `Index(fields=["county", "town"])`
- `Index(fields=["landlord", "status"])`
- `CheckConstraint(condition=Q(latitude__isnull=True) | (Q(latitude__gte=-90) & Q(latitude__lte=90)), name="property_lat_range")` and the longitude equivalent
- `CheckConstraint(condition=~Q(status="published") | Q(published_at__isnull=False), name="published_needs_timestamp")`

### `Unit`

The lettable thing. Vacancy lives here — this is what the draft schema had no
place for.

| Field | Type | Notes |
|---|---|---|
| `property` | FK → `Property` | `CASCADE` |
| `label` | `CharField(50)` | "B12", or "Bedsitters" for a fungible pool |
| `unit_type` | `CharField(20)` | same choices as `property_type` |
| `rent_kes` | `Decimal(10,2)` | **KES per month** |
| `deposit_kes` | `Decimal(10,2)` | null |
| `water_included` | `BooleanField` | |
| `electricity_included` | `BooleanField` | — token metering is the norm otherwise |
| `wifi_included` | `BooleanField` | |
| `furnished` | `CharField(20)` | `unfurnished \| semi_furnished \| furnished` |
| `size_sqm` | `PositiveSmallIntegerField` | null — **square metres** |
| `bedrooms` | `PositiveSmallIntegerField` | 0 for a bedsitter or single room |
| `has_private_bathroom` | `BooleanField` | replaces the draft's `bathrooms ≥ 1`, which excluded shared ablutions |
| `has_kitchenette` | `BooleanField` | |
| `floor` | `SmallIntegerField` | null |
| `total_count` | `PositiveSmallIntegerField` | how many identical units exist |
| `vacant_count` | `PositiveSmallIntegerField` | how many are free right now |
| `available_from` | `DateField` | null |
| `min_stay_months` | `PositiveSmallIntegerField` | default **4** — one semester, not 12 |
| `is_active` | `BooleanField` | |

**Constraints / indexes**

- `UNIQUE (property, label)`
- `CheckConstraint(condition=Q(vacant_count__lte=F("total_count")), name="vacant_not_over_total")`
- `CheckConstraint(condition=Q(rent_kes__gt=0), name="rent_positive")`
- `CheckConstraint(condition=Q(deposit_kes__isnull=True) | Q(deposit_kes__gte=0), name="deposit_non_negative")`
- `Index(fields=["property", "is_active"])`
- `Index(fields=["rent_kes"])` — the price filter
- `Index(fields=["unit_type", "rent_kes"])` — the common combined filter

### `UnitPhoto` (ADR-007)

| Field | Type | Notes |
|---|---|---|
| `unit` | FK → `Unit` | `CASCADE` |
| `original_key` | `CharField(500)` | object key in the bucket |
| `thumb_key`, `medium_key`, `large_key` | `CharField(500)` | blank until generated |
| `processing_status` | `CharField(20)` | `pending \| ready \| failed` |
| `caption` | `CharField(200)` | blank |
| `is_primary` | `BooleanField` | |
| `sort_order` | `PositiveSmallIntegerField` | |
| `width`, `height` | `PositiveSmallIntegerField` | null; for layout stability |

ADR-007 resolved to django-rq generating variants ourselves, so these columns
stay. Keys refer to the **public** media bucket; verification documents live in
a separate private bucket with its own storage backend and never share this
one.

**Constraints / indexes**

- `UniqueConstraint(fields=["unit"], condition=Q(is_primary=True), name="one_primary_photo_per_unit")` — the draft enforced this in `save()`, where a bulk update bypasses it
- `Index(fields=["unit", "sort_order"])`

### `PropertyCampusDistance` (ADR-002)

| Field | Type | Notes |
|---|---|---|
| `property` | FK → `Property` | `CASCADE` |
| `university` | FK → `University` | `PROTECT` |
| `campus` | FK → `Campus` | `PROTECT` |
| `straight_line_km` | `Decimal(5,2)` | **NOT NULL** — haversine, computed on save |
| `walking_distance_km` | `Decimal(5,2)` | **null** until the routing job runs |
| `walking_minutes` | `PositiveSmallIntegerField` | **null** until the routing job runs |
| `routed_at` | `DateTimeField` | null — when routing last succeeded |
| `route_provider` | `CharField(32)` | blank; `openrouteservice`, … |
| `matatu_route` | `CharField(50)` | blank; "Route 45" |
| `is_primary` | `BooleanField` | the campus this listing is marketed against |

**Constraints / indexes**

- `UNIQUE (property, campus)`
- `UniqueConstraint(fields=["property"], condition=Q(is_primary=True), name="one_primary_campus")`
- `Index(fields=["university", "straight_line_km"])` — **the platform's primary query**
- `Index(fields=["routed_at"])` — the routing job takes the oldest first
- `CheckConstraint` — `0 <= straight_line_km <= 500`
- `CheckConstraint` — `walking_minutes` and `walking_distance_km` are either
  both null or both set, and `routed_at` is non-null whenever they are

The two distance figures mean different things and must never be conflated
(ADR-002). `straight_line_km` is an honest lower bound and is always present;
walking figures come only from a routing provider and stay null otherwise.
**Walking time is never derived from straight-line distance**, and any UI
showing the straight-line figure must label it as such.

A property with zero rows here is invisible to every tenant. Enforce ≥ 1 at the
serializer and monitor for orphans (ADR-002).

**A property with no coordinates cannot join a campus at all.**
`straight_line_km` is `NOT NULL` and is always present, so there is no honest
value for an unpinned property; the join refuses with a named error rather than
letting the database report a null column. The consequence is that an unpinned
property is invisible to every tenant, so the serializer must require
coordinates before publication.

---

## Transactions

### `Application`

A student applying for a unit. Distinct from `Inquiry`: an application is
intent to take the unit.

| Field | Type | Notes |
|---|---|---|
| `unit` | FK → `Unit` | `PROTECT` |
| `applicant` | FK → `User` | `PROTECT` |
| `status` | `CharField(20)` | `submitted \| under_review \| accepted \| rejected \| withdrawn \| expired` |
| `move_in_date` | `DateField` | requested |
| `intended_months` | `PositiveSmallIntegerField` | |
| `message` | `TextField` | blank |
| `decided_by` | FK → `User` | `SET_NULL`; landlord or caretaker |
| `decided_at` | `DateTimeField` | null |
| `decision_note` | `TextField` | blank |

**Accepting an application creates a confirmed `Tenancy` directly**, with
`confirmation_source='application'` and no `TenancyClaim` (ADR-004). The
platform witnessed the agreement — it holds the application, the acceptance, the
actor and the timestamp — so a second confirmation adds latency and a dispute
surface for nothing. This is the primary control on dispute-queue volume.

**Constraints / indexes**

- `UniqueConstraint(fields=["unit", "applicant"], condition=Q(status__in=["submitted", "under_review"]), name="one_open_application")`
- `Index(fields=["unit", "status"])`
- `Index(fields=["applicant", "-created_at"])`
- `CheckConstraint(condition=Q(decided_at__isnull=True) | Q(decided_by__isnull=False), name="decision_has_an_author")`

### `TenancyClaim` (ADR-004)

**Only for stays the platform did not witness** — off-platform arrangements and
pre-platform history. An accepted `Application` creates a confirmed `Tenancy`
directly with no claim at all. This is the primary control on dispute volume;
do not route witnessed tenancies through here for uniformity.

The tenant initiates. The landlord and any assigned caretaker have
`settings.TENANCY_CONFIRMATION_WINDOW_DAYS` (7) to confirm or dispute; silence
auto-confirms.

| Field | Type | Notes |
|---|---|---|
| `unit` | FK → `Unit` | `PROTECT` |
| `claimant` | FK → `User` | `PROTECT` — the tenant |
| `start_date` | `DateField` | |
| `end_date` | `DateField` | null while ongoing |
| `monthly_rent_kes` | `Decimal(10,2)` | as claimed |
| `status` | `CharField(16)` | `pending \| confirmed \| disputed \| escalated \| withdrawn \| expired` |
| `confirmation_deadline` | `DateTimeField` | `created_at + window` |
| `dispute_reason` | `CharField(20)` | `dates_incorrect \| never_tenanted \| duplicate` **as raised**; never rewritten |
| `dispute_note` | `TextField` | blank — **additional** to the reason, never instead |
| `disputed_by` | FK → `User` | `SET_NULL`, null |
| `disputed_at` | `DateTimeField` | null |
| `proposed_start_date` | `DateField` | null — the disputer's correction |
| `proposed_end_date` | `DateField` | null |
| `counter_start_date` | `DateField` | null — the tenant's single counter |
| `counter_end_date` | `DateField` | null |
| `dispute_withdrawn_at` | `DateTimeField` | null — the disputer took it back; clears the annotation |
| `tenant_accepted_correction_at` | `DateTimeField` | null — **evidence**, not necessarily a resolution |
| `escalated_at` | `DateTimeField` | null — entered the admin queue |
| `escalation_reason` | `CharField(28)` | `counter_unresolved \| correction_defeats_review \| identity_disputed \| duplicate_unmatched` |
| `escalation_deadline` | `DateTimeField` | null — `escalated_at + DISPUTE_RESOLUTION_WINDOW_DAYS` |
| `resolved_by` | FK → `User` | `SET_NULL`, null — null when resolved by timeout |
| `resolved_at` | `DateTimeField` | null |

**Constraints / indexes**

- `UniqueConstraint(fields=["unit", "claimant"], condition=Q(status__in=["pending", "disputed", "escalated"]), name="one_open_claim_per_unit")`
- `CheckConstraint` — `end_date` is null or `>= start_date`
- `CheckConstraint` — a `disputed` or `escalated` status requires a non-blank
  `dispute_reason`; an untyped dispute cannot be routed
- `CheckConstraint` — `dispute_reason='dates_incorrect'` requires
  `proposed_start_date`
- `CheckConstraint` — `counter_start_date` requires `proposed_start_date`; you
  cannot counter a correction that was not proposed
- `CheckConstraint` — a terminal status requires `resolved_at`
- `CheckConstraint` — `escalation_deadline` is non-null exactly when
  `escalated_at` is
- `Index(fields=["status", "confirmation_deadline"])` — the auto-confirm job,
  and the overdue-claim alert
- `Index(fields=["status", "escalation_deadline"])` — the dispute
  auto-resolution job, and the SLA alert. **Both alerts read the oldest row,
  not the count** (`docs/OPERATIONS.md`)
- `Index(fields=["claimant", "-created_at"])` — the per-user rate limit
- `Index(fields=["disputed_by", "-disputed_at"])` — the per-landlord dispute
  rate limit and the `dispute_rate` metric

**Dispute routing (ADR-004).** Only three cases reach an admin:

| Reason | Path |
|---|---|
| `dates_incorrect` | Disputer proposes dates → tenant accepts (confirms, **no admin**) or counters once → an unaccepted counter escalates as `counter_unresolved`. **A correction that would drop the stay under the review minimum cannot auto-resolve at all**, even with the tenant's acceptance: it escalates as `correction_defeats_review` (ADR-004) |
| `duplicate` | Auto-resolves if a confirmed overlapping tenancy exists for the same unit and user; otherwise escalates |
| `never_tenanted` | Escalates |

**Symmetric timeout.** An escalated claim past `escalation_deadline`
auto-resolves in the tenant's favour: the claim confirms with
`confirmation_source='dispute_timeout'`, and the resulting `Review` carries
a neutral annotation, derived at read time rather than stored.

### `Tenancy` (ADR-004)

The evidence that a stay happened. Nothing else can vouch for a review.

**Two sources, and they must not converge:**

- **witnessed** — an `Application` accepted on-platform creates this directly in
  confirmed state, `confirmation_source='application'`, `claim` null. No
  confirmation window, no dispute surface, no queue entry.
- **claimed** — a `TenancyClaim` that confirmed, by any route.

| Field | Type | Notes |
|---|---|---|
| `unit` | FK → `Unit` | `PROTECT` |
| `tenant` | FK → `User` | `PROTECT` |
| `application` | FK → `Application` | `SET_NULL`, null — the witnessed path |
| `claim` | FK → `TenancyClaim` | **`PROTECT`**, null — the claimed path. PROTECT because the review annotation is derived from this record, so it must survive |
| `confirmation_source` | `CharField(20)` | `application \| landlord \| caretaker \| auto \| admin \| dispute_timeout` |
| `confirmed_by` | FK → `User` | `SET_NULL`, **null for `auto` and `dispute_timeout`** |
| `confirmed_at` | `DateTimeField` | |
| `was_disputed` | `BooleanField` | default `False` — a dispute occurred, whatever its outcome |
| `start_date` | `DateField` | |
| `end_date` | `DateField` | null while ongoing |
| `monthly_rent_kes` | `Decimal(10,2)` | |
| `status` | `CharField(16)` | `active \| ended` |

**Constraints / indexes**

- `CheckConstraint(condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")), name="tenancy_end_after_start")`
- `UniqueConstraint(fields=["unit", "tenant", "start_date"], name="tenancy_unique_per_unit_tenant_start")`
- **`ExclusionConstraint`** over `(unit =, tenant =, daterange(start_date,
  coalesce(end_date, 'infinity'), '[]') &&)` where `status='active'`, named
  `tenancy_no_overlapping_active_stay`. Requires `btree_gist`. A serializer
  cannot see a concurrent insert; this can. It is also the exact predicate the
  `duplicate` dispute auto-resolution queries.

  Scoped per unit **and per tenant**. `Unit` is a pool model, so one row can be
  forty identical bedsitters; a per-unit-only exclusion would cap the whole
  block at one active tenancy. Vacancy comes from `vacant_count`, never from
  the absence of a tenancy row. `end_date IS NULL` coalesces to infinity, so an
  ongoing stay overlaps everything after it.
- `CheckConstraint` — `confirmation_source='application'` requires a non-null
  `application` and a null `claim`; any other source requires a non-null `claim`
  and a null `application`. The two paths cannot blur.
- `CheckConstraint` — `confirmed_by` is null if and only if
  `confirmation_source` is `auto` or `dispute_timeout`
- `Index(fields=["tenant", "-start_date"])`
- `Index(fields=["unit", "status"])`
- `Index(fields=["confirmation_source"])` — the volume-control metric
- `CheckConstraint(condition=Q(monthly_rent_kes__gt=0), name="tenancy_rent_positive")`

---

## Reviews (ADR-004)

### `Review`

| Field | Type | Notes |
|---|---|---|
| `tenancy` | `OneToOneField` → `Tenancy` | `PROTECT`, **NOT NULL** — the whole point |
| `rating` | `PositiveSmallIntegerField` | 1..5 |
| `cleanliness_rating` | `PositiveSmallIntegerField` | null, 1..5 |
| `security_rating` | `PositiveSmallIntegerField` | null, 1..5 |
| `water_reliability_rating` | `PositiveSmallIntegerField` | null, 1..5 — the complaint that actually recurs |
| `landlord_rating` | `PositiveSmallIntegerField` | null, 1..5 |
| `value_rating` | `PositiveSmallIntegerField` | null, 1..5 |
| `comment` | `TextField` | max 2000 |
| `would_recommend` | `BooleanField` | null |
| `editable_until` | `DateTimeField` | `created_at + 14 days` |
| `is_published` | `BooleanField` | default `True` |
| `hidden_reason` | `CharField(200)` | blank; staff only |

**No `disputed_by_landlord` column.** The annotation is derived at read time by
`review_dispute_annotation(review)`, which returns `None` for a withdrawn
dispute and — behind a settings-gated hook that is off by default — for a
landlord whose `dispute_upheld_rate` is too low over a large enough sample.
Store facts, derive presentation: changing the policy is a function edit rather
than a migration over live reviews (ADR-004).

The dispute annotation reads "the landlord disputed this stay" and is
**neutral** — never a discredit. The review is not greyed out, collapsed,
excluded from the average, or labelled unverified (ADR-004).

The verified badge is read from `review.tenancy.tenant.student_profile.verification_status`
at render time, not copied onto the review. When the university has
`verification_required_to_review = False` (the default), an unverified student
may still post and the badge is simply absent (ADR-003).

The reviewer is `review.tenancy.tenant`; the unit is `review.tenancy.unit`.
Neither is duplicated onto the review — a denormalised copy is a chance for the
two to disagree, and disagreement here is the trust property failing.

**Constraints / indexes**

- `UNIQUE (tenancy)` — from the `OneToOneField`
- `CheckConstraint(condition=Q(rating__gte=1) & Q(rating__lte=5), name="review_rating_range")` and the same for each category rating (allowing null)
- `Index(fields=["is_published", "-created_at"])`
- `Index(fields=["tenancy"])`

The minimum-stay rule cannot be a `CheckConstraint`, because it compares
against "today" for an ongoing tenancy. Resolved in ADR-004: the threshold is
`settings.REVIEW_MINIMUM_STAY_DAYS` (30), and it is enforced in **one** service
function, `assert_tenancy_is_reviewable(tenancy)`, that the serializer, the
admin and any future path all go through. It is tested directly at that
boundary, not only through the API. This is the single documented exception —
every other invariant here is a database constraint.

### `ReviewResponse`

| Field | Type | Notes |
|---|---|---|
| `review` | `OneToOneField` → `Review` | `CASCADE` — one response, ever |
| `author` | FK → `User` | `PROTECT`; landlord or assigned caretaker |
| `body` | `TextField` | max 1000 |

**Constraints:** `UNIQUE (review)` — enforced by the schema, not by the
`if review.landlord_response:` check the draft used.

---

## Engagement

### `SavedProperty`

| Field | Type | Notes |
|---|---|---|
| `user` | FK → `User` | `CASCADE` |
| `property` | FK → `Property` | `CASCADE` |
| `note` | `CharField(200)` | blank |

**Constraints / indexes:** `UNIQUE (user, property)`; `Index(fields=["user", "-created_at"])`

Saved at the **property** level, not the unit: a student saves "that hostel",
and units churn.

### `Inquiry`

A question. Lighter than an `Application` — most contacts are "is it still
vacant?".

| Field | Type | Notes |
|---|---|---|
| `property` | FK → `Property` | `CASCADE` |
| `unit` | FK → `Unit` | `SET_NULL`, null — may be about the property generally |
| `sender` | FK → `User` | `PROTECT` |
| `message` | `TextField` | |
| `contact_phone` | `CharField(13)` | blank |
| `preferred_viewing_date` | `DateField` | null |
| `status` | `CharField(20)` | `new \| read \| replied \| closed` |
| `read_at` | `DateTimeField` | null |

Replies live in `InquiryReply` (FK → `Inquiry`, `author`, `body`), not in a
single `landlord_reply` text column — the draft's shape allowed exactly one
reply and no conversation.

**Indexes:** `Index(fields=["property", "status"])`, `Index(fields=["sender", "-created_at"])`

---

## Relationship summary

```
University 1──* Campus
University 1──* StudentProfile
University 1──* UniversityStaffProfile
University 1──* PropertyCampusDistance *──1 Property     (ADR-002: many-to-many
                                                          carrying distance)
Campus     1──* PropertyCampusDistance

User 1──1 LandlordProfile 1──* Property 1──* Unit 1──* UnitPhoto
User 1──1 StudentProfile 1──* StudentEmailVerification
                         1──* StudentVerificationRequest 1──* VerificationDocumentAccess
User 1──1 UniversityStaffProfile
User 1──* CaretakerAssignment *──1 Property

Unit 1──* Application   *──1 User
Application 0..1──1 Tenancy                              (ADR-004: WITNESSED —
                                                          acceptance creates a
                                                          confirmed tenancy,
                                                          no claim)
Unit 1──* TenancyClaim  *──1 User                        (ADR-004: CLAIMED —
                                                          only for stays the
                                                          platform did not
                                                          witness)
TenancyClaim 0..1──1 Tenancy
Unit 1──* Tenancy       *──1 User

Tenancy 1──0..1 Review 1──0..1 ReviewResponse            (ADR-004: a review
                                                          cannot exist without
                                                          a tenancy)

User *──* Property  through SavedProperty
User 1──* Inquiry   *──1 Property, 0..1 Unit
Inquiry 1──* InquiryReply
```

## Tenant scoping (ADR-001)

Reached through `PropertyCampusDistance`:

| Model | How it scopes |
|---|---|
| `Property` | via `campus_distances__university` |
| `Unit`, `UnitPhoto` | via `property` |
| `Application`, `TenancyClaim`, `Tenancy`, `Inquiry` | via `unit__property` |
| `Review`, `ReviewResponse` | via `tenancy__unit__property` |
| `StudentProfile`, `UniversityStaffProfile` | direct FK |
| `StudentVerificationRequest` | via `student_profile__university` |
| `User`, `LandlordProfile` | **not scoped** — a landlord may serve several universities |
| `CaretakerAssignment` | via `property` |

`User` being unscoped is deliberate and worth noting in review: it means user
enumeration is not tenant-isolated, so any endpoint exposing user data needs its
own authorization check rather than relying on the tenant filter.

## Background jobs (ADR-007: django-rq on Redis)

Four jobs are load-bearing. Each fails silently if the worker stops, so each
needs an alert on its *backlog* rather than on its own success.

| Job | Trigger | Alert on |
|---|---|---|
| Auto-confirm tenancy claims | scheduled, hourly | oldest `TenancyClaim` past `confirmation_deadline` still `pending` |
| Auto-resolve escalated disputes | scheduled, hourly | oldest `escalated` claim past `escalation_deadline` |
| Route campus walking distance | on `PropertyCampusDistance` create; a sweep takes the oldest, **nulls first** | rows with null `walking_minutes` older than a day; provider quota |
| Delete verification documents | scheduled, hourly | oldest `StudentVerificationRequest` past its retention window with `document_deleted_at` null |
| Generate image variants | on `UnitPhoto` create | oldest `UnitPhoto` in `processing_status='pending'` |

**Alert on the age of the oldest unresolved item, never on volume.** Forty items
with the oldest two days old is a busy week; three items with the oldest
thirteen days old is an emergency, and a volume threshold does not fire on it.
Thresholds and the dispute SLA are in `docs/OPERATIONS.md`.

## Storage buckets (ADR-007)

| Bucket | Contents | Access |
|---|---|---|
| public media | `UnitPhoto` originals and variants, `University.logo_url` | unsigned, CDN-served |
| private documents | `StudentVerificationRequest.document_key`, `LandlordProfile.id_document_url` | signed URLs, 5-minute expiry, never CDN |

Separate `STORAGES` entries with separate backend classes, not a key-prefix
convention: a convention is one careless `default_storage.save()` away from
publishing someone's national ID.

## Migration note

There is no production data. Migrations are **reset**, not chained: delete the
existing `0001_initial` files and generate a fresh initial migration per app.
This is the last moment at which that is free.
