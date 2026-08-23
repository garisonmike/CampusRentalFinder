# ADR-003: Authorization is object-level, not a `user_type` string

**Status:** Accepted
**Date:** 2026-08-23
**Amended:** 2026-08-24 — caretaker capabilities and student verification resolved
**Deciders:** Tech lead

## Context

The draft carries the entire authorization model in one field:
`User.user_type ∈ {tenant, landlord, admin}`. This has already produced live
defects, catalogued in `docs/AUDIT.md` §4.4:

- **`user_type` is client-supplied at registration and never validated.**
  Anyone can register as a landlord — or as an `admin`.
- **There are two unrelated meanings of "admin".** DRF's `IsAdminUser` checks
  `is_staff`, so a `user_type='admin'` user gets 403 across the admin API. But
  the object-permission checks in `rentals/views.py` and `reviews/views.py` test
  `user.user_type == 'admin'`, so that same self-declared admin **can edit or
  delete any listing or review on the platform.** That is a privilege-escalation
  path open through the public signup form.
- **The model has no way to express a caretaker.** Caretakers manage specific
  properties on a landlord's behalf. A fourth string would grant every caretaker
  authority over every property.
- **A person can hold more than one role.** A postgraduate student who also
  sublets a room is both a student and a landlord. One string cannot say so.
- **Roles are scoped, and strings are not.** "Landlord" is not a global fact
  about a person; it is a fact about their relationship to particular
  properties.

## Decision

**`User` holds identity and authentication only.** Capability lives in separate
models that describe relationships:

```
User
├── email (USERNAME_FIELD, unique), password
├── first_name, last_name, phone_number (+254 format)
├── is_active, is_staff, is_superuser      ← Django/platform staff ONLY
├── date_joined, last_login
└── NO user_type

LandlordProfile        (1:1 → User)
    A user who may own properties. Carries verification state, business
    details and payout information.

CaretakerAssignment    (FK → User, FK → Property, FK → granted_by User)
    A user authorised to manage ONE property, granted by that property's
    landlord. Carries a permission scope and may be revoked.

StudentProfile         (1:1 → User, FK → University)
    A user who belongs to an institution. Verification is optional and its
    mechanism is per-university policy (see below).

UniversityStaffProfile (1:1 → User, FK → University)
    A member of university staff, scoped to one institution. Its only
    capability today is that tenant's student verification queue.
```

Field lists and constraints are in `docs/DOMAIN_MODEL.md`.

**DRF permission classes check relationships, not string equality:**

```python
class IsPropertyManager(BasePermission):
    """The owning landlord, or a caretaker currently assigned to it."""

    def has_object_permission(self, request, view, obj):
        property_ = obj if isinstance(obj, Property) else obj.property
        if property_.landlord.user_id == request.user.id:
            return True
        return CaretakerAssignment.objects.filter(
            property=property_, user=request.user, is_active=True
        ).exists()
```

`is_staff` is the **only** flag meaning platform administrator, and it is
settable only through the Django admin or a management command — never through
the API.

### Caretaker capabilities

Design review noted that "manage" was underspecified, and that the sharpest
question was whether a caretaker may confirm a tenancy — because under the
original ADR-004 that would let one actor manufacture reviewers unilaterally.

**Resolved.** A caretaker **may**:

- create and edit units
- set vacancy counts
- upload and manage photos
- set availability
- confirm or dispute tenancy claims
- respond to inquiries

A caretaker **may not**:

- delete a property
- transfer ownership
- create or revoke caretaker assignments
- edit the landlord profile or any billing or payout field
- post a `ReviewResponse`

The tenancy concern is resolved by the ADR-004 amendment rather than by
withholding the capability: **the tenant now initiates the claim**, so
confirmation is an acknowledgement of someone else's assertion, not the
creation of a record out of nothing. Caretaker confirmation power therefore no
longer lets anyone manufacture a reviewer on their own.

Every caretaker action records the acting user. Confirmations additionally
store which actor confirmed, via `Tenancy.confirmation_source` and the
confirming user (ADR-004), so a pattern of one caretaker confirming implausible
volumes is visible in the data.

