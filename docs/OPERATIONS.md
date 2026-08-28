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

### 2a. Termination auto-confirmation

| | |
|---|---|
| **Schedule** | Hourly |
| **Does** | Confirms `TerminationRequest` rows past `confirmation_deadline` that are still `pending` |
| **Guarantees** | That a stay which ended is recorded as ended, without either party's silence acting as a veto |

**Symptom if it stops:** early terminations sit `pending` for ever and the
stays behind them keep reading as **current** — so a landlord's vacancy list is
wrong, and a student who moved out months ago still shows as living there.
Nothing errors. Silence stops being a signal and quietly becomes a veto, which
is the behaviour the confirmation window exists to defeat.

**What it deliberately does NOT sweep:** a termination escalated as
`termination_defeats_review`. Letting silence confirm one of those would delete
the counterparty's own review right by inaction — the precise outcome
escalating it exists to prevent. Those wait for an administrator, and the
dispute SLA above covers them.

**Time to visible:** 7 days plus however long anyone takes to notice a vacancy
figure that never moves. Realistically: never, without the alert.

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

### 2b. Vacancy staleness prompts

| | |
|---|---|
| **Schedule** | Weekly, Monday 09:00 |
| **Does** | Emails landlords whose `vacant_count` has not been restated within `VACANCY_STALE_DAYS`, grouped one message per landlord |
| **Points at** | `PATCH /api/v1/properties/manage/{slug}/units/{id}/vacancy/` — the only write path for the count, which stamps who said it and when |
| **Guarantees** | That the only person who *can* refresh a stated vacancy is asked to |

`vacant_count` is stated by the landlord and never derived — they know about
the room let off-platform last week and we do not, so their number is usually
better than anything we could compute. The cost of that choice is that it only
stays true if somebody restates it.

**Symptom if it stops:** counts age, nobody is asked, and listings quietly
become misleading — advertising last term's free rooms. **Nothing errors**, and
nothing can: a stale number is indistinguishable from a current one to
everything except its own timestamp. A student travels to view a room that was
let in March.

The API mitigates rather than hides this: `vacancy_freshness` and
`vacancy_age_days` ship on every unit and the frontend is required by contract
note to surface them. So a stopped job degrades to "every listing is labelled
stale", which is honest and visible, rather than to silence. That is the
intended failure mode and it is why the count is never hidden or zeroed.

**Time to visible:** the labels start appearing at `VACANCY_STALE_DAYS` (30)
whether or not the job runs, so this one *does* surface on its own — the alert
is for the case where an operator wants to know the prompts stopped before
every listing on the platform is wearing a stale badge.

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

## Checks that can pass without checking

**This has been the most productive bug class in the project.** Five instances
so far, none of which failed anything. Each was found by someone asking "would
this have caught it?" — or by reading a log line that disagreed with a commit
message, or by opening a diff instead of the summary of it — rather than by a
test going red.

### The shape

The generic form is: a check reports success, and the thing it was supposed to
look at was never in scope. But that is too vague to grep for. The specific
form, which all four share, is sharper:

> **The check and the thing it checks were configured in two places, and the
> wrong one won silently.**

Not "won and errored". Won *silently* — the losing configuration stayed in the
file, readable, plausible, and completely inert. Anyone editing it got a green
build and believed they had changed something.

That is worth grepping for directly. **Any value that appears twice is a
candidate**, and the question is always: *which copy does the machine actually
read?*

### The five

| # | The two places | Which won | What it looked like |
|---|---|---|---|
| 1 | `--cov=<pkg>` flags in `addopts`; `source` in `[tool.coverage.run]` | the flags | 84% of a little over half the code, reported as 84% |
| 2 | `order_by("routed_at")`; PostgreSQL's **unstated** `NULLS LAST` default | the default | a healthy `enqueued` count every run, backlog growing |
| 3 | the test file's skip condition; the CI service-container config | the skip | a green build with the compliance test never executed |
| 4 | `--cov-fail-under=88` in `addopts`; `fail_under = 89` in `[tool.coverage.report]` | the flag | CI printing "88% reached" on the commit that raised it to 89 |
| 5 | a phase summary asserting a bug and its fix; the commit that supposedly contained them | **the summary** | a `vacant_count` reconciliation bug reported, accepted, and specced against — for a function that has never existed |

**Number 5 is the one with no code in it at all**, and it is the most dangerous
because it is the fastest to propagate. A phase summary described a
`Unit.vacant_count` reconciliation counting tenancies by `status='active'`, with
a plausible consequence: pooled blocks reporting themselves full and vanishing
from search. It was accepted, and the next round's brief carried a clause
requiring the fix. **There is no such function.** The commit it was attributed
to touches `vacant_count` in one test-fixture argument. `Unit.vacant_count` has
one reader, one constraint, and zero writers.

