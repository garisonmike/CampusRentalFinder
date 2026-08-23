# ADR-004: Review integrity via a `Tenancy` record

**Status:** Accepted
**Date:** 2026-08-23
**Amended:** 2026-08-24 — claim-with-timeout adopted; minimum-stay enforcement settled
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
TenancyClaim
├── unit                    FK → Unit (PROTECT)
├── claimant                FK → User (PROTECT) — the tenant
├── start_date, end_date
├── status                  pending | confirmed | disputed | withdrawn | expired
├── confirmation_deadline   claimed_at + TENANCY_CONFIRMATION_WINDOW_DAYS
├── resolved_by             FK → User, nullable
├── resolved_at, dispute_reason
└── created_at, updated_at

Tenancy
├── unit                 FK → Unit (PROTECT)
├── tenant               FK → User (PROTECT)
├── claim                FK → TenancyClaim (SET_NULL) — where it came from
├── confirmation_source  landlord | caretaker | auto | admin
├── confirmed_by         FK → User, NULL when confirmation_source is 'auto'
├── confirmed_at
├── start_date           DateField
├── end_date             DateField, nullable while ongoing
├── monthly_rent_kes     the agreed rent, for the record
├── status               active | ended | disputed
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
| Tenancy dates are ordered | `CheckConstraint` — schema |
| No overlapping confirmed tenancy per unit | `ExclusionConstraint` — schema |
| One open claim per user per unit | partial `UniqueConstraint` — schema |
| Minimum stay before reviewing | one service function, threshold in settings — see below |
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
        ExclusionConstraint(
            name="no_overlapping_confirmed_tenancy",
            expressions=[
                ("unit", RangeOperators.EQUAL),
                (
                    DateRange("start_date", "end_date", RangeBoundary()),
                    RangeOperators.OVERLAPS,
                ),
            ],
            condition=Q(status="active"),
        ),
    ]
