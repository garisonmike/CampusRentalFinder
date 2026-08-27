"""
Review, response and rating endpoints (ADR-004).

Reading reviews is public — they are the product, and a trust signal behind a
login persuades nobody. Writing one requires a confirmed tenancy of your own,
which the service layer enforces and this layer never second-guesses.

**The dispute annotation is batched.** Deriving it per review is one query per
row, and the annotation is derived rather than stored precisely so the policy
can change without a migration over live reviews. That trade only holds if
deriving it is cheap, so the list view annotates a whole page in one pass and
a query-count test asserts one review and fifty cost the same.
"""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.exceptions import NotFound
from rest_framework.generics import ListAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsPropertyOwner
from config.api.throttling import Scope
from config.middleware import get_current_university
from properties.constants import PropertyStatus
from properties.models import Property, Unit

from .aggregates import (
    LandlordRatingAggregate,
    PropertyRatingAggregate,
    UnitRatingAggregate,
)
from .models import Review
from .serializers import (
    LandlordRatingSerializer,
    RatingAggregateSerializer,
    ReviewResponseWriteSerializer,
    ReviewSerializer,
    ReviewWriteSerializer,
)
from .services import (
    create_review,
    dispute_annotations_for,
    respond_to_review,
    update_review,
)


def _tenant():
    university = get_current_university()
    if university is None:
        raise NotFound("No university is configured for this host.")
    return university


def _visible_property(slug: str) -> Property:
    """A published property in the current tenant, or 404."""
    return get_object_or_404(
        Property.objects.for_tenant(_tenant()).filter(status=PropertyStatus.PUBLISHED),
        slug=slug,
    )


# ---------------------------------------------------------------------------
# Ratings
# ---------------------------------------------------------------------------


EMPTY_AGGREGATE = {
    "average_rating": None,
    "student_count": 0,
    "review_count": 0,
    "rating_distribution": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
    "last_review_at": None,
    "computed_at": None,
}


@extend_schema_view(
    get=extend_schema(
        summary="Rating for one property",
        description=(
            "`average_rating: null` means **no verified reviews yet** and must "
            "render as those words -- never 0, never an empty star row.\n\n"
            "`student_count` is the public denominator ('from N students'). It "
            "is deliberately smaller than `review_count` whenever anyone "
            "reviewed more than one stay in the same property; that divergence "
            "IS the de-duplication (ADR-004).\n\n"
            "A property with no reviews of its own may show `landlord` as a "
            "secondary signal, **labelled as being about the landlord**, never "
            "about this property."
        ),
    )
)
class PropertyRatingView(APIView):
    """The three figures a listing page shows."""

    permission_classes = [AllowAny]
    throttle_scope = Scope.PUBLIC_READ

    def get(self, request, slug: str):
        prop = _visible_property(slug)

        aggregate = PropertyRatingAggregate.all_objects.filter(property_reviewed=prop).first()
        landlord = LandlordRatingAggregate.objects.filter(landlord=prop.landlord).first()

        return Response(
            {
                "property": RatingAggregateSerializer(aggregate).data
                if aggregate
                else EMPTY_AGGREGATE,
                # Deliberately a separate key rather than a fallback value: a
                # landlord's record is not this property's rating, and merging
                # them would be the platform quietly answering a question
                # nobody asked.
                "landlord": LandlordRatingSerializer(landlord).data
                if landlord
                else {**EMPTY_AGGREGATE, "property_count": 0},
            }
        )


@extend_schema_view(
    get=extend_schema(
        summary="Rating for one unit",
        description=(
            "Unit ratings are NOT de-duplicated per student, unlike property "
            "ratings: one stay is one review, and a tenant cannot hold "
            "overlapping stays in the same unit, so there is nothing to "
            "collapse. `student_count` and `review_count` differ here only "
            "when someone genuinely returned to the same room years later."
        ),
    )
)
class UnitRatingView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = Scope.PUBLIC_READ

    def get(self, request, pk: int):
        unit = get_object_or_404(
            Unit.objects.for_tenant(_tenant()).filter(property__status=PropertyStatus.PUBLISHED),
            pk=pk,
        )
        aggregate = UnitRatingAggregate.all_objects.filter(unit=unit).first()

        return Response(RatingAggregateSerializer(aggregate).data if aggregate else EMPTY_AGGREGATE)


