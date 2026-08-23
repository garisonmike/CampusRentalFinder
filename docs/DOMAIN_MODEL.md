# Target Domain Model

**Status:** Proposed — the schema rewrite implements this.
**Date:** 2026-08-23

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
| `email_domains` | `ArrayField(CharField(255))` | `["students.ku.ac.ke", "ku.ac.ke"]` — student verification (ADR-003) |
| `county` | `CharField(50)` | `choices=KENYAN_COUNTIES`, 47 entries |
| `town` | `CharField(100)` | "Nairobi", "Juja", "Eldoret" |
| `logo_url` | `URLField` | on the media CDN (ADR-007) |
| `favicon_url` | `URLField` | blank |
| `primary_hsl` | `CharField(32)` | `"142 71% 45%"` — validated (ADR-005) |
| `secondary_hsl` | `CharField(32)` | |
| `accent_hsl` | `CharField(32)` | |
| `is_active` | `BooleanField` | default `True` |

**Constraints / indexes**

- `UNIQUE (subdomain)`, `UNIQUE (slug)`
- `Index(fields=["subdomain", "is_active"])` — hit on every request by the
  tenancy middleware, so it must be covering
- `CheckConstraint` on each `*_hsl` field matching
  `^\d{1,3}(\.\d+)?\s+\d{1,3}(\.\d+)?%\s+\d{1,3}(\.\d+)?%$`

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
`username` column is gone (`docs/AUDIT.md` §7 item 11).

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
| `id_document_url` | `URLField` | blank; **private bucket** (ADR-007) |
| `verification_status` | `CharField` | `unverified \| pending \| verified \| rejected` |
| `verified_at` | `DateTimeField` | null |
| `verified_by` | FK → `User` | `SET_NULL`, staff only |
| `payout_phone` | `CharField(13)` | blank — M-Pesa number, for later |

**Indexes:** `Index(fields=["verification_status"])`

### `CaretakerAssignment`

A user authorised to manage **one** property, granted by that property's
landlord.

| Field | Type | Notes |
|---|---|---|
| `user` | FK → `User` | `CASCADE` |
| `property` | FK → `Property` | `CASCADE` |
| `granted_by` | FK → `User` | `PROTECT` — the landlord |
| `permissions` | `ArrayField(CharField(32))` | see below |
| `is_active` | `BooleanField` | revocation is a flag, not a delete |
| `revoked_at` | `DateTimeField` | null |
| `revoked_by` | FK → `User` | `SET_NULL` |

`permissions` values: `manage_vacancy`, `manage_photos`, `respond_inquiries`,
`confirm_tenancy`. **Not** `change_price`, `delete_property` or
`grant_assignments` — see ADR-003's second flagged flaw, which is still open.

**Constraints / indexes**

- `UniqueConstraint(fields=["user", "property"], condition=Q(is_active=True), name="uniq_active_assignment")`
- `Index(fields=["property", "is_active"])` — the object-permission check
- `Index(fields=["user", "is_active"])`

### `StudentProfile`

| Field | Type | Notes |
|---|---|---|
| `user` | `OneToOneField` → `User` | `CASCADE` |
| `university` | FK → `University` | `PROTECT` |
| `student_email` | `EmailField` | blank; must match a `university.email_domains` entry |
| `verification_method` | `CharField` | `none \| email_domain \| manual_id` (ADR-003 flaw 1) |
| `is_verified` | `BooleanField` | |
| `verified_at` | `DateTimeField` | null |
| `year_of_study` | `PositiveSmallIntegerField` | null, 1..8 |
| `course` | `CharField(200)` | blank |

**Constraints / indexes**

- `UNIQUE (user)`
- `UniqueConstraint(fields=["student_email"], condition=~Q(student_email=""), name="uniq_student_email")`
- `Index(fields=["university", "is_verified"])`
- `CheckConstraint(condition=Q(is_verified=False) | ~Q(verification_method="none"), name="verified_needs_a_method")`

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

If ADR-007's open question resolves to Cloudflare Images, the three variant key
columns and `processing_status` are dropped and a single `image_id` replaces
them.

**Constraints / indexes**

- `UniqueConstraint(fields=["unit"], condition=Q(is_primary=True), name="one_primary_photo_per_unit")` — the draft enforced this in `save()`, where a bulk update bypasses it
- `Index(fields=["unit", "sort_order"])`

### `PropertyCampusDistance` (ADR-002)