```

The exclusion constraint needs the `btree_gist` extension, added by a migration
with `django.contrib.postgres.operations.BtreeGistExtension`.

and on `Review`:

```python
models.CheckConstraint(
    condition=Q(rating__gte=1) & Q(rating__lte=5), name="review_rating_range"
)
```

### Tenancy is claimed by the tenant and confirmed on a timeout

The first draft of this decision had the landlord create the tenancy. Design
review rejected that: it gives one party unilateral control over whether a
review can exist. A landlord who notices that confirming move-ins produces
one-star reviews simply stops confirming, and the properties in the worst
condition end up with the cleanest profiles — an inversion of the exact signal
students need. The mechanism was sound against *fake* reviews and weak against
*missing* ones.

**Resolved: claim-with-timeout, adopted in full.**

1. **A tenant creates a `TenancyClaim`** against a specific unit, with claimed
   `start_date` and `end_date`.
2. **The landlord and every assigned caretaker are notified.** They have
   `settings.TENANCY_CONFIRMATION_WINDOW_DAYS` (7) to confirm or dispute.
3. **Silence auto-confirms**, via a scheduled job on the ADR-007 queue. This is
   the whole point: landlord silence becomes a signal, not a veto.
4. **A dispute freezes the claim** and opens a moderation entry for platform
   admins. A disputed claim yields no review until it is resolved.
5. **`Tenancy` records `confirmation_source` ∈ {landlord, caretaker, auto,
   admin}** and the confirming actor. This is retained from the original design
   — it costs one column and it is a genuinely useful trust signal later, both
   for surfacing "this landlord confirms 12% of claims" and for spotting a
   caretaker confirming implausible volumes.

Because the tenant now initiates, caretaker confirmation power no longer lets
any single actor manufacture a reviewer (see ADR-003).

**Abuse controls**, since the initiating party has changed:

- **One open claim per user per unit**, as a partial unique constraint.
- **A per-user rate limit on claim creation.**
- **Claims whose date range overlaps an existing confirmed tenancy for the same
  unit are rejected at the database level**, using a PostgreSQL
  `ExclusionConstraint` over `(unit, daterange(start_date, end_date))` with the
  `btree_gist` extension. This is enforced in the schema rather than in a
  serializer because a serializer cannot see a concurrent insert.

### Minimum stay is a service function, not a constraint

Design review was right that a 30-day minimum stay cannot be a
`CheckConstraint`: Postgres cannot reference "today", so an *ongoing* tenancy's
eligibility is not expressible there.

**Resolved:**

- The threshold lives in `settings.REVIEW_MINIMUM_STAY_DAYS`, so changing policy
  is not a migration.
- It is enforced in **one** well-named service function —
  `assert_tenancy_is_reviewable(tenancy)` — that every path goes through: the
  serializer, the admin, and any future endpoint. Tested directly, at the
  boundary, rather than only through the API.
- **Everything that can be a database constraint still must be.** One review per
  tenancy (`OneToOneField` → `UNIQUE`), date ordering
  (`end_date >= start_date`), the rating range, and the overlap exclusion above
  are all schema-level. The service function covers only the one rule the
  database genuinely cannot express.

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
- **Landlord friction remains, even though the veto is gone.** A landlord who
  ignores every claim still adds seven days of latency to every review. The
  timeout removes the incentive problem, not the delay.
- **Extra friction in the happy path.** Somebody must remember to confirm the
  move-in. Every un-confirmed tenancy is a review that will never be written.
  Confirmation must be one tap from the enquiry thread, not a form buried in a
  dashboard, and it should be prompted automatically.
- **`PROTECT` on the tenancy FK means reviews block deletion** of units and
  users. Correct — a deleted unit must not silently take its review history with
  it — but it means account deletion needs an anonymisation path rather than a
  cascade, which has GDPR-shaped implications if the platform ever operates
  under one.
- **Disputes need a workflow, not just a status.** See the resolution
  consequences below; this is now on the critical path rather than a loose end.

### Consequences of the claim-with-timeout resolution

- **The suppression incentive is gone**, which was the point. Landlord silence
  now produces a confirmed tenancy after seven days rather than a permanent
  veto, so the only way to stop a review is an active dispute — which is
  recorded, visible to moderators, and countable.
- **The abuse surface moves to the tenant.** Someone can now claim a tenancy
  they never had. The three controls above are what stand between that and a
  fake review, and none is optional: the exclusion constraint in particular is
  the only thing that stops two people claiming the same unit for the same
  dates. Treat a regression in any of them as a security bug.
- **Auto-confirmation depends on a scheduled job actually running.** If the
  worker stops, claims sit in `pending` and no review is ever written — a silent
  failure that looks like a quiet week. Alert on the count of claims past their
  `confirmation_deadline` that are still pending, not on the job's own success.
- **Disputes now need a moderation workflow**, and platform admins are the
  bottleneck. A dispute queue nobody works blocks the reviews it touches
  indefinitely. Surface queue age, and set an expectation for resolution time
  before the feature ships.
- **`confirmation_source` will skew towards `auto`** early on, while landlords
  are unfamiliar with the flow. That is expected and not itself a signal; only
  a *per-landlord* pattern is. Do not surface the raw distribution to students
  until there is enough volume for it to mean something.
- **Two records where there was one.** `TenancyClaim` and `Tenancy` both exist,
  and the relationship between them must stay clear: the claim is the request,
  the tenancy is the fact. Do not let code start reading claims where it means
  tenancies.

### Consequences of the minimum-stay resolution

- **One rule lives outside the database**, and that is a deliberate, documented
  exception rather than a pattern to copy. Every other invariant here is a
  constraint.
- **`assert_tenancy_is_reviewable` is a chokepoint that must not be bypassed.**
  Assert in a test that the review serializer calls it; a future endpoint that
  forgets is exactly how this rule erodes.
- Changing `REVIEW_MINIMUM_STAY_DAYS` retroactively changes who may review.
  That is usually wanted, but it means the setting is policy, not configuration
  — change it deliberately.

### Still open, and not blocking

- **One review per tenancy, but tenancies renew.** A student who stays two
  academic years under two tenancy records gets two reviews of the same unit,
  which reads as inflated volume. Either treat a renewal as one continuing
  tenancy, or de-duplicate by `(unit, tenant)` when computing the average. This
  can be decided when the aggregate rating is implemented; it does not affect
  the schema.

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
