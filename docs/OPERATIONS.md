# Operations

**Date:** 2026-08-25

What has to keep running for the platform's promises to hold, what we commit to,
and what it looks like when something stops.

This document exists because of a specific failure shape. Every job listed here
fails **silently**: nothing errors, no request 500s, no alert fires from an
exception tracker. The system simply stops doing something it promised, and the
symptom is an absence — reviews that never appear, documents that are never
deleted, claims that never confirm. **A job whose failure is invisible is worse
than no job**, because no job at least makes the gap obvious.

---

## Commitments

### Dispute resolution SLA

> An escalated tenancy dispute is resolved within **14 days**
> (`settings.DISPUTE_RESOLUTION_WINDOW_DAYS`).

This is a commitment, not a target. It is enforced by the system rather than by
diligence: an escalated dispute we have not resolved by the deadline
**auto-resolves in the tenant's favour** (ADR-004). The claim confirms, the
review becomes possible, and it is shown with a neutral annotation — derived at
read time from the dispute record by `review_dispute_annotation()`, not stored
on the review.

Missing the deadline is therefore not a backlog, it is a decision — and the
decision is made for us, in the direction that protects the party with less
power. That is deliberate: an indefinite block would turn our capacity limits
into a landlord veto by proxy.

**What this means in practice:** the queue cannot grow without consequence. If
we are missing deadlines, we are not "behind" — we are auto-confirming disputes
we never looked at. Treat sustained auto-resolution as a staffing signal, not a
tolerable steady state.

### Verification document retention

> A student ID document is deleted within `University.id_review_retention_days`
> (default **7**) of a decision being recorded.

This is a **legal obligation** under Kenya's Data Protection Act 2019, not a
housekeeping preference (ADR-003). The decision outcome is retained; the image
is not.

---

## Jobs that must be running

Six jobs on django-rq (ADR-002, ADR-004, ADR-007). For each: what it does, what
breaks when it stops, and how long that takes to become visible.

### 1. Claim auto-confirmation

| | |
|---|---|
| **Schedule** | Hourly |
| **Does** | Confirms `TenancyClaim` rows past `confirmation_deadline` that are still `pending` |
| **Guarantees** | ADR-004's core property: landlord silence is a signal, not a veto |

**Symptom if it stops:** claims sit in `pending` for ever. No error anywhere.
Tenants who submitted a claim never become able to review, and it looks
identical to landlords simply not confirming — which is exactly the behaviour
the timeout was introduced to defeat. **The failure restores the bug.**

**Time to visible:** 7 days plus however long anyone takes to notice a quiet
week in review volume. Realistically: never, without the alert.

### 2. Dispute auto-resolution

| | |
|---|---|
| **Schedule** | Hourly |
| **Does** | Resolves escalated disputes past `escalation_deadline` in the tenant's favour |
| **Guarantees** | The SLA above, and that platform backlog cannot block a review indefinitely |

**Symptom if it stops:** escalated disputes accumulate and the reviews behind
them stay impossible. The platform is silently vetoing reviews on behalf of
landlords who disputed them. This is the most damaging of the four failures,
because the thing being suppressed is precisely the content a landlord had an
incentive to suppress.

**Time to visible:** 14 days, then indefinitely.

### 3. Verification document retention deletion

| | |
|---|---|
| **Schedule** | Daily, 02:00 |
| **Does** | Deletes verification document images past **either** of two independent deadlines, verifies each deletion, and auto-rejects requests that expired unreviewed |
| **Guarantees** | The Data Protection Act commitment above |

**Two deadlines, and they are independent.**

| Setting | Default | Runs from | Catches |
|---|---|---|---|
| `VERIFICATION_DECISION_RETENTION_DAYS` | 7 | the decision | documents whose review is done |
| `VERIFICATION_ABSOLUTE_RETENTION_DAYS` | 30 | the upload | **documents nobody ever reviewed** |

The second is not a backstop for the first, it is the main one. An earlier
version of this spec had only the post-decision deadline, which meant a
document nobody reviewed lived for ever — and an unworked queue is the
likeliest real-world case, not an edge one. On absolute expiry the image is
deleted *and the request is auto-rejected with a reason naming expiry*, so the
student knows to resubmit rather than waiting on a queue that will never reach
them. No reviewer is recorded: nobody decided it, a clock did.

**Three distinct failures, three distinct symptoms.**

**(a) The job stops running.** National ID documents accumulate in object
storage indefinitely. Nothing in the product changes. Nothing errors. The first
signal is a subject access request, a breach, or an audit — all of which arrive
after the exposure, not before. *Time to visible: never, without the alert.*
**This is the one to wire up first.**

**(b) The job runs but deletion silently fails.** S3-compatible stores answer a
delete of an unremovable key with a 204 in several situations: a bucket policy
denying `DeleteObject`, an eventually-consistent replica, a versioned bucket
where deletion writes a marker and leaves the version readable. A job that
trusts the return value writes `deleted_at` over a file that is still there —
**a compliance record asserting something false, which is worse than no
record.** So every delete is followed by a re-read and `deleted_at` is written
only when the object is confirmed gone. A delete that cannot be confirmed
increments `delete_attempts`, records `last_delete_error`, logs
`retention_delete_unconfirmed` at ERROR, and is retried. *Alert on any row in
`unconfirmed_deletions()` — retrying does not fix a permissions problem.*

