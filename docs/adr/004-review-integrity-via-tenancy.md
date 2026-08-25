# ADR-004: Review integrity via a `Tenancy` record

**Status:** Accepted
**Date:** 2026-08-23
**Amended:** 2026-08-24 — claim-with-timeout adopted; minimum-stay enforcement settled
**Amended:** 2026-08-25 — dispute queue bounded: application path, typed disputes, symmetric timeout
**Amended:** 2026-08-26 — escalation reasons separated; correction-defeats-review closed; annotation derived
**Amended:** 2026-08-27 — annotation batched per page; transitions table-driven; rating aggregates; cold start
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
TenancyClaim            ONLY for stays the platform did not witness
├── unit                    FK → Unit (PROTECT)
├── claimant                FK → User (PROTECT) — the tenant
├── start_date, end_date
├── status                  pending | confirmed | disputed | escalated
│                           | withdrawn | expired
├── confirmation_deadline   claimed_at + TENANCY_CONFIRMATION_WINDOW_DAYS
├── dispute_reason          dates_incorrect | never_tenanted | duplicate
│                           AS RAISED. Never rewritten.
├── dispute_note            free text, ADDITIONAL to the reason, never instead
├── disputed_by             FK → User
├── disputed_at
├── dispute_withdrawn_at    the disputer took it back; clears the annotation
├── proposed_start_date     set by the disputer for dates_incorrect
├── proposed_end_date
├── counter_start_date      set once by the tenant, if they disagree
├── counter_end_date
├── tenant_accepted_correction_at
│                           evidence, NOT necessarily a resolution — see below
├── escalated_at            when it entered the admin queue
├── escalation_reason       counter_unresolved | correction_defeats_review
│                           | identity_disputed | duplicate_unmatched
├── escalation_deadline     escalated_at + DISPUTE_RESOLUTION_WINDOW_DAYS
├── resolved_by             FK → User, nullable
├── resolved_at
└── created_at, updated_at

Tenancy
├── unit                 FK → Unit (PROTECT)
├── tenant               FK → User (PROTECT)
├── application          FK → Application (SET_NULL) — the witnessed path
├── claim                FK → TenancyClaim (PROTECT) — the claimed path.
│                        PROTECT, not SET_NULL: the review annotation is
│                        derived from this record, so it must survive.
├── confirmation_source  application | landlord | caretaker | auto
│                        | admin | dispute_timeout
├── confirmed_by         FK → User, NULL for 'auto' and 'dispute_timeout'
├── confirmed_at
├── was_disputed         BooleanField — the dispute happened, whatever the outcome
├── start_date           DateField
├── end_date             DateField, nullable while ongoing
├── monthly_rent_kes     the agreed rent, for the record
├── status               active | ended
└── created_at, updated_at

Review
├── tenancy               OneToOneField → Tenancy (PROTECT)   ← REQUIRED
├── rating 1..5 + category ratings
├── comment
├── editable_until        DateTimeField — set on creation
├── is_published
└── created_at, updated_at
   NOTE: no disputed_by_landlord column. The annotation is DERIVED at read
   time by review_dispute_annotation(review); see below.