The capability list lives in `CaretakerAssignment.permissions`, so a landlord
can grant a subset. The list above is the maximum an assignment may contain;
values outside it are rejected at the model layer, not merely unused.

### Student verification is per-university policy

Design review flagged that email-domain verification is weaker than it sounds:
Kenyan universities do not uniformly issue student addresses, alumni keep theirs
for years, and staff often share the student domain. A single hard-coded
mechanism would exclude real students at exactly the institutions we most want
to sell to.

**Resolved: verification is a policy each school chooses at onboarding, and it
is changeable without a deploy. Verification is never required to use the
platform by default — it earns a badge.**

```
University
├── verification_methods_enabled   array ⊆ {email_domain, student_id_upload},
│                                  may be empty
├── student_email_domains          array, e.g. ['s.kyu.ac.ke']
├── verification_required_to_review  bool, default False
├── verification_required_to_signup  bool, default False
└── id_review_retention_days       int, default 7

StudentProfile
├── verification_status   unverified | pending | verified | rejected
├── verification_method   email_domain | manual_id | null
├── verified_at
├── verified_by           nullable FK → User
└── rejection_reason

StudentVerificationRequest        the manual upload flow
├── student_profile, document reference
├── submitted_at
├── reviewer, decision, decided_at
└── notes
```

**Email-domain verification.** The user submits a student address matching one
of the university's domains and receives a signed single-use token by email. On
confirmation the status goes straight to `verified`. No human in the loop.

**Manual ID verification**, for schools that issue no student addresses. The
student uploads a document; a university staff reviewer approves or rejects.

This requires a role the model did not have. **Add `UniversityStaffProfile`,**
scoped to exactly one university, whose only capability for now is the
verification queue for their own tenant. Platform admins (`is_staff`) may also
review.

Reviews display a verified badge when the author's profile is verified. When
`verification_required_to_review` is `False`, unverified students may still
post; the badge is simply absent.

### ID document handling

**This is a legal requirement under Kenya's Data Protection Act 2019, not a
preference.** A national ID or student card is personal data, and the Act
obliges a data controller to collect no more than is necessary, to retain it no
longer than necessary for the stated purpose, and to secure it. The rules
below are the minimum that satisfies that:

- **Private bucket only.** Never the public CDN, never a predictable URL.
  Documents live in a bucket separate from listing photos, with its own
  storage backend class (ADR-007). Access is by short-lived signed URL,
  generated per reviewer request.
- **Deletion is scheduled, not manual.** A job deletes the document
  `id_review_retention_days` after a decision is recorded. The decision outcome
  is retained; the image is not. Retention defaults to 7 days.
- **Every read is logged** with reviewer, timestamp and the request that
  produced it. An access log is what makes the retention promise auditable.
- **The upload endpoint is rate-limited** and accepts images and PDF only, with
  a size cap and content-type validation performed on the **actual bytes**, not
  on the client-supplied header. A `Content-Type: image/png` on a payload that
  is not a PNG is a rejection, not a stored file.

## Consequences

### What this buys us

- **The privilege-escalation path closes.** There is no field a registration
  payload can set that grants any authority. Landlord status requires a
  `LandlordProfile`; caretaker authority requires an assignment created by a
  landlord who already owns the property.
- **Caretakers become expressible**, scoped to exactly the properties they were
  granted, revocable by the landlord who granted them.
- **Dual roles work.** One `User` may have a `StudentProfile` and a
  `LandlordProfile`. No modelling contortion.
- **Permissions become testable in the way that matters.** "Can caretaker X
  edit property Y?" is a query with a definite answer, so the negative cases
  (wrong property, revoked assignment, different landlord) are straightforward
  to assert.
- **The audit trail is free.** `CaretakerAssignment` records who granted what,
  when, and whether it is still live.

### What it costs us

