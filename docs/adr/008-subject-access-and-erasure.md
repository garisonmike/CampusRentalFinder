# ADR-008: Subject access and erasure

**Status:** Accepted
**Date:** 2026-08-26
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
| **`DocumentAccessLog`** | An audit trail of **our staff's** actions, not the subject's data. A regulator asking "who looked at this student's ID" needs an answer months later, and the subject is not the party it holds to account. |
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
- **This ADR does not cover landlords.** A landlord erasure would strand
  properties, units and photos that other people's tenancies depend on, and the
  balancing test there is different — a business relationship rather than a
  consumer one. It needs its own decision, and pretending this one covers it
  would be worse than saying so.
- **There is no `Inquiry` model yet.** The subject access brief listed
  inquiries; the draft's `RentalInquiry` is deleted in Phase 7 and its
  replacement has not been built. When it is, it goes in the export, and the
  test that enumerates the export's sections is where that will be noticed.
