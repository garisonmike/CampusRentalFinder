# ADR-003: Authorization is object-level, not a `user_type` string

**Status:** Accepted
**Date:** 2026-08-23
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
    A user who belongs to an institution. Verification is by student email
    domain (see below).
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

**Student verification** is by email domain: `StudentProfile.is_verified`
becomes true when the user confirms an address whose domain matches one of the
`University.email_domains` entries (`students.ku.ac.ke`, `jkuat.ac.ke`, …).
Verification is a property of the confirmed address, not of a self-declaration.

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

### Flaws worth stating plainly

**1. Email-domain verification is weaker than it looks.** Three gaps:

- Kenyan universities do not uniformly issue student addresses, and where they
  do, some are shared or short-lived. Verification-by-domain will exclude
  legitimate students at exactly the institutions we most want to sell to.
- Alumni keep their addresses for years. A domain match proves an address was
  once issued, not that the holder is currently enrolled.
- Staff addresses often share the student domain, so a lecturer verifies as a
  student.

None of these is fatal — the mechanism raises the cost of a fake review
meaningfully, which is its real job. But it should not be described to a
university as proof of enrolment. Plan for a manual fallback (student ID upload
reviewed by staff) from the start, because a system that cannot verify a real
student is worse than one that occasionally verifies a former one. **Recommend
`StudentProfile.verification_method ∈ {email_domain, manual_id, none}` in the
schema** so the two paths are distinguishable later.

**2. The ADR does not say what a caretaker may actually do.** "Manage" is
underspecified: may a caretaker change the price? Delete the property? Confirm
a tenancy — which, under ADR-004, is what makes a review possible? That last
one matters: a caretaker who can create tenancies can manufacture reviewers.
The schema below reserves a `permissions` field for this, but the *policy*
needs deciding before implementation.

**Recommendation:** caretakers may update vacancy counts, upload photos, respond
to enquiries, and confirm tenancies; they may **not** change price, delete the
property, or grant further assignments. Confirming tenancies is included because
in practice the caretaker is the person on site who knows who moved in — but it
means caretaker assignment must itself be a trusted action, and revocation must
be immediate.

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
