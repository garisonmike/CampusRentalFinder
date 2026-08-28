"""
Review, response and rating-aggregate serializers (ADR-004).

Four of the six contract notes live here, because four of the six mistakes a
frontend makes by default are about ratings: the two counts that are supposed
to differ, the null that means "no reviews yet", and the annotation that must
not be styled as a warning.
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from config.api.contract import (
    AVERAGE_RATING,
    DISPUTE_ANNOTATION,
    REVIEW_COUNT,
    STUDENT_COUNT,
)

from .models import Review, ReviewResponse


class RatingAggregateSerializer(serializers.Serializer):
    """The shared shape of all three aggregates.

    A plain Serializer rather than three ModelSerializers: the fields are
    identical by design, and three copies would be three places for the
    contract notes to drift apart.
    """

    average_rating = serializers.DecimalField(
        max_digits=3,
        decimal_places=2,
        read_only=True,
        allow_null=True,
        help_text=AVERAGE_RATING,
    )
    student_count = serializers.IntegerField(read_only=True, help_text=STUDENT_COUNT)
    review_count = serializers.IntegerField(read_only=True, help_text=REVIEW_COUNT)
    rating_distribution = serializers.DictField(
        child=serializers.IntegerField(),
        read_only=True,
        help_text=(
            "Counts by star, 1-5, every bucket present. Describes RAW reviews, "
            "not de-duplicated students -- 'how the scores fall' and 'how many "
            "people spoke' are different questions and both get an honest "
            "answer."
        ),
    )
    last_review_at = serializers.DateTimeField(read_only=True, allow_null=True)
    computed_at = serializers.DateTimeField(
        read_only=True,
        allow_null=True,
        help_text=(
            "When this cache was last rebuilt. Aggregates are stale by "
            "design between a review landing and the job running; a "
            "reconciler finds drift rather than the number being recomputed "
            "per request. **Null until anything has been computed** -- the "
            "same moment average_rating is null."
        ),
    )


class LandlordRatingSerializer(RatingAggregateSerializer):
    """A landlord's record across every property they own.

    The cold-start signal (ADR-004): a property with no reviews of its own can
    show this, **explicitly labelled as being about the landlord**, never about
    this property.
    """

    property_count = serializers.IntegerField(
        read_only=True,
        help_text=(
            "How many of this landlord's properties the figure spans. Show it "
            "alongside the average: '4.2 across one property' and '4.2 across "
            "nine' are different claims."
        ),
    )


class PropertyRatingSerializer(serializers.Serializer):
    """What the property rating endpoint returns: two aggregates, side by side.

    Declared so the client's types are **generated** rather than hand-written.
    An undeclared response makes the schema say "no response body", and a
    frontend that then describes the payload by hand owns a second copy of the
    contract -- which is how `Paginated<T>` drifted, and how three of the five
    entries in `docs/OPERATIONS.md` began.

    The landlord figure is a separate key rather than a fallback value on
    purpose. A landlord's record is not this property's rating, and merging
    them would be the platform quietly answering a question nobody asked.
    """

    property = RatingAggregateSerializer(
        read_only=True,
        help_text=(
            "This property's own rating. `average_rating: null` means no "
            "verified reviews yet and must render as those words."
        ),
    )
    landlord = LandlordRatingSerializer(
        read_only=True,
        help_text=(
            "The owner's record across their whole portfolio, offered as a "
            "cold-start signal (ADR-004). **Label it as being about the "
            "landlord.** Never present it as this property's score, and never "
            "fall back to it when `property.average_rating` is null."
        ),
    )


class ReviewResponseSerializer(serializers.ModelSerializer):
    """The landlord's single reply."""

    author_name = serializers.SerializerMethodField()

    class Meta:
        model = ReviewResponse
        fields = ("id", "body", "author_name", "created_at")
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_author_name(self, response: ReviewResponse) -> str:
        from accounts.privacy import display_name_for

        return display_name_for(response.author)


