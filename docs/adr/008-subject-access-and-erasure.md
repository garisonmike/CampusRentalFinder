# ADR-008: Subject access and erasure

**Status:** Accepted
**Date:** 2026-08-26
**Amended:** 2026-08-26 — access log pseudonymised rather than linked; landlord erasure added
**Deciders:** Tech lead

## Context

Kenya's Data Protection Act 2019 gives a data subject the right to obtain a
copy of the personal data held about them (§26(a)) and the right to have it
erased (§26(e), §40). The platform holds, for a single student: an account, a
university affiliation, a student email address, an identity document, a
verification decision, tenancy records, claims, applications, and reviews they
wrote about other people's property.

Two things force the decision now rather than later.

**Retrofitting erasure is where the bad deletes come from.** A schema built
without it accumulates `CASCADE`s that nobody chose, and the first erasure
request discovers them in production. We have `PROTECT` on the relations that
matter precisely because the review evidence must survive — which means a naive
`user.delete()` does not work today and would have to be *made* to work later,
under time pressure, by someone weakening those constraints.

**Erasure and the trust property are in direct tension**, and that tension has
an obvious wrong resolution:

> If erasure deleted the subject's reviews, then anyone could remove criticism
> of a property by deleting the account that wrote it. A landlord who wanted a
> bad review gone would need one cooperating student and one support ticket.
> Every control in ADR-004 — the tenancy requirement, the claim timeout, the
> dispute typing, the neutral annotation — would be routed around by a feature
> we built to be compliant.

## Decision

**Erasure anonymises in place. It does not cascade.**

The account row survives. Every identifying field on it is overwritten, the
password is made unusable, the account is deactivated, and `erased_at` is set.
Every foreign key still resolves, so a review keeps its text and its tenancy
keeps its evidence — but nothing on the account names a person, and nothing
links the two back together.

Wherever an author's name would appear, an erased account renders as
**"Former student"**.

### What the subject is actually entitled to

The right to be forgotten is not a right to unpublish what you said about
someone else. What a subject *is* entitled to is that the content can no longer
be traced back to them. Tombstoning delivers exactly that and nothing more.

### What is retained, and why

| Retained | Why |
|---|---|
| **Reviews**, with the author tombstoned | Deleting them on request makes erasure a suppression tool. The text is about a property, not about the author. |
| **Tenancies** | They are the evidence a review rests on, and they are simultaneously the **landlord's** record of who occupied their property. A subject cannot erase the other party's records. |
| **Claims and applications** | Same: each names a landlord or caretaker who is also a party. |
| **Verification outcome** (status, dates, reason) | The record that a decision was made. The **document itself is already gone** — retention deletes it at 7 or 30 days, long before most access requests arrive. |
| **`DocumentAccessLog`**, **pseudonymised** | An audit trail of **our staff's** actions. Retained, but every link to the subject is cut — see the amendment below. |
| **Rating aggregates** | Recomputed unchanged, because the review survives. An erasure that silently moved a property's score would be a way to launder a rating. |

### What is erased

Email, name, phone number, avatar, password, student email address, course,
year of study, and any stored rejection reason. Verification documents are not
listed because retention has already deleted them; if one is still live, the
retention job removes it on its own deadline.

### Subject access

`export_personal_data(user)` assembles the export **per model, by hand**. A
generic relation walk was rejected: it would follow `DocumentAccessLog` and
hand a student the names of the staff who looked at their ID, and it would
follow `Tenancy` into a landlord's records. Both are somebody else's personal
data and neither is covered by the subject's request.

Two specific omissions, both deliberate:

- **The reviewer's identity is never in the export.** Naming the member of
  staff who refused a student's ID, in a document handed to that student, is
  how a policy decision becomes a personal one. The *reason* is included,
  because the student needs it to resubmit.
- **The document image is never in the export.** Returning it would re-expose
  an identity document to whatever channel the export travels over, which is
  the opposite of holding less data. The decision is the record.

An erased subject who asks again is told the account was erased, rather than
handed a plausible-looking empty record — and their reviews are still listed,
because they still exist and the subject should not be left believing
otherwise.

## Consequences

- **`user.delete()` is not the erasure path** and must never become it. The
  `PROTECT` constraints on `Tenancy.tenant` and `ReviewResponse.author` will
  refuse it, which is the intended behaviour and not an obstacle to route
  around.
- **The tombstone email must stay unique.** `User.email` is the login
  identifier and carries a `UNIQUE` constraint, so a shared placeholder would
  make the second erasure fail. Each gets a random local part on the
  RFC 2606-reserved `erased.invalid` domain, which can never be routable.
- **A tombstoned review is still a real review.** It stays published, stays in
  the aggregate, and stays disputable. Nothing downstream needs to special-case
  it, which is the main practical argument for anonymising over deleting.