Nothing about the report was implausible — that is the point. It described a bug
of a shape the codebase had genuinely produced elsewhere, in a place it could
have been, with a consequence that follows. The check (the summary) and the
checked thing (the diff) diverged, and the summary won because **the summary is
what everyone reads**. A diff is read once by its author; a summary is read by
everyone downstream and then built upon.

> **Verify findings against the diff, not against the report of the diff —
> including your own.** `git show <sha> | grep <symbol>` costs ten seconds and
> is the only thing that distinguishes a finding from a plausible story about
> one. A summary that names a function is asserting that function exists.

**Number 2 is the variant to watch for** among the code-level ones, because it
is the one a grep for duplicated values will not find. There, the second
"place" was not a line anywhere in the repository — it was a **library or database default that
nobody had written down**. The code said `order_by("routed_at")` and meant
"oldest first, never-routed first of all"; PostgreSQL said "NULLs last" and
nobody had stated which of those two the system was relying on.

So the grep has two halves:

1. **Duplicated values.** The same number, package list, or threshold appearing
   in two files, or twice in one file. One of them is inert.
2. **Unstated defaults on the critical path.** Sort order, null handling,
   manager selection, exception handling, throttle rates. If the behaviour
   depends on a default nobody named, the default is the second place.

### The four questions

**Would this fail if the thing it checks were broken?** Break it and watch. The
only one that gives a definite answer, and it is cheap: comment out the guard,
run the test, put it back. The re-read in `delete_verification_document` was
confirmed this way — four tests fail without it. The coverage floor was
confirmed by setting it to 99 and watching it fail.

**Is the scope derived, or written down?** Any hand-maintained list — apps,
packages, models, routes — drifts the moment something is added. Derive it from
a fact that cannot drift: what is installed, where the code lives, what the
router exposes.

**Can it skip?** A skipped test and a passing test look identical in a summary
line. If a skip is legitimate locally but not in CI, assert that distinction.

**Was it actually run on what you are claiming?** CI builds the head of a
push, so a green tick on a ten-commit push says nothing about the nine
underneath it. `tools/verify_commits.sh` checks each one in a throwaway
worktree. A bisect lands on a commit nobody ever built, and the first time
anyone finds out is when they are already looking for something else.

**Does the fixture contain the case?** A suite whose fixtures are all
currently-running tenancies cannot see a bug that needs history — which is
exactly how the `vacant_count` bug survived eleven filters with the same shape.
`docs/ENGINEERING.md` records why the default factory shape is the awkward case.

## Reconcilers are blind to absence by construction

**A reconciler that samples the derived side can only compare rows that exist.**
It walks the caches, recomputes each, and reports the ones that disagree. A
subject that *should* have a cache and does not is not sampled, does not
disagree, and does not appear in the count — so the job reports no drift, and
"no drift" is what everybody reads.

This is not a bug in any particular reconciler. It is what sampling the derived
side means, and it will be true of the next one somebody writes.

### The rule

> **Every reconciler must count what should exist and does not, as a separate
> number with a separate alert. "No drift" is meaningless without it.**

Separate, not folded into the drift count, because the two have different
causes and different fixes. Drift means the job ran and the number moved since:
recompute it, then find out why. Absence means it never ran at all — a queue
that was down, a write path that skipped the enqueue, a restore that brought
the source rows without their caches. A single number would send whoever is
on call looking for the wrong thing.

### Found this way

`reconcile_rating_aggregates` reported `drifted=0` against a platform with 33
reviews and zero aggregates: a clean bill from a check that had looked at
nothing. It now returns `missing` alongside `drifted`, and both have alert rows.

### The audit

Every sweep and reconciler in the project, checked for the same shape:

| Job | Walks | Blind to | Verdict |
|---|---|---|---|
| `reconcile_rating_aggregates` | aggregate rows | reviewed subjects with no aggregate | **had it — fixed** |
| `route_stale_distances` | `PropertyCampusDistance` rows | a property with no distance row for a campus that exists | **has it** |
| `sweep_expired_documents` | `VerificationDocument` rows | an object in the bucket with no row pointing at it | **has it, and it is the worst one** |
| `sweep_due_erasures` | `ErasureRequest` rows | nothing: the request *is* the subject | clean |
| `sweep_overdue_claims` | claims past deadline | nothing: a claim is its own subject | clean |
| `sweep_overdue_disputes` | escalated claims | nothing | clean |
| `sweep_overdue_terminations` | pending terminations | nothing | clean |
| `expire_stale_inquiries` | inquiries in `sent` | nothing | clean |
| `prompt_stale_vacancies` | units with aged counts | nothing — it **already** includes never-stated units, which is this rule applied before it was written down | clean |

**`route_stale_distances`.** A campus added after a property was published gets
no `PropertyCampusDistance` row, and the sweep only routes rows that exist. The
property is then invisible to that campus for ever, with no error anywhere —
the same silent-invisibility failure the publish gate exists to prevent,
arriving by a different door. Nothing creates the join retrospectively.