class ReviewSerializer(serializers.ModelSerializer):
    """One review, as a reader sees it.

    The reviewer is read through the tenancy and never stored on the review, so
    there is no author field to leak; what is exposed is a display name and a
    verified badge, both derived at render time.
    """

    author_name = serializers.SerializerMethodField(
        help_text="'Former student' for an erased account (ADR-008)."
    )
    is_verified_author = serializers.SerializerMethodField(
        help_text=(
            "Whether the author carries the verification badge, read from "
            "their profile at render time. Absent verification is NOT a "
            "discredit -- most universities do not require it (ADR-003)."
        )
    )
    dispute_annotation = serializers.SerializerMethodField(help_text=DISPUTE_ANNOTATION)
    # Nullable, and declared so. Most reviews have no reply; a schema saying
    # otherwise generates a non-optional type and the first `.body` read
    # crashes on the common case.
    response = ReviewResponseSerializer(read_only=True, allow_null=True)
    unit_label = serializers.CharField(source="tenancy.unit.label", read_only=True)
    stay_months = serializers.SerializerMethodField(
        help_text="Length of the stay behind this review, in whole months."
    )

    class Meta:
        model = Review
        fields = (
            "id",
            "rating",
            "cleanliness_rating",
            "security_rating",
            "water_reliability_rating",
            "landlord_rating",
            "value_rating",
            "comment",
            "would_recommend",
            "created_at",
            "editable_until",
            "author_name",
            "is_verified_author",
            "dispute_annotation",
            "response",
            "unit_label",
            "stay_months",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.CharField())
    def get_author_name(self, review: Review) -> str:
        from accounts.privacy import display_name_for

        return display_name_for(review.tenancy.tenant)

    @extend_schema_field(serializers.BooleanField())
    def get_is_verified_author(self, review: Review) -> bool:
        from .services import review_is_verified

        return review_is_verified(review)

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_dispute_annotation(self, review: Review) -> str | None:
        """Read from a batch the view attached, never computed per row.

        ADR-004 §2.1: computing this per review is one query per row, so the
        view annotates the whole page in one pass and this reads the result.
        The fallback exists for a serializer used outside a list context, and
        is deliberately the slow path rather than a silent ``None`` -- a
        silently-absent annotation would look identical to "no dispute".
        """
        batch = self.context.get("dispute_annotations")
        if batch is not None:
            return batch.get(review.pk)

        from .services import review_dispute_annotation

        return review_dispute_annotation(review)

    @extend_schema_field(serializers.IntegerField())
    def get_stay_months(self, review: Review) -> int:
        from .services import stay_days

        return stay_days(review.tenancy) // 30


class ReviewWriteSerializer(serializers.ModelSerializer):
    """Creating or editing a review.

    ``tenancy`` is write-only and validated against the caller: a review is
    only ever about the caller's own stay, and accepting an arbitrary tenancy
    id would let anyone review anyone's.
    """

    class Meta:
        model = Review
        fields = (
            "tenancy",
            "rating",
            "cleanliness_rating",
            "security_rating",
            "water_reliability_rating",
            "landlord_rating",
            "value_rating",
            "comment",
            "would_recommend",
        )
        extra_kwargs = {
            "tenancy": {
                "write_only": True,
                "help_text": (
                    "The stay being reviewed. Must be one of your own, at "
                    "least REVIEW_MINIMUM_STAY_DAYS long, and not already "
                    "reviewed (ADR-004)."
                ),
            }
        }

    def __init__(self, *args, **kwargs):
        """Drop the implicit uniqueness validator on `tenancy`.

        `Review.tenancy` is a OneToOneField, so DRF attaches a UniqueValidator
        that fires before the view reaches the service layer -- returning a 400
        `validation_failed` where `create_review` would raise
        `TenancyNotReviewableError` and produce a 409 `not_reviewable`.

        Two paths to the same refusal with two different codes is precisely the
        inconsistency the error contract exists to prevent, and the service
        layer is the correctness boundary: it is what the admin, a management
        command and any future job go through. So the serializer stops
        pre-empting it.
        """
        super().__init__(*args, **kwargs)
        self.fields["tenancy"].validators = [
            validator
            for validator in self.fields["tenancy"].validators
            if not isinstance(validator, UniqueValidator)
        ]

    def validate_tenancy(self, tenancy):
        request = self.context["request"]
        if tenancy.tenant_id != request.user.pk:
            # Deliberately the same message as a missing tenancy: confirming
            # that someone else's tenancy exists is an enumeration oracle.
            raise serializers.ValidationError("No such stay.")
        return tenancy


class ReviewResponseWriteSerializer(serializers.ModelSerializer):
    """The landlord's reply. One per review, ever (ADR-004)."""

    class Meta:
        model = ReviewResponse
        fields = ("body",)
