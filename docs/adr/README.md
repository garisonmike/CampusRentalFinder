# Architecture Decision Records

Each file records one decision: the context that forced it, the decision itself,
the consequences we accept, and the alternatives we rejected and why.

An ADR is immutable once merged. If a decision changes, write a new ADR that
supersedes it and add a `Superseded by` line to the old one — do not edit
history.

| # | Title | Status |
|---|---|---|
| [001](001-multi-tenancy.md) | Multi-tenancy via shared database, shared schema | Accepted, amended 2026-08-24 |
| [002](002-property-university-join.md) | Properties link to universities through a join model | Accepted, amended 2026-08-24 |
| [003](003-object-level-authorization.md) | Object-level authorization, not a user_type string | Accepted, amended 2026-08-24 |
| [004](004-review-integrity-via-tenancy.md) | Review integrity via a Tenancy record | Accepted, amended 2026-08-24 |
| [005](005-per-university-theming.md) | Per-university theming through database-stored design tokens | Accepted, amended 2026-08-24 |
| [006](006-geo-search.md) | Geo search stays simple initially | Accepted, amended 2026-08-24 |
| [007](007-media-object-storage.md) | Media on S3-compatible object storage | Accepted, amended 2026-08-24 |

## Format

```
# ADR-NNN: Title

Status / Date / Deciders

## Context
## Decision
## Consequences
## Alternatives considered
```

The Consequences section is not a formality. Where design review finds a
genuine flaw in a decision, it is recorded there rather than quietly worked
around in the implementation. Several of the amendments dated 2026-08-24 came
from exactly that: the objection is kept in the Decision section as the
rationale for the change, so the reasoning is not lost.
