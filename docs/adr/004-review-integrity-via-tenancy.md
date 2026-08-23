# ADR-004: Review integrity via a `Tenancy` record

**Status:** Accepted
**Date:** 2026-08-23
**Deciders:** Tech lead

## Context

Trustworthy reviews from former tenants are the platform's reason to exist. A
student can already see photos and a price on WhatsApp; what they cannot get is
an honest account from someone who lived there.

The draft protects that not at all. `docs/AUDIT.md` §2 documents the hole:
a `Review` requires only that its author's `user_type == 'tenant'`, a value the
client chooses at registration. `move_in_date` and `move_out_date` are optional
and unverified. Any account — including one created five minutes ago — can post
a five-star review of a property it has never seen, or a one-star review of a
competitor.

There is nothing in the schema tying a review to a stay, because there is no
representation of a stay.

## Decision

**A `Tenancy` record is the evidence that a stay happened, and a `Review`
cannot exist without one.**

```
Tenancy
├── unit              FK → Unit (PROTECT)
├── tenant            FK → User (PROTECT)
├── confirmed_by      FK → User — the landlord or assigned caretaker
├── start_date        DateField
├── end_date          DateField, nullable while ongoing
├── monthly_rent_kes  the agreed rent, for the record
├── status            active | ended | disputed
└── created_at, updated_at

Review
├── tenancy           OneToOneField → Tenancy (PROTECT)   ← REQUIRED
├── rating 1..5 + category ratings
├── comment
├── editable_until    DateTimeField — set on creation
├── is_published
└── created_at, updated_at
```

The five rules, and where each is enforced:

| Rule | Enforcement |
|---|---|
| A review must reference a tenancy | `NOT NULL` FK — schema |
| One review per tenancy | `OneToOneField` → `UNIQUE` — schema |
| Minimum stay before reviewing | `CheckConstraint` on the tenancy's duration — schema |
| Reviews freeze after an edit window | `editable_until` + permission class — schema stores it, view enforces it |
| A landlord may respond once | `ReviewResponse` with a `OneToOneField` to `Review` — schema |

**Enforcement is by database constraint, not only serializer validation.** A
serializer check is bypassed by the Django admin, a management command, a data
migration, a shell session, or any future code path that does not go through
that serializer. The trust property is the product; it belongs where nothing
can route around it.

Concretely:

```python
class Meta:
    constraints = [
        models.CheckConstraint(
            condition=Q(end_date__isnull=True) | Q(end_date__gte=F("start_date")),
            name="tenancy_end_after_start",
        ),
        models.UniqueConstraint(
            fields=["unit", "tenant", "start_date"],
            name="uniq_tenancy_per_unit_tenant_start",
        ),
    ]
```

and on `Review`:

```python
models.CheckConstraint(
    condition=Q(rating__gte=1) & Q(rating__lte=5), name="review_rating_range"
)
```

**A tenancy is created only by a landlord or an assigned caretaker confirming
that a tenant moved in through the platform.** It is never self-served by the
tenant.

**Minimum stay:** 30 days between `start_date` and the earlier of `end_date`
and today, before a review may be written.

**Edit window:** 14 days from creation, stored in `editable_until`. After that
the review is immutable — including to its author.

## Consequences

### What this buys us

- **A review implies a stay, and the implication is enforced by Postgres.** No
  code path — admin, shell, migration, future endpoint — can produce a review
  without one. This is the difference between a claim we make in marketing and
  a property of the system.
- The landlord who confirmed the tenancy is recorded, so a pattern of
  manufactured tenancies is visible in the data rather than inferred.
- `Tenancy` is independently useful: occupancy history, vacancy derivation, and
  the beginning of a rent-payment record if that is ever built.
- Frozen reviews mean a landlord cannot pressure a tenant into rewriting one
  months later — a real dynamic when the tenant may want a reference.

### What it costs us

- **Cold start.** On day one there are no tenancies, therefore no reviews,
  therefore the platform's headline feature is empty. This is the significant
  cost and it needs an explicit plan, not a hope. Options: seed with
  landlord-confirmed historical tenancies during onboarding; run a launch period
  where properties display "no reviews yet" honestly rather than hiding the
  section; recruit reviews from a handful of partner hostels first.
- **Landlords control the gate.** A landlord who never confirms a tenancy never
  receives a review. Since reviews carry reputational risk, the incentive runs
  the wrong way for exactly the landlords a student most needs warning about.
  See the flaw below — **this is the decision's central weakness.**