- **Erasure is irreversible and refuses to run twice.** There is no un-erase:
  the data is gone, and a second call raises rather than generating a fresh
  tombstone over an existing one.
- **Landlord erasure is covered below**, in the 2026-08-26 amendment. The
  original version of this ADR deferred it; deferring turned out to be the
  expensive option, because every month of listings makes the properties harder
  to detach from an owner.
- **There is no `Inquiry` model yet.** The subject access brief listed
  inquiries; the draft's `RentalInquiry` is deleted in Phase 7 and its
  replacement has not been built. When it is, it goes in the export, and the
  test that enumerates the export's sections is where that will be noticed.


---

## Amendment (2026-08-26), part 1: the access log is pseudonymised, not linked

The original decision retained `DocumentAccessLog` intact on the grounds that
it is an audit trail about our staff. That was right about *why* it survives
and wrong about *what* survives with it: the row still reached the subject
through `document → request → profile → user`, so "retained" quietly meant
"still linked to a named person, indefinitely, after that person asked to be
forgotten".

Neither extreme is the answer. Deleting the trail destroys evidence a regulator
is entitled to and the subject has no right to remove. Keeping the foreign keys
leaves the subject reachable from it.

**Resolved: keep the row, cut every link.**

Every `VerificationRequest` carries a `subject_token` generated at creation:
**random, stored, and derived from nothing.** A hash of a user id would be
reversible by enumerating the users, which is obfuscation with extra steps
rather than pseudonymisation. Each access log row copies the token of the case
it belonged to, so rows sharing a token were accesses to the same case.

At erasure, `document` and `verification_request` are set to null.
`subject_token`, `reviewer`, `reviewer_label`, `accessed_at`, `request_id` and
`purpose` survive.

| Question | Before erasure | After |
|---|---|---|
| Who opened this case, when, why? | yes | **yes** |
| How many times was this case opened? | yes | **yes** (group by token) |
| Which cases did this reviewer open? | yes | **yes** |
| Whose case was it? | yes | **no** |

**That last row is deliberate and irreversible by design.** The token is
random, so there is no key, no salt and no lookup table anywhere in the system
that could undo it — not held by us, not recoverable under compulsion, not
recoverable by us if we later wished we could. Anyone reading this looking for
a way to re-link the trail should stop: there is not one, and adding one would
defeat the whole mechanism.

The property is tested by **walking the foreign keys** from a log row and
asserting none of them lands on the subject, rather than by asserting on the
two columns anyone happens to remember. A field added later that reintroduces a
path is exactly the regression a remembered-column test cannot catch.

## Amendment (2026-08-26), part 2: landlord erasure

A landlord is **two things at once**, and the erasure decision falls between
them:

- a **natural person**, whose name, phone, personal email, national ID, KRA
  PIN, payout phone, avatar and business name are personal data; and
- a **counterparty** to contracts other people are still relying on, whose
  property records are the substrate of students' tenancy history and reviews.

### The balancing test

| | |
|---|---|
| **Erasure right** | DPA §26(e), §40. Applies squarely to the personal data above. Nothing about running a letting business requires us to keep a landlord's national ID once the relationship ends. |
| **Contract performance** | DPA §30(1)(b). A tenancy is a live contract between the landlord and a student. While one is running, the student needs a counterparty who can be reached about a leaking roof. |
| **Legal claims** | DPA §51(1)(c). Deposit disputes, rent arrears and habitability complaints are resolved from the tenancy record, and both parties may need it. |
| **Third-party rights** | The decisive one. A student's tenancy history and their review are **their** data as much as the landlord's. A landlord cannot exercise an erasure right over them, any more than a student could delete a review by closing their account. |

**Where the line falls:** the personal data goes, the business record stays.
That is not a compromise between the two — it is the only reading under which
neither party erases the other's records.

### Properties go dormant, they do not cascade

`PropertyStatus.DORMANT`: unlisted, unsearchable, closed to new applications,
claims and inquiries, with existing tenancies and reviews intact and attributed
to a **"Former landlord"** tombstone. `LandlordRatingAggregate` is retained
against the same tombstone.

Deliberately distinct from `ARCHIVED`, which is a decision an owner made about
a listing they still hold. **Nothing moves a property out of `DORMANT`,**
because there is no owner left to do it.

**Reviews of a dormant landlord's properties remain visible.** Same reasoning
as student erasure: deleting criticism by deleting the account cannot be an
available move, and it would be a considerably more attractive one here — the
landlord is the party a bad review is about.

### A landlord with running tenancies cannot complete erasure

`landlord_erasure_blockers()` returns the reason, and `erase_landlord_data()`
refuses. **Flagged, never silently partial.** Erasing the fields that happen to
be safe and leaving the rest is the worst available outcome: the subject
believes they are erased, the platform believes it complied, and neither is
true.

The block clears on its own as the tenancies end — currency is derived from the
dates (ADR-004), so no job has to run for yesterday's blocker to be gone today.