```

The five rules, and where each is enforced:

| Rule | Enforcement |
|---|---|
| A review must reference a tenancy | `NOT NULL` FK — schema |
| One review per tenancy | `OneToOneField` → `UNIQUE` — schema |
| Tenancy dates are ordered | `CheckConstraint` — schema |
| No overlapping confirmed tenancy per unit | `ExclusionConstraint` — schema |
| One open claim per user per unit | partial `UniqueConstraint` — schema |
| A witnessed tenancy has an application and no claim | `CheckConstraint` — schema |
| A disputed claim carries an enumerated reason | `CheckConstraint` — schema |
| Corrected dates accompany a `dates_incorrect` dispute | `CheckConstraint` — schema |
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
   `start_date` and `end_date`. Only for stays the platform did not witness —
   an accepted `Application` produces a confirmed `Tenancy` directly, with no
   claim at all. See "Bounding the dispute queue" below.
2. **The landlord and every assigned caretaker are notified.** They have
   `settings.TENANCY_CONFIRMATION_WINDOW_DAYS` (7) to confirm or dispute.
3. **Silence auto-confirms**, via a scheduled job on the ADR-007 queue. This is
   the whole point: landlord silence becomes a signal, not a veto.
4. **A dispute freezes the claim.** Disputes are typed, and only some reach an
   admin; an escalated one that we do not resolve within 14 days auto-resolves
   in the tenant's favour. See "Bounding the dispute queue" below.
5. **`Tenancy` records `confirmation_source` ∈ {application, landlord,
   caretaker, auto, admin, dispute_timeout}** and the confirming actor. This is retained from the original design
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

### Bounding the dispute queue

Design review accepted the claim-with-timeout mechanism and then found its
successor problem: the timeout removed the landlord's veto by silence, but it
made *disputing* the only way to stop a review. Disputing is cheap, so it will
not be rare — and every disputed claim lands on platform admins, which is a
team of one. An unresolved dispute blocks its review indefinitely. The veto had
moved from silence to paperwork, and the paperwork was ours.

Three changes bound the queue. They are listed in order of how much volume each
removes, and the first is by far the largest.

#### 1. Most tenancies never become claims

**When an `Application` is accepted by a landlord or caretaker on-platform, a
`Tenancy` is created directly in confirmed state**, with
`confirmation_source = 'application'`. No claim, no confirmation window, no
dispute, no queue entry. The platform already witnessed the agreement: it holds
the application, the acceptance, the actor and the timestamp. Asking the
landlord to confirm a second time what they just accepted adds latency and a
dispute surface for nothing.

**`TenancyClaim` exists only for stays the platform did not witness:**

- off-platform arrangements — the student found the room through a friend, a
  noticeboard or a WhatsApp group, and only later wants to review it;
- pre-platform history — stays that predate the university's onboarding, which
  is how a new tenant gets any reviews at all in its first months.

This is the primary volume control. **A future reader must not "simplify" this
by routing every tenancy through a claim for uniformity.** Doing so would
restore the unbounded queue that this amendment exists to prevent, and it would
do so silently, because the code would look tidier. If the two paths ever feel
like duplication, the duplication is the point: one path is evidence the
platform already holds, the other is an assertion it has to test.

#### 2. Disputes are typed, and most never reach an admin

A dispute requires an **enumerated reason**. Free text is captured in
`dispute_note` as additional context, never as a substitute — an untyped
dispute cannot be routed, so it can only go to a human.

| Reason | Meaning | Path |
|---|---|---|
| `dates_incorrect` | The stay happened; the dates are wrong. | Resolved between the parties |
| `never_tenanted` | Identity dispute: this person never lived here. | Admin |
| `duplicate` | Already covered by an existing tenancy. | Auto-resolved where possible |

**`dates_incorrect`** — the landlord or caretaker submits corrected dates in
`proposed_start_date` / `proposed_end_date`. The tenant either accepts, in which
case the claim confirms with the corrected dates and **no admin is involved**,
or counters **once** with `counter_start_date` / `counter_end_date`. A counter
that the disputer does not accept escalates with
`escalation_reason = 'counter_unresolved'`.

**`duplicate`** — auto-resolves if a confirmed tenancy already exists that
overlaps the claimed range for the same unit and the same user. That is a
database query, not a judgement call, and it is the same predicate the
`ExclusionConstraint` already enforces. If no such tenancy exists, the claim is
not in fact a duplicate and the dispute escalates.

**Only three things reach the admin queue:** a `never_tenanted` dispute, an
unresolved counter, and a `duplicate` that matched nothing — plus the
correction case in 2b below.

#### 2a. `escalation_reason` is separate from `dispute_reason`

The first version of this amendment had `dates_incorrect` escalate *as*
`never_tenanted`, on the reasoning that two parties who cannot agree when
someone lived somewhere are in substance disagreeing about whether they did.

Design review rejected that: it changes the dispute's meaning on the way to the
queue. The admin receives "this person never lived here" for a case where both
parties agree the stay happened and disagree by a fortnight. **An identity
question and a date question need completely different evidence**, and
collapsing them makes the queue harder to work — the opposite of what this
amendment is for.

**Resolved: two fields.**

- **`dispute_reason` keeps the value as originally raised, and is never
  rewritten.** It records what the disputer actually claimed.
- **`escalation_reason` is set when the dispute reaches the admin queue**, and
  says what the admin has to decide:

| `escalation_reason` | Arrived from | What the admin decides |
|---|---|---|
| `counter_unresolved` | `dates_incorrect`, tenant countered, disputer did not accept | Which set of dates is right |
| `correction_defeats_review` | `dates_incorrect` where the correction drops the stay under the review minimum | Whether the correction is honest — see 2b |
| `identity_disputed` | `never_tenanted`, raised as such | Whether this person lived here at all |
| `duplicate_unmatched` | `duplicate` with no confirmed overlapping tenancy | Whether an existing tenancy really covers this |

The admin queue is **sortable and filterable by `escalation_reason`**. Working a
mixed queue oldest-first is right; working it without knowing which kind of
question each item is means gathering the wrong evidence first.

#### 2b. A correction that defeats the review cannot settle between the parties

Design review identified the cheapest attack on this whole mechanism. It costs a
motivated landlord exactly one extra step:

> Dispute with `dates_incorrect`, and propose corrected dates that put the stay
> under `REVIEW_MINIMUM_STAY_DAYS`. If the tenant accepts — and a tenant who
> misremembers by a week, or who simply wants the argument over, may well
> accept — the claim confirms with the corrected dates and the review is
> silently impossible. No admin ever sees it. It does not read as suppression;
> it reads as a settled disagreement.

That is not an honest date disagreement, and the outcome is exactly the veto
this ADR exists to remove.

**Resolved: if a proposed correction would make the stay shorter than
`settings.REVIEW_MINIMUM_STAY_DAYS`, it cannot auto-resolve between the parties
— not even with the tenant's acceptance.** It escalates immediately with
`escalation_reason = 'correction_defeats_review'`, and an admin decides.

**Tenant acceptance is recorded as evidence, not as a resolution.**
`tenant_accepted_correction_at` is set and shown to the admin, who will usually
find the correction honest and confirm it. The landlord is not presumed to be
lying; the point is that this particular correction has a side effect the
parties cannot settle privately, because one of them may not realise it has one.

This is a **computable predicate**, so it is a guard inside the correction
service function, not an instruction in a reviewer runbook:

```python
def apply_date_correction(claim, *, start_date, end_date, accepted_by_tenant):
    if stay_days(start_date, end_date) < settings.REVIEW_MINIMUM_STAY_DAYS:
        return escalate(claim, reason=EscalationReason.CORRECTION_DEFEATS_REVIEW)
    ...
