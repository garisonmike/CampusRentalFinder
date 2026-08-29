# Pre-launch: what has never been exercised

This file is the honest list. Everything here is either untested, tested only
in a way that does not resemble production, or known to be unfinished — and
none of it is reachable by seeding, which is why it is a document rather than a
command.

It exists before anyone thinks about a launch date, because the alternative is
discovering the list one item at a time in the week after.

**The bar for removing an entry is the same as the bar for the rest of the
project: it has been watched working against something that resembles the real
thing.** Not "we wrote a test", not "it should be fine" — watched.

---

## 1. Throttles have never met a plausible traffic shape

**Status: untested, and not a seeded-data problem.**

Every rate limit is tested by tripping it deliberately: send N+1 requests, get a
429. That proves the limit is wired up. It says nothing about whether the
numbers are right, because a limit is a judgement about a *distribution* and
every test we have supplies a spike.

What would need to be measured, per scope:

| Scope | The question the number answers |
|---|---|
| `public_read` | Can a lecture hall of students browsing listings on the same campus wifi — one NAT, one apparent IP — read the catalogue without any of them being throttled? |
| `inquiry` | Does a student comparing six properties in an evening hit the limit before an abuser does? |
| `auth` | Does a credential-stuffing run get stopped before a student who forgot their password gets locked out? |
| `claim` | Is the monthly cap survivable for somebody recording three years of pre-platform history at once? |
| `privacy` | Is a burst here ever legitimate, or is any burst a signal? |
| `write` | What does a landlord with forty units doing a vacancy sweep on Monday morning actually generate? |

**The keying is now asserted; the number is not.**
`backend/tests/test_public_read_caching.py` models a lecture hall behind one
address and proves that `public_read` is spent per address rather than per
person — so the assumption stops being invisible even though the rate stays an
open question. Two things it also established, worth knowing before anyone
writes another throttle test:

- `override_settings(REST_FRAMEWORK=...)` does **not** reach the throttle. DRF
  binds `SimpleRateThrottle.THROTTLE_RATES` as a class attribute at import, so
  an override updates `api_settings`, reports itself as applied, and the
  requests run against the real rate. A test written that way proves nothing
  and looks like it proves something.
- Public listing reads are now `Cache-Control: public, s-maxage=…,
  stale-while-revalidate=…`, so a CDN can answer most of them. That removes
  traffic from the origin and therefore from the throttle. **It does not make
  the keying correct** — it makes the wrong keying hurt less, which is a
  different thing and should not be mistaken for the fix.

**The NAT case is the one to measure first.** Kenyan campus wifi puts hundreds of
students behind one address, and DRF's default anonymous throttling keys on IP.
A `public_read` limit that is generous for one person is a limit divided by four
hundred for a hall — and the failure mode is that browsing listings, which is
the product, stops working for everybody at once, on the busiest day of the
intake.

Needs: a load generator with a realistic mix (mostly reads, occasional writes),
a NAT simulation, and a run against staging. Not reachable by seeding, because
seeding produces data and this is about arrival rates.

---

## 1b. Public listing reads must stay cacheable at the edge

**Status: headers set, CDN behaviour unverified.**

`Cache-Control` is emitted by `config/api/caching.py` on the three public read
endpoints and asserted in tests. What has never been checked is whether a real
CDN in front of a real deployment honours it — that it caches on the canonical
host, that it does not key on the tenant subdomain in a way that shards the
cache per university, and that a signed-in reader's `private` response is not
stored.

The last one is the dangerous one. The decorator refuses to mark an
authenticated response `public`, but a misconfigured edge that ignores
`private` would serve one student's response to another. It needs watching
against the real CDN, not asserting in a unit test.

**To remove this entry:** a request through the production edge, twice, with
the second served from cache; and an authenticated request through the same
edge that is demonstrably not stored.

---

## 1c. Synchronous image work on the upload request

**Status: measured, small, and unmeasured under concurrency.**

`add_photo` sniffs and strips metadata on the request path. Measured on a real
4 MB, 4032×3024 phone photo with EXIF and GPS: **0.26 s, 5 MB peak**. That is
down from 15.7 s and 838 MB before the stripper was rewritten, and it is small
enough that moving it to a job would cost more than it saves — a deferred strip
means un-stripped bytes exist somewhere, and ADR-007's fallback serves the
original.

What is unmeasured: what that costs at concurrency. 0.26 s of CPU per upload is
fine for one; twenty simultaneous uploads is five CPU-seconds of image decoding
competing with request handling, and nobody has a number for how many workers
this deployment will have or how many concurrent uploads to expect.