# ---------------------------------------------------------------------------
# Reviews
# ---------------------------------------------------------------------------


@extend_schema_view(
    get=extend_schema(
        summary="Reviews of one property",
        description=(
            "`dispute_annotation` is neutral and must be rendered as a plain "
            "factual line. Never grey the review out, collapse it, badge it "
            "amber or exclude it from the average -- styling it as a warning "
            "restores the veto ADR-004 removed."
        ),
    )
)
class PropertyReviewListView(ListAPIView):
    """Published reviews of one property, newest first."""

    serializer_class = ReviewSerializer
    permission_classes = [AllowAny]
    throttle_scope = Scope.PUBLIC_READ

    def get_queryset(self):
        prop = _visible_property(self.kwargs["slug"])
        return (
            Review.objects.for_tenant(_tenant())
            .filter(is_published=True, tenancy__unit__property=prop)
            .select_related("tenancy__unit", "tenancy__tenant__student_profile", "response__author")
            .order_by("-created_at")
        )

    def get_serializer_context(self):
        """Attach the page's annotations in one pass.

        `paginate_queryset` has already sliced the page by the time the
        serializer is built, so this batches exactly what is about to be
        rendered rather than the whole queryset.
        """
        context = super().get_serializer_context()
        page = getattr(self, "_annotated_page", None)
        if page is not None:
            context["dispute_annotations"] = dispute_annotations_for(page)
        return context

    def paginate_queryset(self, queryset):
        page = super().paginate_queryset(queryset)
        self._annotated_page = page if page is not None else list(queryset)
        return page


@extend_schema_view(
    post=extend_schema(
        summary="Write a review",
        description=(
            "Requires a confirmed tenancy of your own, at least "
            "REVIEW_MINIMUM_STAY_DAYS long and not already reviewed. A week in "
            "a room tells you about the viewing; the water going off every "
            "third Thursday takes a month to notice (ADR-004)."
        ),
    )
)
class ReviewCreateView(APIView):
    """Create a review against one of the caller's own stays."""

    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.WRITE

    def post(self, request):
        serializer = ReviewWriteSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        # Straight through to the service function. Every rule -- the minimum
        # stay, the already-reviewed check, the verification gate -- lives
        # there, so the admin and any future job go through the same gate.
        review = create_review(**serializer.validated_data)

        return Response(ReviewSerializer(review).data, status=201)


@extend_schema_view(
    patch=extend_schema(
        summary="Edit your review, inside its window",
        description=(
            "Editable for REVIEW_EDIT_WINDOW_DAYS after posting, then frozen. "
            "A review that can be rewritten for ever can be rewritten under "
            "pressure, and the pressure would come from the party with more of "
            "it (ADR-004)."
        ),
    )
)
class ReviewEditView(APIView):
    """Edit a review the caller wrote."""

    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.WRITE

    def patch(self, request, pk: int):
        review = get_object_or_404(Review.all_objects, pk=pk)

        if review.tenancy.tenant_id != request.user.pk:
            # Same message as a missing review: confirming somebody else's
            # exists is an enumeration oracle.
            raise NotFound("No such review.")

        serializer = ReviewWriteSerializer(
            review, data=request.data, partial=True, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        fields = {
            key: value for key, value in serializer.validated_data.items() if key != "tenancy"
        }

        return Response(ReviewSerializer(update_review(review, **fields)).data)


@extend_schema_view(
    post=extend_schema(
        summary="Respond to a review, once",
        description=(
            "The landlord's single public reply. Never a caretaker's: a "
            "caretaker can confirm that somebody lived somewhere, but speaking "
            "for the business in public is the owner's own act (ADR-003)."
        ),
    )
)
class ReviewResponseCreateView(APIView):
    """One response per review, ever."""

    permission_classes = [IsAuthenticated, IsPropertyOwner]
    throttle_scope = Scope.WRITE

    def post(self, request, pk: int):
        review = get_object_or_404(
            Review.objects.for_tenant(_tenant()).select_related(
                "tenancy__unit__property__landlord"
            ),
            pk=pk,
        )
        self.check_object_permissions(request, review.tenancy.unit.property)

        serializer = ReviewResponseWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        response = respond_to_review(
            review, author=request.user, body=serializer.validated_data["body"]
        )

        from .serializers import ReviewResponseSerializer

        return Response(ReviewResponseSerializer(response).data, status=201)