```

**The cheapest attack on the trust property should also be the most conspicuous
one.** Before this rule, suppressing a review cost one dispute and one
plausible-looking correction, and left no trace anywhere a human would look.
After it, the same move puts the claim in front of an administrator under a
label naming precisely what is being attempted.

#### 2c. Transitions live in one table, not in an implied mapping

Design review found that `dispute_reason` and `escalation_reason` are two enums
with a mapping between them that exists only in whichever function happens to
branch on it. A new `dispute_reason` with no escalation path would be a dispute
that can be **raised and never routed** — it would sit in `disputed` for ever,
which is precisely the indefinite block the timeout was introduced to remove.

A test would catch the drift, but only after someone had written the
unreachable state. **Resolved: make the invalid state unconstructable.**

A single module-level table maps every `dispute_reason` to its permitted
`escalation_reason` values and its resolution paths:

```python
DISPUTE_TRANSITIONS = {
    DisputeReason.DATES_INCORRECT: DisputeTransition(
        escalates_to=(EscalationReason.COUNTER_UNRESOLVED,
                      EscalationReason.CORRECTION_DEFEATS_REVIEW),
        can_resolve_between_parties=True,
    ),
    DisputeReason.NEVER_TENANTED: DisputeTransition(
        escalates_to=(EscalationReason.IDENTITY_DISPUTED,),
        can_resolve_between_parties=False,
    ),
    DisputeReason.DUPLICATE: DisputeTransition(
        escalates_to=(EscalationReason.DUPLICATE_UNMATCHED,),
        can_resolve_between_parties=False,
        auto_resolves=True,
    ),
}
```

- **The state machine reads this table. Nothing else encodes a transition.**
- **Raising a dispute with a reason absent from the table raises at
  construction**, so an unroutable dispute cannot be created at all.
- Two tests, in both directions: every `dispute_reason` has at least one
  escalation path, and every `escalation_reason` is reachable from at least one
  `dispute_reason`. A reason nobody can reach is dead code in a state machine,
  which is its own kind of drift.
#### 3. The timeout is symmetric

**An escalated dispute that is unresolved after
`settings.DISPUTE_RESOLUTION_WINDOW_DAYS` (14) auto-resolves in the tenant's
favour.** The claim confirms, `confirmation_source` is `dispute_timeout`, and
the review becomes possible.

The review is shown with a **neutral annotation** — "the landlord disputed this
stay". **Never as a discredit**: not greyed out, not collapsed, not excluded
from the average, not labelled "unverified". The reader is told a fact and left
to weigh it. A landlord who disputes honestly and a landlord who disputes
tactically produce the same annotation, which is precisely why it must not read
as a verdict.

The rationale belongs in the record, because this is the clause most likely to
be softened by someone trying to be fair to landlords:

> An indefinite block turns platform backlog into a landlord veto by proxy. If
> a dispute stops a review until we get to it, then our capacity — not the
> merits — decides which reviews exist, and a landlord who disputes everything
> gets exactly the outcome the original silence-veto gave them. A deadline that
> binds the platform is the only version where the trust property survives our
> own capacity limits.

The deadline binds us, not the tenant. Missing it is our failure, and the
default on our failure must favour the party with less power.

#### 3a. The annotation is derived, not stored

The first version of this amendment put a `disputed_by_landlord` boolean on
`Review`. Design review objected that it is permanent, with no path to removal
even when the dispute is later shown to be spurious.

The fix is **not** to tune the permanence. It is to stop freezing a display
policy in a column at all.

**Resolved: delete the field.** The annotation is derived at read time by one
named function:

```python
def review_dispute_annotation(review) -> DisputeAnnotation | None:
    """The neutral annotation to show beside a review, or None."""