The identity-document path adds a second cost on the same request: the object
store PUT happens **inside** the transaction, measured at 18 ms median for a
218 KB document and 70 ms for a 4.3 MB one against MinIO on loopback. So
roughly 0.33 s per upload, ~70 ms of it holding a database connection and a row
lock — over a link far friendlier than production object storage.

**To remove this entry:** a measurement of upload throughput at the intended
worker count, with the decode running and the store over a real network,
against the concurrency this deployment actually plans for. If it turns out to
matter, the fix is not to defer the strip — it is to bound concurrent uploads
and to move the store out of the transaction (`docs/OPERATIONS.md`, "The upload
ordering is narrowed, not closed").

---

## 2. The frontend has never been served to a browser

**Status: unexercised.**

Every frontend assertion in this project runs in jsdom, which has no layout, no
painting, and no network stack. What that cannot see:

- **Contrast in practice.** `theme/contrast.test.ts` proves the derivation
  mathematically and `theme/gallery-over-photos.test.ts` measures controls over
  real photographs, but neither has looked at a rendered pixel.
- **Layout at real viewport sizes.** Every "mobile-first" claim in this
  repository is an assertion about class names.
- **The performance budget as experienced.** `check-bundle-budget.mjs` measures
  transfer size. It says nothing about parse time on a mid-range Android, which
  is the device the budget exists for.
- **Font loading, image decode, cumulative layout shift.**

Needs: a headless browser run at a few viewport widths against the seeded
platform, and one pass on a real low-end device.

---

## 3. Nothing has been run against a database of realistic size

**Status: measured at development scale only.**

The seeded platform is about 100 units and 100 tenancies. Query counts are flat
in row count where it matters (`tools/verify_commits.sh` runs the assertions),
but "flat" was measured at page sizes of 20 against tables of hundreds.

Unmeasured: index behaviour at 100k rows, the `ExclusionConstraint` on tenancy
date ranges under concurrent inserts, the cross-check aggregation over a full
catalogue, and whether `order_by("-published_at", "-id")` still uses an index
when the table is large enough for the planner to change its mind.

---

## 4. The scheduled jobs have never run on a schedule

**Status: each job has been executed; the scheduler has not been watched.**

Every job in `config/jobs/schedule.py` has been run directly and, since the
seed drives them, through a burst worker. Nobody has watched
`install_schedules` register them against a long-running rq-scheduler and
observed a week of firing.

The specific risks: a job that overruns its interval and overlaps itself, a
worker that dies and leaves the schedule intact so nothing fires and nothing
errors, and the retry behaviour of a job that raises — none of which the
direct-execution path exercises.

`docs/OPERATIONS.md` alerts on the age of the oldest unprocessed row precisely
because these failures are silent. Those alerts have also never fired in anger.

---

## 5. Email has never been delivered

**Status: the outbox has been asserted; nothing has been sent.**

Every mail assertion reads Django's locmem outbox. No message has been rendered
by a real provider, and nothing has checked deliverability, SPF/DKIM, or what
these messages look like in Gmail on a phone.

The vacancy prompt is the one that matters: the entire freshness mechanism
depends on landlords opening it, and a message that lands in spam is a job that
appears to work and achieves nothing. It was held out of the schedule for a
round for exactly this class of reason.

---

## 6. Multi-tenancy has never been exercised across hosts

**Status: tested through the middleware; never through DNS.**

Tenant resolution is asserted by setting a `Host` header. Nothing has run
against real subdomains with real TLS, and the canonical-host redirect logic
(ADR-001) has never been observed by a crawler.

---

## 7. Nobody has used it

**Status: no user has ever seen this product.**

Every judgement in the interface — that "Still 6 free" is the right label,
that naming the excluding filter helps, that a plain dispute annotation reads
as neutral rather than as a warning — is a designer's argument, tested against
its own reasoning. Several are probably wrong in ways no amount of internal
review will find.

The cheapest correction available before launch is watching three students and
one landlord use the seeded platform for twenty minutes each.

---

## Known-unfinished, tracked elsewhere

- **AAA contrast is unreachable** by the current derivation for roughly 21% of
  the colour space. Recorded in `theme/contrast.test.ts` as deliberate: AA is
  the target, and reaching AAA would mean overriding the tenant's colour, which
  ADR-005 does not allow.
- **`nearest_campus_km` is widened client-side** because drf-spectacular marks
  every read-only field required. Noted in `frontend/src/api/types.ts`.
- **Caretaker review replies** are refused by the API and hidden by the portal;
  ADR-003 draws that line deliberately and it is not a gap.