**(c) The queue is never worked.** Requests sit `pending` and students wait on
a review that never comes. The absolute deadline bounds this: at 30 days the
document goes and the student is told to resubmit. *Symptom before the fix was
added: indefinite silence, indistinguishable from the platform ignoring them.*

**Alert on the age of the oldest undeleted document past its deadline**, never
on volume and never on job success. A count tells you the queue is big, which
it may legitimately be. Age tells you whether something has been abandoned, and
**one document abandoned for six months is a worse breach than a thousand
deleted on time**. `oldest_overdue_document_age()` is the query.

**Testing note.** The verified-delete property cannot be tested against
`InMemoryStorage`: it is a dict whose `delete()` always works and whose
`exists()` always tells the truth, so those tests pass whether or not the
verification does anything at all. `tests/test_retention_minio.py` runs against
a real S3 API and CI provides MinIO as a service container. A test in that file
fails the build if MinIO is unreachable while `CI` is set, because a silently
skipped compliance test is indistinguishable from a passing one.

### 3a. Campus routing

| | |
|---|---|
| **Schedule** | On `PropertyCampusDistance` create; a sweep takes the oldest |
| **Does** | Fills `walking_distance_km`, `walking_minutes`, `routed_at` from the routing provider |
| **Guarantees** | That walking times exist at all — never that they are invented |

**Symptom if it stops:** every property shows a straight-line distance and an
em dash where the walking time should be. Listings still work; the number
students actually care about is simply missing, and it looks identical to a
provider that has no route for that pair.

The sweep orders by `routed_at` **nulls first**. PostgreSQL sorts NULLs last in
an ascending order, so the obvious ordering would re-route rows that already
have an answer and leave the never-routed ones for ever — a backlog that grows
while the job reports success.

**Time to visible:** never, without the alert.

### 3b. Rating aggregate reconciliation

| | |
|---|---|
| **Schedule** | Daily |
| **Does** | Recomputes a rolling sample of aggregates from `Review`, compares against stored values, **alerts on drift** |
| **Guarantees** | That the cached ratings still match their source |

**It never silently corrects.** Self-healing would hide the bug that caused the
drift, and the bug is the thing worth knowing about. A drift alert means
"recompute this one and then find out why", not "the system fixed itself".

**Symptom if it stops:** ratings drift from their source and nobody finds out.
Every page still renders a number; the number is simply wrong, and it is wrong
in the direction of whatever the incremental update got wrong — which on a
platform selling trustworthy ratings is the worst available failure.

**Time to visible:** never, without the alert. A specific "this rating is wrong"
complaint is answered by recomputing that property directly, not by waiting for
the sweep to sample it:

```
manage.py recompute_ratings --property <id>
```

The command and the job call the same functions in `reviews/recompute.py`. Do
not add a second averaging implementation anywhere: the rebuild and the
incremental update would drift, and only one of them would be right with no way
to tell which from the outside.

Drift is logged at ERROR as `rating_aggregate_drift`, one line per disagreeing
field, carrying both the stored and the computed value. Alert on any
occurrence.

### 3c. Inquiry expiry

| | |
|---|---|
| **Schedule** | Daily, 04:00 |
| **Does** | Marks inquiries `expired` once `INQUIRY_EXPIRY_DAYS` (14) passes with no reply |
| **Guarantees** | That "the landlord never replied" is visible to the student as a fact |

**Symptom if it stops:** unanswered inquiries stay `sent` indefinitely, and a
student cannot tell *"the landlord has not replied yet"* from *"the landlord
will never reply"* — the screen is identical either way. They wait on a
listing they should have moved on from, which is a worse outcome than a plain
refusal.

It also silently holds the one-open-inquiry-per-unit slot, so the student
cannot ask again about the same unit even months later.

**Time to visible:** never, without the alert. Nobody complains about a message
that was merely ignored.

### 4. Image variant generation

| | |
|---|---|
| **Schedule** | On `UnitPhoto` create |
| **Does** | Produces thumb/medium/large WebP variants; sets `processing_status` |
| **Guarantees** | Page weight, not correctness |

**Symptom if it stops:** the API serves originals, so listings still work and
still show photos — they are just 4–8 MB each, on metered mobile connections.
Degradation, not breakage, which is the design intent and also why nobody
notices.

**Time to visible:** a bandwidth bill, or a complaint about slow pages.

---

### Installing the schedule

`config/jobs/schedule.py` is the single table of recurring jobs, and
`manage.py install_schedules` registers them with rq-scheduler idempotently.
Run it on deploy; `--dry-run` prints the table without touching Redis.