```

It returns `None` when:

- there was no dispute;
- **the disputer withdrew it** (`dispute_withdrawn_at` is set) — a withdrawn
  dispute is not a fact about the stay, it is a retracted assertion; or
- **as a policy hook, off by default and settings-gated:** the landlord's
  `dispute_upheld_rate` is below `settings.DISPUTE_ANNOTATION_MIN_UPHELD_RATE`
  over at least `settings.DISPUTE_ANNOTATION_MIN_SAMPLE` disputes. A landlord
  who disputes constantly and is upheld almost never is producing noise, not
  signal, and the annotation should stop repeating it.

Both thresholds default to values that disable the hook, because the sample
sizes needed to make the ratio meaningful do not exist yet.

**Store facts, derive presentation.** Changing this policy later is a function
edit and a settings change, not a data migration rewriting a boolean on every
live review — and not a decision that silently persists on reviews written under
the old policy.

**The annotation is computed per page, not per review.** Design review pointed
out that deriving it makes the review list an N+1 waiting to happen, and that
enabling the policy hook would then look like it *caused* a performance
regression when the real cause is the access pattern. The interface therefore
forbids the per-review shape outright:

```python
def review_dispute_annotations(reviews) -> dict[int, DisputeAnnotation]:
    ...  # annotations for a COLLECTION, keyed by review id
