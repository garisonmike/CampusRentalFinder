"""
The six things a frontend gets wrong by default.

These are not documentation. They are **schema descriptions**, which
`openapi-typescript` carries into the generated `.d.ts` as doc comments — so
they appear in autocomplete at the moment somebody reaches for the field, which
is the only moment they can prevent the mistake.

A markdown note in `docs/` gets read once, by whoever is reading docs that week.
A field description is read by whoever touches the field, for as long as the
field exists.

`tests/test_architecture.py::test_the_contract_notes_reach_the_schema` asserts
every field named here still carries its note in the generated schema, so these
cannot rot silently as fields are added or serializers refactored.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 1. Tenancy currency
# ---------------------------------------------------------------------------
#
# Stated three times -- on `status`, on `end_date`, and on the endpoint -- because
# it is the most likely misread in the whole contract and the failure is silent:
# a listing filtered on a status value that does not exist returns an empty page
# rather than an error.

TENANCY_STATUS = (
    "Where the tenancy stands as a RECORD: pending, confirmed, disputed, "
    "rejected or withdrawn. "
    "**There is no value here meaning 'current' or 'active'.** Whether a "
    "confirmed tenancy is running is derived from start_date and end_date, "
    "never stored -- a stored currency flag needs a job to stay true, and when "
    "the job stops the data lies silently. Filter with ?currency=current|past|"
    "upcoming instead."
)

TENANCY_END_DATE = (
    "The agreed or actual last day. "
    "**null means the tenancy is OPEN-ENDED AND STILL RUNNING** -- a real "
    "arrangement with no agreed end -- and must NOT be rendered as a finished "
    "or unknown stay. A stay that has ended always has a date here."
)

TENANCY_ENDPOINT = (
    "Tenancy records. "
    "Currency (current / past / upcoming) is DERIVED from start_date and "
    "end_date at query time and has no stored field. Use ?currency= to filter. "
    "A null end_date means open-ended and currently running, not ended."
)

TENANCY_TERMINATED_EARLY = (
    "The stay ended before its agreed end date. end_date has been rewritten to "
    "the actual last day and remains authoritative for currency; this flag is "
    "context, not a currency signal."
)

# ---------------------------------------------------------------------------
# 2. The two counts
# ---------------------------------------------------------------------------

STUDENT_COUNT = (
    "Distinct students who contributed. "
    "**This is the public denominator: render it as 'from N students'.** It is "
    "deliberately smaller than review_count whenever anyone reviewed more than "
    "one stay in the same property -- that divergence IS the de-duplication, "
    "not a bug."
)

REVIEW_COUNT = (
    "Number of review rows behind the average. "
    "**Not the public denominator** -- use student_count for that. A student "
    "who moved from a bedsitter to a one-bedroom in the same block writes two "
    "genuine reviews and counts as one voice, so these two numbers are "
    "SUPPOSED to differ."
)

# ---------------------------------------------------------------------------
# 3. The empty state
# ---------------------------------------------------------------------------

AVERAGE_RATING = (
    "Mean rating, 1.00-5.00. "
    "**null means 'no verified reviews yet' and must render as those words.** "
    "Never 0, never an empty star row, never a placeholder. On a trust platform "
    "a fabricated signal is worse than no signal, because it is "
    "indistinguishable from a real one."
)

# ---------------------------------------------------------------------------
# 4. The dispute annotation
# ---------------------------------------------------------------------------

DISPUTE_ANNOTATION = (
    "Neutral note that the landlord disputed this stay, or null. "
    "**Render it as a plain factual line. Never as a warning.** Do not grey "
    "the review out, collapse it, badge it amber, or exclude it from the "
    "average. A landlord who disputes honestly and one who disputes tactically "
    "produce the identical annotation, which is exactly why it must not read as "
    "a verdict -- styling it as one restores the veto ADR-004 removed."
)

# ---------------------------------------------------------------------------
# 5. Reviewer identity
# ---------------------------------------------------------------------------

VERIFICATION_DECISION_REASON = (
    "Why the request was approved or rejected, written for the student. "
    "**The reviewer's identity is deliberately absent from every payload and "
    "will not be added.** A named individual refusing a student's ID at their "
    "own institution is a person who can be found in a corridor. A screen that "
    "wants 'reviewed by' does not get one."
)

# ---------------------------------------------------------------------------
# 6. Gating
# ---------------------------------------------------------------------------

CAPABILITIES = (
    "What this user may do right now. "
    "**Per-student, not per-university: do NOT cache these against the "
    "university.** Gating reads the policy frozen at each student's own "
    "registration intersected with the live one, so two students at the same "
    "university can legitimately have different capabilities, and a policy "
    "change only ever widens what an existing student may do."
)

# ---------------------------------------------------------------------------
# Distances (ADR-002), which are a different kind of easy mistake
# ---------------------------------------------------------------------------

STRAIGHT_LINE_KM = (
    "STRAIGHT-LINE distance to the campus, in kilometres. Not a walking "
    "distance and never presented as one -- label it 'as the crow flies'. Real "
    "walking distance is walking_distance_km, which may be null."
)

WALKING_MINUTES = (
    "Walking time from the routing provider, in minutes. "
    "**Legitimately null**, and null must render as an em dash rather than a "
    "zero or a guess: the provider may have no route, be out of quota, or be "
    "down. A fabricated walking time erodes exactly the trust the platform "
    "sells, so the API will never substitute the straight-line estimate here."
)

# ---------------------------------------------------------------------------
# 7. Vacancy staleness (ADR-002)
# ---------------------------------------------------------------------------

VACANCY_FRESHNESS = (
    "How much to trust `vacant_count`: fresh | ageing | stale | unknown. "
    "**Surface this. Never present a stale count as current.** The number is "
    "stated by the landlord and never derived -- they know about the room let "
    "off-platform last week and we do not -- so it is only as good as its age. "
    "A stale count is still SHOWN, with its age; hiding it or zeroing it would "
    "replace a number the reader can judge with one they cannot. Render the "
    "band, do not recompute it from the timestamp: the thresholds are server "
    "side and a second copy in the client is how they drift."
)

VACANCY_AGE_DAYS = (
    "Days since the landlord last stated `vacant_count`, or null if they never "
    "have. **null is not zero** -- 'nobody has ever said' and 'said today' are "
    "opposite facts. Show alongside `vacancy_freshness`; do not derive the band "
    "from this."
)

#: Serializer field name -> the note it must carry in the generated schema.
#:
#: The architecture test reads this. A field appearing here whose description
#: has gone missing fails the build, so the notes cannot rot as serializers are
#: refactored.
CONTRACT_NOTES: dict[str, str] = {
    "status": TENANCY_STATUS,
    "end_date": TENANCY_END_DATE,
    "terminated_early": TENANCY_TERMINATED_EARLY,
    "student_count": STUDENT_COUNT,
    "review_count": REVIEW_COUNT,
    "average_rating": AVERAGE_RATING,
    "dispute_annotation": DISPUTE_ANNOTATION,
    "decision_reason": VERIFICATION_DECISION_REASON,
    "capabilities": CAPABILITIES,
    "straight_line_km": STRAIGHT_LINE_KM,
    "walking_minutes": WALKING_MINUTES,
    "vacancy_freshness": VACANCY_FRESHNESS,
    "vacancy_age_days": VACANCY_AGE_DAYS,
}

#: Schema components where a listed field name means something else entirely.
#:
#: Keyed by **OpenAPI component name**, not serializer class name -- which are
#: not the same thing: drf-spectacular strips the `Serializer` suffix and
#: appends `Request` for write components, so `ApplicationDecisionSerializer`
#: appears as `ApplicationDecisionRequest`.
#:
#: `status` is the obvious collision: an Application, an Inquiry and a
#: VerificationRequest all have one and none of them is a tenancy. `end_date`
#: collides on every model with a date range. Listed explicitly so each
#: exemption is a decision somebody made rather than a note that quietly went
#: missing.
NOTE_EXEMPT: dict[str, frozenset[str]] = {
    "status": frozenset(
        {
            "Application",
            "ApplicationRequest",
            "Inquiry",
            "InquiryRequest",
            "TenancyClaim",
            "VerificationRequest",
            "VerificationRequestRequest",
            "Property",
            "PropertyDetail",
            "PropertySummary",
            "UnitPhoto",
            "ErasureRequest",
            # An erasure's own lifecycle -- cooling off, completed, blocked,
            # cancelled -- not a tenancy's. The `status` note is about
            # tenancies and would misdescribe this one.
            "ErasureRequestResult",
        }
    ),
    "end_date": frozenset(
        {
            # A claim ASSERTS dates; it is not a tenancy and has no currency.
            "TenancyClaim",
            "ClaimCreate",
            "ClaimCreateRequest",
            # The correction exchange proposes dates, likewise.
            "Correction",
            "CorrectionRequest",
            # Accepting an application SETS the dates rather than reporting
            # them; the note about what null means lives on its own help_text.
            "ApplicationDecision",
            "ApplicationDecisionRequest",
        }
    ),
    "decision_reason": frozenset({"Application", "ApplicationRequest", "TenancyClaim"}),
}