- **Extra friction in the happy path.** Somebody must remember to confirm the
  move-in. Every un-confirmed tenancy is a review that will never be written.
  Confirmation must be one tap from the enquiry thread, not a form buried in a
  dashboard, and it should be prompted automatically.
- **`PROTECT` on the tenancy FK means reviews block deletion** of units and
  users. Correct — a deleted unit must not silently take its review history with
  it — but it means account deletion needs an anonymisation path rather than a
  cascade, which has GDPR-shaped implications if the platform ever operates
  under one.
- **Disputes need somewhere to go.** `status='disputed'` exists; the workflow
  around it does not. Without one, a landlord who disputes a tenancy has no
  recourse except support email.

### Flaws worth stating plainly

**1. Landlord-controlled tenancy creation makes the review supply
self-censoring — and it undercuts the point.**

The mechanism is sound against *fake* reviews. It is weak against *missing*
ones, and missing reviews are how a bad landlord stays unrated. A landlord who
learns that confirming move-ins produces one-star reviews will simply stop
confirming, and the properties with the worst conditions end up with the
cleanest-looking profiles — the exact inversion of the signal students need.

I do not think this invalidates the decision; a tenancy record is still the
right anchor. But the decision as written gives one party unilateral control
over the evidence, and that needs a counterweight. Options, in rough order of
cost:

- **Tenant-initiated with landlord confirmation, and a timeout.** The tenant
  claims a tenancy; the landlord has 7 days to confirm or dispute; silence
  auto-confirms. This keeps a landlord's *active* dispute meaningful while
  removing the value of ignoring the request. It is the smallest change that
  fixes the incentive.
- **Publish the confirmation rate.** A property page showing "confirms 12% of
  reported tenancies" makes non-confirmation itself a visible signal.
- **Unverified reviews, clearly labelled and never counted in the average.**
  Preserves ADR-004's integrity property for the headline number while giving
  students somewhere to warn each other.

**Recommendation: adopt the timeout variant.** It is a modest change to the
schema (a `claimed_by` field, a `confirmation_deadline`, and a scheduled job)
and it converts landlord silence from a veto into a signal. **This needs a
decision before the schema rewrite starts** — retrofitting the claim/confirm
flow after tenancies exist is materially harder than building it in.

**2. A 30-day minimum stay is a policy number in a schema constraint.**
`CheckConstraint` cannot reference "today", so the duration check can only
constrain `end_date` against `start_date` — an *ongoing* tenancy's eligibility
still has to be evaluated in application code. Expect the rule to live in two
places, and test both. If the number changes later, a constraint change is a
migration; keep the *policy* threshold in settings and let the constraint
enforce only the invariant that cannot vary (`end_date >= start_date`).

**3. One review per tenancy, but tenancies can be renewed.** A student who
stays two academic years under two tenancy records gets two reviews of the same
unit, which reads as inflated volume. Either treat a renewal as one continuing
tenancy, or de-duplicate by `(unit, tenant)` when computing the average.

## Alternatives considered

### Serializer-only validation — rejected

What the draft does. Bypassed by the admin, the shell, management commands and
any future endpoint. For the platform's core trust property, "usually enforced"
is not enforced. Constraints in the database are the whole point of this ADR.

### Verified-badge model: anyone may review, verified stays get a badge — rejected as the primary mechanism

Lower friction and solves the cold start. Rejected because unverified reviews
still land in the average unless carefully excluded, and a review section where
most entries are unverified trains students to ignore the badge. Retained as a
possible *supplement* under a strict rule: unverified reviews are visually
distinct and never contribute to the numeric rating.

### Payment-based verification — rejected

Verify a stay by verifying rent paid through the platform (M-Pesa). Strongest
evidence available and the obvious long-term answer. Rejected for now because
it requires payment integration, a Safaricom Daraja account, PCI-adjacent
handling and a settlement flow — an order of magnitude more work than the whole
schema rewrite, and it gates reviews on a feature that does not exist. Revisit
once payments ship: it slots in as an additional
`Tenancy.verification_source` without disturbing this model.

### Time-limited edit window enforced by a database constraint — considered, partially rejected

`editable_until` is stored in the schema, but the freeze itself is enforced in
the permission layer because a `CheckConstraint` cannot compare a column to the
current time. A `BEFORE UPDATE` trigger could do it. Rejected for now: triggers
are invisible to Django's migration history and to anyone reading the models,
and the review path is narrow enough that a permission class covers it. Revisit
if a second write path to reviews ever appears.