| Field | Type | Notes |
|---|---|---|
| `property` | FK → `Property` | `CASCADE` |
| `university` | FK → `University` | `PROTECT` |
| `campus` | FK → `Campus` | `PROTECT` |
| `distance_km` | `Decimal(5,2)` | haversine, computed on save |
| `walking_minutes` | `PositiveSmallIntegerField` | **null** unless routed — ADR-002's flagged flaw |
| `matatu_route` | `CharField(50)` | blank; "Route 45" |
| `is_primary` | `BooleanField` | the campus this listing is marketed against |

**Constraints / indexes**

- `UNIQUE (property, campus)`
- `UniqueConstraint(fields=["property"], condition=Q(is_primary=True), name="one_primary_campus")`
- `Index(fields=["university", "distance_km"])` — **the platform's primary query**
- `CheckConstraint(condition=Q(distance_km__gte=0) & Q(distance_km__lte=500), name="distance_sane")`

A property with zero rows here is invisible to every tenant. Enforce ≥ 1 at the
serializer and monitor for orphans (ADR-002).

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

**Constraints / indexes**

- `UniqueConstraint(fields=["unit", "applicant"], condition=Q(status__in=["submitted", "under_review"]), name="one_open_application")`
- `Index(fields=["unit", "status"])`
- `Index(fields=["applicant", "-created_at"])`
- `CheckConstraint(condition=Q(decided_at__isnull=True) | Q(decided_by__isnull=False), name="decision_has_an_author")`

### `Tenancy` (ADR-004)

The evidence that a stay happened. Nothing else can vouch for a review.

| Field | Type | Notes |
|---|---|---|
| `unit` | FK → `Unit` | `PROTECT` |
| `tenant` | FK → `User` | `PROTECT` |
| `application` | FK → `Application` | `SET_NULL`, null — where it came from |
| `confirmed_by` | FK → `User` | `PROTECT` — landlord or assigned caretaker |
| `confirmed_at` | `DateTimeField` | |
| `start_date` | `DateField` | |
| `end_date` | `DateField` | null while ongoing |
| `monthly_rent_kes` | `Decimal(10,2)` | the agreed figure, for the record |
| `status` | `CharField(20)` | `active \| ended \| disputed` |

If ADR-004's recommended claim/confirm-with-timeout variant is adopted, add
`claimed_by`, `claimed_at` and `confirmation_deadline`. **That decision is
open** and it is cheaper to make now than later.

**Constraints / indexes**

- `CheckConstraint(condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")), name="tenancy_end_after_start")`
- `UniqueConstraint(fields=["unit", "tenant", "start_date"], name="uniq_tenancy_per_unit_tenant_start")`
- `Index(fields=["tenant", "-start_date"])`
- `Index(fields=["unit", "status"])`
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

The reviewer is `review.tenancy.tenant`; the unit is `review.tenancy.unit`.
Neither is duplicated onto the review — a denormalised copy is a chance for the
two to disagree, and disagreement here is the trust property failing.

**Constraints / indexes**

- `UNIQUE (tenancy)` — from the `OneToOneField`
- `CheckConstraint(condition=Q(rating__gte=1) & Q(rating__lte=5), name="review_rating_range")` and the same for each category rating (allowing null)
- `Index(fields=["is_published", "-created_at"])`
- `Index(fields=["tenancy"])`

The minimum-stay rule (30 days) cannot be a `CheckConstraint`, because it
compares against "today" for an ongoing tenancy. It is enforced in the
serializer, and the threshold lives in settings — ADR-004's flagged flaw 2.

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
University 1──* PropertyCampusDistance *──1 Property     (ADR-002: many-to-many
                                                          carrying distance)
Campus     1──* PropertyCampusDistance

User 1──1 LandlordProfile 1──* Property 1──* Unit 1──* UnitPhoto
User 1──1 StudentProfile
User 1──* CaretakerAssignment *──1 Property

Unit 1──* Application *──1 User
Unit 1──* Tenancy     *──1 User
Application 0..1──1 Tenancy

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
| `Application`, `Tenancy`, `Inquiry` | via `unit__property` |
| `Review`, `ReviewResponse` | via `tenancy__unit__property` |
| `StudentProfile` | direct FK |
| `User`, `LandlordProfile` | **not scoped** — a landlord may serve several universities |
| `CaretakerAssignment` | via `property` |

`User` being unscoped is deliberate and worth noting in review: it means user
enumeration is not tenant-isolated, so any endpoint exposing user data needs its
own authorization check rather than relying on the tenant filter.

## Migration note

There is no production data. Migrations are **reset**, not chained: delete the
existing `0001_initial` files and generate a fresh initial migration per app.
This is the last moment at which that is free.