```

- It takes a **collection** and returns a **mapping**. There is no public
  single-review entry point that touches the database; a caller needing one
  review passes a one-element collection.
- Landlord counters for the policy hook are fetched in **one query keyed by
  landlord id**, covering every landlord on the page.
- **A query-count test asserts that rendering 1 review and rendering 50 issue
  the same number of queries, with the hook both disabled and enabled.** That
  test is what stops the hook being switched on in production and being blamed
  for the result.

**Everything the annotation needs is reconstructable from the dispute record**,
which is why two things changed alongside:

| Needed | Source |
|---|---|
| Was there a dispute? | `claim.dispute_reason`, `claim.disputed_at` |
| Was it withdrawn? | `claim.dispute_withdrawn_at` — **added for this** |
| Who raised it? | `claim.disputed_by` |
| How did it end? | `claim.escalation_reason`, `claim.resolved_at`, `Tenancy.confirmation_source` |
| Is this landlord credible? | `LandlordProfile.disputes_raised_count` / `disputes_upheld_count` |

`Tenancy.claim` therefore becomes **`PROTECT`, not `SET_NULL`**. A deleted claim
would take the annotation's evidence with it and leave the review silently
un-annotated — the same "quietly clears the flag" outcome the derived approach
exists to avoid.

`Tenancy.was_disputed` stays. It is a fact about the tenancy, not a display
decision, and it lets the common "did anything happen here at all?" query skip
the join.

#### 4. Disputing is visible and costly

Two per-landlord rates, computed from `TenancyClaim` and recorded on
`LandlordProfile` as denormalised counters refreshed by the same job that
processes deadlines:

| Metric | Definition |
|---|---|
| `dispute_rate` | disputes raised ÷ claims received |
| `dispute_upheld_rate` | disputes resolved in the landlord's favour ÷ disputes raised |

**Admin-visible from the start**, so a pattern is legible before it becomes a
problem. **A public trust signal later** — a landlord who disputes 80% of claims
and is upheld in 5% of them is telling students something useful, but that
should not ship until there is enough volume for the ratio to mean anything. A
denominator of three makes any percentage misleading.

A **per-landlord rate limit** caps disputes raised per rolling 30 days,
configurable as `settings.MAX_DISPUTES_PER_LANDLORD_PER_MONTH`. Hitting the cap
does not silently drop a dispute: it refuses it with an explanation and a route
to contact support, so a landlord with a genuine flood of fraudulent claims has
somewhere to go.

### Ratings are aggregate tables, not a denormalised foreign key

Uniqueness stays at the tenancy: one stay, one review. A `(unit, tenant)` or
`(property, tenant)` constraint would block a legitimate second review — a
student who moves from a bedsitter to a one-bedroom in the same block has two
genuinely different experiences to describe — and there is no honest way to
choose which of the two to keep.

That leaves a fairness problem in the **aggregate**: a student who moved twice
contributes two ratings to the block's score, so one person is weighted 2×.

**Resolved: de-duplicate in the aggregate, not the schema.**

- The property rating averages **one contribution per `(property, tenant)`**,
  taking the mean of that tenant's reviews for that property.
- **Unit-level ratings show on the unit, property-level on the property.**
- The property figure is labelled **"from N students"**, never "N reviews", so
  the denominator means what a reader assumes it means.

#### Why aggregate tables and not a denormalised `Review.property_id`

A denormalised FK makes the grouping cheaper, and it was the obvious
suggestion. It was declined, and the reasoning is worth keeping because both
options are denormalisation and the difference is entirely in how they fail:

| | Denormalised `property_id` | Aggregate tables |
|---|---|---|
| Still dedupes and averages per page load | **Yes** | No — precomputed |
| Failure mode when it drifts | A duplicated FK disagreeing with `tenancy.unit.property`: **silent corruption**, reviews attributed to the wrong building | A stale cached number, **found by the reconciler** |
| Rebuildable | Not meaningfully — you cannot tell which value is right | Fully, from `Review` alone |

**A cache with a reconciler fails loudly. A duplicated foreign key fails
silently.** On a platform whose product is trustworthy ratings, that difference
decides it.

Three tables, one row each per property, unit and landlord, carrying
`average_rating`, `student_count`, `review_count`, a 1–5 `rating_distribution`,
`last_review_at` and `computed_at`. `student_count` and `review_count` are
**separate columns and are expected to differ** — that divergence *is* the
de-duplication, and a test asserts it for a tenant with two reviews on one
property.

Rules:

- **Recomputed by a job** on review create, edit, and moderation state change.
  Never inline in a request.
- **Fully rebuildable from source.** A management command recomputes any or all
  aggregates from `Review`, and it is *the same code path the job uses*. One
  implementation, two entry points — otherwise the rebuild and the incremental
  update drift and only one of them is right.
- **A scheduled reconciler recomputes a rolling sample, compares, and alerts on
  drift rather than silently correcting.** Self-healing hides the bug that
  caused the drift, and the bug is the thing worth knowing about.

### The cold start is solved with the same machinery, not a lower bar

A tenancy-anchored review model starts empty, which is its main cost. Three
decisions, and the third is a product constraint rather than an engineering one:

**(a) Launch seeding runs through `TenancyClaim`.** At onboarding, students may
claim past off-platform stays. **Same claim machinery, same confirmation window,
same dispute typing — no separate code path and no lower evidentiary bar.** A
review from a seeded claim is exactly as verified as any other, because the
mechanism is identical. `TenancyClaim.is_retrospective` records that the stay
predates the property's presence on the platform, **for analytics and for the
operations queue only — never for display and never for weighting.** A flag that
reaches the UI becomes a second class of review, which is the lower bar arriving
by the back door.

**(b) `LandlordRatingAggregate` is the secondary signal.** A property with no
reviews can show its landlord's record across their other properties,
**explicitly labelled as being about the landlord**, never about this property.

**(c) The empty state is honest.** No review means **"no verified reviews
yet"**. Never a neutral score, never a placeholder star count, never an average
over zero rows. This is written here as a constraint so a later UI pass cannot
quietly invent a default rating: **on a trust platform a fabricated signal is
worse than no signal**, because it is indistinguishable from a real one and it
is the platform itself doing the fabricating.
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
- **Disputes need a moderation workflow**, and platform admins are the
  bottleneck. This is what the 2026-08-25 amendment addresses; see its
  consequences below and `docs/OPERATIONS.md` for the alerting and the SLA.
- **`confirmation_source` will skew towards `application`** once the on-platform
  path is the normal route, and towards `auto` among the claims that remain
  while landlords are unfamiliar with the flow. Neither is itself a signal; only
  a *per-landlord* pattern is. Do not surface any raw distribution to students
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

### Consequences of bounding the dispute queue

- **Two ways a `Tenancy` comes into existence**, and they must not converge.
  `application`-sourced tenancies skip the whole claim machinery; claim-sourced
  ones go through it. Every query over tenancies has to be correct for both, and
  a test should assert that the application path creates no `TenancyClaim` row.
- **The `dates_incorrect` flow is a small state machine** — propose, accept or
  counter, escalate — and state machines rot when a new state is added without
  revisiting the transitions. Keep the transitions in one service module with a
  table-driven test, not spread across serializer methods.
- **"Counter once" needs enforcing, not documenting.** A tenant who can counter
  repeatedly has a denial-of-service against the landlord's attention. The
  single counter is a nullable field pair that is written once; a second attempt
  is refused.
- **The annotation is now a read-time computation on a hot path.** Every review
  render needs the claim and, if the policy hook is enabled, the landlord's
  counters. `select_related("tenancy__claim__disputed_by__landlord_profile")` on
  every review queryset, and a query-count assertion on the review list, or this
  becomes an N+1 on the busiest read in the product.
- **A derived annotation can change retroactively**, which is the point and also
  a surprise: enabling the policy hook silently removes annotations from
  existing reviews. That is the intended behaviour — the alternative is a data
  migration over live reviews — but it should be announced rather than shipped
  quietly.
- **`escalation_reason` is a second enum that can drift from the first.** A new
  `dispute_reason` value with no corresponding escalation path is a dispute that
  can be raised and never routed. Assert the mapping in a test.
- **The 14-day platform deadline is a commitment we can fail.** It has to be
  monitored on the *oldest unresolved item*, not on queue volume: a queue of
  three items where the oldest is 13 days old is an emergency, and a queue of
  forty where the oldest is two days old is fine. `docs/OPERATIONS.md` states
  the thresholds.
- **The rate limit can misfire on a genuine victim.** A landlord facing a
  coordinated wave of false claims hits the cap and is then unable to defend
  themselves. The cap refuses with a route to support rather than silently
  dropping, and support can raise it per account. Watch for the first real
  instance.
- **`dispute_rate` needs a denominator guard before it is shown to anyone**,
  including admins. Three claims and one dispute is not "33% dispute rate" in
  any useful sense.

### Consequences of the aggregate resolution

- **The aggregates can be stale**, by design. Between a review landing and the
  job running, a property shows the previous number. Acceptable — the alternative
  is computing it inline on the busiest read in the product — but it means
  `computed_at` has to be visible to anyone debugging a "wrong rating" report.
- **The reconciler is only as good as its sample.** A rolling sample finds
  systematic drift quickly and a single corrupted row slowly. That is the right
  trade for a scheduled job, but it means a specific complaint should be answered
  by recomputing that property, not by waiting for the sweep.
- **`student_count` will be smaller than `review_count`** wherever anyone has
  moved within a building, and the two numbers appearing side by side in an
  admin view will look like a bug to whoever sees it first. Label them.
- **A landlord aggregate is a reputation number attached to a person.** It is
  the right cold-start signal, and it is also the thing most likely to be
  disputed by the landlord it describes. Keep it rebuildable and keep
  `computed_at`, so "this is wrong" has a factual answer.

### Still open, and not blocking

- **Renewals still produce two reviews.** A student who stays two academic years
  under two tenancy records writes two reviews of the same unit. The aggregate
  now counts them as one student, which fixes the fairness problem, but the
  *unit* page still shows two entries from one person. Deciding whether a
  renewal is one continuing tenancy is a product question that does not affect
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