- **More queries per request.** `user.user_type == 'landlord'` was free;
  checking a `CaretakerAssignment` is a database hit on every object-level
  check. Mitigate by `select_related('landlord')` on the property queryset and
  by caching a request-scoped set of the caller's assignment property ids —
  measure before optimising further.
- **More models, more migrations, more surface.** Four tables where there was
  one column. This is the cost of modelling the domain rather than approximating
  it.
- **The frontend must change.** `Navbar` and `ProtectedRoute` currently branch
  on `user.role` — a field that has never existed, so those branches have never
  fired (`docs/AUDIT.md` §5). The `/auth/me/` response should return an explicit
  capability set (`{"is_student": true, "is_landlord": false, "manages_properties": [3, 7], "is_staff": false}`)
  so the client never re-derives authorization from raw model shapes.
- **Data migration is required.** No production data exists today, so existing
  `user_type` values are discarded rather than migrated. This is only true
  *now*; the window closes at first deploy.

### Consequences of the verification resolution

- **Verification is now optional by default, so the badge is the product, not a
  gate.** That is the right trade — a system that cannot verify a real student
  is worse than one that occasionally verifies a former one — but it means the
  badge's meaning must be explained in the UI, or students will read its absence
  as a warning rather than as "this school does not run verification".
- **Two verification paths means two code paths and two test matrices**, and
  `verification_method` must be recorded so a later audit can tell them apart.
- **Manual review needs staffing**, and a queue nobody works is worse than no
  queue at all: students sit in `pending` indefinitely. Surface queue age to
  university staff, and let a school disable `student_id_upload` rather than
  advertise a review it will not perform.
- **`UniversityStaffProfile` is a third role model**, and it widens the blast
  radius of a compromised account: that user can read ID documents for their
  tenant. Scope it hard, log every read, and keep its capability list to the
  verification queue until there is a concrete reason to widen it.
- **Retention deletion must be verified, not assumed.** A job that silently
  stops running turns a 7-day promise into indefinite storage. Alert on the
  count of documents older than the retention window rather than on the job's
  own success.
- **`verification_required_to_signup` is a foot-gun** for a school that enables
  it without issuing addresses to first-years in time. Warn in the admin when it
  is set while `verification_methods_enabled` is empty; that combination locks
  everyone out.

### Consequences of the caretaker resolution

- **More queries per request.** `user.user_type == 'landlord'` was free; checking
  an assignment and its permission list is a database hit per object-level
  check. Cache a request-scoped map of the caller's assignments; measure before
  optimising further.
- **The permission list is data, so it can drift from the code that reads it.**
  Define the allowed values in one enum, validate on write, and assert in a test
  that no permission string is checked in code without existing in the enum.
- **Revocation must be immediate.** Assignments are deactivated, not deleted, so
  the check must filter on `is_active` every time — a cached permission map that
  outlives a revocation is a real hole.

## Alternatives considered

### Keep `user_type`, add validation — rejected

Making the field server-controlled would close the registration hole but leaves
every other problem: no caretaker scoping, no dual roles, and the two
incompatible meanings of "admin" still in the codebase. It treats the symptom.

### Django's built-in `Group` and `Permission` — rejected as the primary
mechanism

Groups are global: "Landlords" as a group says nothing about *which* properties.
Django's object-level permission hooks exist but the default backend does not
implement them, so we would end up writing the relationship checks anyway,
with an extra layer of indirection. Groups remain useful for **platform staff**
roles (moderator, support) where global scope is correct, and we use them
there.

### django-guardian (per-object permissions) — rejected

Provides exactly the generic per-object model we need, backed by a generic
foreign key. Rejected because our object-permission rules are few, specific,
and expressible as ordinary queries against models we already need for other
reasons. `CaretakerAssignment` has to exist regardless — it carries
`granted_by`, `granted_at` and revocation, which guardian would not. Adding
guardian on top would mean two sources of truth.

### Attribute-based access control via a policy engine (Casbin, OPA) — rejected

Correct for a system with many rules changing independently of code. We have
roughly six rules, all stable. The operational cost of a policy engine is not
repaid at this size.