The set of things that must be running is a fact about the system, so it lives
in one readable place rather than as `scheduler.cron(...)` calls scattered
across app modules. An architecture test asserts that every entry points at a
real function, targets a configured queue, and is described in this document —
because a job with no runbook entry has no alert, and a job with no alert may
as well not run: its failure is invisible.

### The NULL-ordering trap in sweeps

PostgreSQL sorts NULLs **last** in an ascending order and **first** in a
descending one. Every sweep that picks "the oldest N rows" is therefore one
`order_by` away from a backlog that grows while the job reports success.

It has already bitten once, in `route_stale_distances`: a nullable `routed_at`
ordered ascending returned the rows that already *had* an answer and left the
never-routed ones permanently at the back. Fixed with
`F("routed_at").asc(nulls_first=True)`.

The tenancy sweeps avoid it differently, and the difference is worth knowing:
they filter `deadline__lte=now` first, which excludes nulls regardless of where
they would have sorted. The filter is what makes them safe, not the ordering.

**A new sweep that orders on a nullable column without such a filter must say
`nulls_first=True` explicitly.** The audit as of this writing:

| Query | Column | Nullable | Safe because |
|---|---|---|---|
| `route_stale_distances` | `routed_at` | yes | explicit `nulls_first=True` |
| `sweep_overdue_claims` | `confirmation_deadline` | **no** | column is NOT NULL |
| `sweep_overdue_disputes` | `escalation_deadline` | yes | `__lte` filter excludes nulls |
| `Property.Meta.ordering` | `published_at` | yes | explicit `nulls_first=True` — drafts lead a landlord's own list, which is intended and now stated rather than inherited from the sort direction |
| `order_by_campus_distance` | `nearest_campus_km` | no in practice | tenant scoping reaches properties *through* `campus_distances`, so `Min()` is never taken over an empty set |

## Alerting

**Alert on the age of the oldest unresolved item, never on queue volume.**

Volume is not a health signal. Forty items with the oldest two days old is a
busy week. Three items with the oldest thirteen days old is an emergency, and a
volume threshold will not fire on it. Every threshold below is an age.

| Alert | Condition | Severity |
|---|---|---|
| Dispute SLA at risk | Oldest escalated dispute > **10 days** old | Page |
| Dispute SLA breached | Any dispute auto-resolved by timeout in the last 24h | Page |
| Claim confirmation stalled | Oldest `pending` claim past its `confirmation_deadline` by > **2 hours** | Page |
| Retention overdue | Oldest `StudentVerificationRequest` past its retention window with `document_deleted_at IS NULL` > **6 hours** | Page |
| Variants stalled | Oldest `UnitPhoto` in `processing_status='pending'` > **1 hour** | Warn |
| Routing stalled | Oldest `PropertyCampusDistance` with null `walking_minutes` > **24 hours** | Warn |
| Rating drift | Any sampled aggregate differing from its recomputed value | Page |
| Reconciler stalled | Oldest aggregate `computed_at` > **48 hours** | Warn |
| Worker absent | No RQ job of any kind completed in **15 minutes** | Page |

The "dispute SLA at risk" threshold is deliberately 10 days rather than 13: it
has to leave time to actually do the work, not merely to observe the miss.

**Do not alert on job success.** A job that runs and finds nothing to do is
indistinguishable from a job that runs correctly, and both are indistinguishable
from a job whose query is subtly wrong. Alert on the *backlog it is supposed to
be draining* — that is the only signal that survives a bug in the job itself.

The "worker absent" alert is the catch-all: it fires when the queue is not being
serviced at all, which is the common failure (a worker container that exited and
was not restarted) and would otherwise show up as four separate age alerts
firing at four different times.

---

## Metrics worth watching

Not alerts — trends that indicate whether the design is working.

| Metric | Why | Watch for |
|---|---|---|
| Share of tenancies by `confirmation_source` | ADR-004's volume control | `application` should dominate. If `claim`-sourced tenancies grow as a share, either the on-platform application flow has friction or landlords are settling off-platform |
| Escalated share of disputes | Whether dispute typing is working | Should be a minority. If most disputes escalate, the typed reasons are not matching reality — see the open question in ADR-004 |
| Per-landlord `dispute_rate` | Tactical disputing | Requires a denominator guard; meaningless below ~10 claims |
| Per-landlord `dispute_upheld_rate` | Whether disputes are honest | A landlord with high rate and low upheld rate is gaming the mechanism |
| Reviews carrying a dispute annotation | SLA health, indirectly | A rising share means we are missing deadlines |

---

## Runbook stubs

**Worker is down.** `docker compose ps rq-worker`; check the container exited
rather than being unhealthy. Restart, then check the four age alerts have
cleared — a restarted worker drains the backlog, it does not retroactively meet
a missed deadline.

**Retention backlog.** Do not clear `document_deleted_at` to silence the alert.
Run the job manually, verify the objects are gone from the private bucket, and
work out why the schedule stopped. The alert is measuring a legal obligation.

**Dispute queue over threshold.** Work oldest-first, always. Working newest-first
maximises the number of deadlines missed.