**`sweep_expired_documents`.** `submit_verification_document` writes the object
to the bucket **before** opening the transaction that creates the row. If that
transaction fails, the bytes are in private storage with nothing pointing at
them: a national ID document that no retention sweep will ever see, because
every sweep enumerates rows. The row-side accounting is impeccable —
`deleted_at` is only set after a re-read confirms the object is gone, and a
check constraint forbids the halfway state — and none of it can see a file the
database has never heard of.

`accounts.retention.orphaned_document_objects()` now answers the question from
the other side: it lists the bucket and subtracts the rows. It returns keys
rather than a count, because the next question is always "which ones" and a
compliance answer of "seventeen" is not one.

**It is an alert, not a test.** The mechanism has a test — a planted orphan is
found — but "the bucket is clean" is a fact about an environment rather than
about the code, and asserting it in CI would make the suite fail whenever a
developer's seed data shares a bucket with it. It did, immediately. The claim
belongs in the alert table above and in `run_compliance_sweeps`.

That is the generalisation stated as strongly as it deserves: **when the thing
being protected lives somewhere other than the database, the database cannot be
the only place you look.**

## Checks whose scope is narrower than the belief attached to them

**A different failure from the five above, and worth keeping separate.** Those
five are checks that ran and reported nothing useful. This one is a check that
ran **correctly**, reported **accurately**, and had a belief attached to it
wider than the thing it measured. Nothing was broken; the reading was true. It
simply was not the reading anybody thought they had.

### The instance

`theme/contrast.test.ts` sweeps roughly 30,000 colours and proves that every
tenant palette produces a foreground meeting AA **against its own background
token**. That is a real guarantee and it holds. The belief that grew around it
was "the palette suite proves the interface is readable in any tenant colour",
and it does not, because one class of surface has no token behind it at all.

The photo-gallery arrows sit on **user-supplied photography**. Their background
is whatever a landlord uploaded. At 90% opacity in the pale-yellow tenant
palette the control dissolved entirely into a bright photograph — while
`--secondary` and `--secondary-foreground` continued, correctly, to pair at
better than 4.5:1 with each other. Both facts are true at once and only one of
them was being read.

Nor could the page-level palette suite have caught it: jsdom has no layout and
no painting, so axe's contrast rule does not run there. Twenty-four
page × palette combinations passed on the commit that shipped the defect, and
they were right to.

### The permanent consequence

> **Any control placed over a photograph, a video, a map tile, or any other
> user-supplied imagery is outside the palette suite's reach, permanently and
> by construction. It needs deliberate treatment — an opaque fill, its own
> border, or a scrim — regardless of what the suite says.**

This is not a gap to be closed later. There is no palette computation that can
make a claim about an image nobody has seen. The suite's silence about these
controls is correct behaviour, and the mistake is only ever in reading that
silence as approval.

### How to spot the shape

The question is not "did the check pass?" but **"what exactly did it measure,
and is that the sentence I am about to say about it?"** The gap opens when a
check's name is broader than its assertion: "contrast" measured
token-against-token, "accessibility" measured a tree with no pixels,
"integration" measured two services with a third stubbed. The check is right;
the summary of it is what drifts.

Related to but distinct from the previous section. There, the check and the
checked thing had diverged. Here, they agree perfectly — and the sentence
people repeat about the check has quietly widened past both.

### Where this is already enforced

| Guard | Fails when |
|---|---|
| `config/jobs/schedule.py` + architecture test | A scheduled job names a function that does not exist, a queue that is not configured, or is missing from this document |
| `ScopedThrottle` | A view names a throttle scope with no configured rate — DRF's own class silently applies no throttle |
| `tools/check_field_shadowing.py` | Run from pre-commit **before** Django imports, because the defect it catches kills the import |
| `test_the_contract_notes_reach_the_generated_schema` | A frontend-facing contract note stops being emitted into the schema |
| `test_every_api_view_declares_its_permission_classes` | A view inherits DRF's default without anyone choosing it |
| `test_minio_is_reachable_in_ci` | A compliance test would skip rather than run |
| `test_the_coverage_floor_is_declared_once` | The floor is declared in two places again |
| `test_the_performance_budget_is_enforced` (frontend) | The listing bundle grows past its budget |
| `reconcile_rating_aggregates` (the `missing` count) | A reviewed property has no aggregate row -- which the drift sample cannot see, because it walks aggregates that exist |
| `seed_platform` + `test_every_shape_survives_the_smallest_allowed_platform` | The development seed stops producing a shape the UI has a branch for |
| `tools/verify_commits.sh` | A commit in a pushed range is not green on its own — which CI cannot tell you, because it builds only the head |
| *(nothing — and there cannot be)* | A control over user-supplied imagery becomes unreadable. See **Checks whose scope is narrower than the belief attached to them**: this one is handled by construction at the component, not by a check |

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
| Rating aggregate missing | Any published review whose property or unit has **no aggregate row at all** | Page |
| Orphaned document object | Any object in the documents bucket with **no `VerificationDocument` row** (`orphaned_document_objects()`) | Page |
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
