"""
The landlord and caretaker write surface (ADR-002, ADR-003).

Split from `views.py` because the two halves answer different questions. That
file is "what may anybody see"; this one is "who may change what", and mixing
them is how a read endpoint quietly acquires a write method with the read
endpoint's permissions.

**Every authority here is checked at the endpoint.** The service functions
enforce the rules that span rows, and the permission classes enforce who may
call them, and neither substitutes for the other: a caretaker prohibition
tested only in the service layer is a prohibition that says nothing about the
URL somebody can POST to.

The caretaker line is drawn per action, from `CaretakerPermission`:

- **units** — a caretaker with `manage_units` may add and edit rooms;
- **vacancy** — its own permission, because the count is what a student
  crosses a city on;
- **availability** — separately delegable: trusted to say a room is off the
  market without being trusted to change its rent;
- **photos** — its own permission;
- **the property itself, and publishing** — landlord only. Neither is in
  `CaretakerPermission` at all, and publishing in particular is the act that
  makes a building public under the owner's name.
"""

from __future__ import annotations

from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import (
    CanManagePhotos,
    CanManageUnits,
    CanManageVacancy,
    CanSetAvailability,
    IsLandlord,
    IsPropertyOwner,
)
from accounts.privacy import display_name_for
from config.api.throttling import Scope

from .models import Property, Unit, UnitPhoto
from .serializers import PropertyDetailSerializer, UnitSummarySerializer
from .services import (
    add_photo,
    create_property,
    create_unit,
    delete_photo,
    publish,
    reorder_photos,
    set_availability,
    state_vacancy,
    unpublish,
    update_property,
    update_unit,
)
from .write_serializers import (
    AvailabilitySerializer,
    PhotoOrderSerializer,
    PhotoSerializer,
    PhotoUploadSerializer,
    PropertyWriteSerializer,
    UnitWriteSerializer,
    VacancyResultSerializer,
    VacancySerializer,
)


class ManagedPropertyMixin:
    """Resolve the property by slug and run the object permission on it.

    **Unscoped by tenant, deliberately.** Every read endpoint filters by the
    resolved university, because that join is what makes a listing visible.
    Management is the other way round: a landlord editing their own draft is
    not browsing a catalogue, and a draft has no campus join yet -- so scoping
    this by host would make a new property uneditable from the moment it is
    created until the moment it is pinned. Authorization here is the ownership
    relation, which is stronger than the tenant filter rather than weaker.
    """

    def get_property(self, request, slug: str) -> Property:
        prop = get_object_or_404(Property.all_objects.select_related("landlord"), slug=slug)
        self.check_object_permissions(request, prop)  # type: ignore[attr-defined]
        return prop

    def get_unit(self, request, slug: str, pk: int) -> Unit:
        unit = get_object_or_404(
            Unit.all_objects.select_related("property__landlord"),
            pk=pk,
            property__slug=slug,
        )
        self.check_object_permissions(request, unit)  # type: ignore[attr-defined]
        return unit


@extend_schema_view(
    post=extend_schema(
        summary="Create a property",
        description=(
            "Always created as a **draft**, whatever the payload says. "
            "Publishing has a gate -- a property with no coordinates cannot "
            "join a campus and would be invisible to every student -- so "
            "creating and publishing are two steps, one of which can refuse.\n\n"
            "Landlord only. Creating a building is not delegable."
        ),
        request=PropertyWriteSerializer,
        responses={201: PropertyDetailSerializer},
    )
)
class PropertyCreateView(APIView):
    """Add a property."""

    permission_classes = [IsAuthenticated, IsLandlord]
    throttle_scope = Scope.WRITE

    def post(self, request):
        serializer = PropertyWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        prop = create_property(landlord=request.user.landlord_profile, **serializer.validated_data)

        return Response(PropertyDetailSerializer(prop).data, status=201)


@extend_schema_view(
    patch=extend_schema(
        summary="Edit a property",
        description=(
            "Landlord only -- editing the building's own details is not in "
            "`CaretakerPermission`.\n\n"
            "The slug follows the name only while the property is a draft. "
            "Once published the URL is in somebody's saved list and in "
            "messages the landlord has already sent, so renaming a live "
            "listing changes what it is called and not where it lives."
        ),
        request=PropertyWriteSerializer,
        responses=PropertyDetailSerializer,
    )
)
class PropertyUpdateView(ManagedPropertyMixin, APIView):
    """Edit a property."""

    permission_classes = [IsAuthenticated, IsPropertyOwner]
    throttle_scope = Scope.WRITE

    def patch(self, request, slug: str):
        prop = self.get_property(request, slug)

        serializer = PropertyWriteSerializer(prop, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        return Response(
            PropertyDetailSerializer(update_property(prop, **serializer.validated_data)).data
        )


@extend_schema_view(
    post=extend_schema(
        summary="Publish a property",
        description=(
            "**The coordinates gate runs here.** A property with no latitude "
            "and longitude cannot be joined to a campus, and the join is what "
            "makes a listing visible to a university (ADR-002) -- so "
            "publishing one produces a listing the landlord can see and "
            "nobody else can. That failure looks exactly like low demand, "
            "which is why this refuses with a named reason instead.\n\n"
            "Landlord only. Publishing puts a building on the internet under "
            "the owner's name."
        ),
        request=None,
        responses={200: PropertyDetailSerializer},
    ),
    delete=extend_schema(
        summary="Take a property off the site",
        description=(
            "Back to draft, never deleted. Tenancies, claims and reviews point "
            "at this property and are other people's records."
        ),
        responses={200: PropertyDetailSerializer},
    ),
)
class PropertyPublicationView(ManagedPropertyMixin, APIView):
    """Publish or unpublish."""

    permission_classes = [IsAuthenticated, IsPropertyOwner]
    throttle_scope = Scope.WRITE

    def post(self, request, slug: str):
        prop = self.get_property(request, slug)
        return Response(PropertyDetailSerializer(publish(prop)).data)

    def delete(self, request, slug: str):
        prop = self.get_property(request, slug)
        return Response(PropertyDetailSerializer(unpublish(prop)).data)


@extend_schema_view(
    post=extend_schema(
        summary="Add a unit",
        description=(
            "A pool of identical rooms is **one** unit with a `total_count`, "
            "not forty units. `vacant_count` is not settable here: a new unit "
            "starts with nobody having stated anything, which is what "
            "`vacancy_freshness: unknown` means."
        ),
        request=UnitWriteSerializer,
        responses={201: UnitSummarySerializer},
    )
)
class UnitCreateView(ManagedPropertyMixin, APIView):
    """Add a unit to a property."""

    permission_classes = [IsAuthenticated, CanManageUnits]
    throttle_scope = Scope.WRITE

    def post(self, request, slug: str):
        prop = self.get_property(request, slug)

        serializer = UnitWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        unit = create_unit(property_obj=prop, **serializer.validated_data)
        return Response(UnitSummarySerializer(unit).data, status=201)


@extend_schema_view(
    patch=extend_schema(
        summary="Edit a unit",
        description=(
            "**`vacant_count` is refused here**, not ignored. It has one write "
            "path, which stamps the count with who stated it and when; a unit "
            "edit that could also set the number would leave a fresh count "
            "wearing an old date, and the staleness signal would then claim "
            "currency."
        ),
        request=UnitWriteSerializer,
        responses=UnitSummarySerializer,
    )
)
class UnitUpdateView(ManagedPropertyMixin, APIView):
    """Edit a unit."""

    permission_classes = [IsAuthenticated, CanManageUnits]
    throttle_scope = Scope.WRITE

    def patch(self, request, slug: str, pk: int):
        unit = self.get_unit(request, slug, pk)

        serializer = UnitWriteSerializer(unit, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        fields = dict(serializer.validated_data)
        # Passed through rather than filtered out, so the service refuses it
        # by name. A silently dropped field is how somebody comes to believe
        # they set something they did not.
        if "vacant_count" in request.data:
            fields["vacant_count"] = request.data["vacant_count"]

        return Response(UnitSummarySerializer(update_unit(unit, **fields)).data)


@extend_schema_view(
    patch=extend_schema(
        summary="State how many rooms are free",
        description=(
            "**The only way to write `vacant_count`.** Every write stamps the "
            "time and the author together, and the listing shows that stamp "
            "beside the number -- which is what lets a student tell 'six free, "
            "confirmed yesterday' from 'six free, nobody has said since "
            "March'.\n\n"
            "The count is never derived from our tenancy records and never "
            "overwritten by them: you know about the room let off-platform "
            "last week and we do not.\n\n"
            "Delegable to a caretaker as `manage_vacancy`, separately from "
            "everything else, because this is the number people travel on."
        ),
        request=VacancySerializer,
        responses=VacancyResultSerializer,
    )
)
class UnitVacancyView(ManagedPropertyMixin, APIView):
    """Restate a unit's vacancy."""

    permission_classes = [IsAuthenticated, CanManageVacancy]
    throttle_scope = Scope.WRITE

    def patch(self, request, slug: str, pk: int):
        unit = self.get_unit(request, slug, pk)

        serializer = VacancySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        unit = state_vacancy(
            unit,
            vacant_count=serializer.validated_data["vacant_count"],
            stated_by=request.user,
        )

        from .services import vacancy_age_days, vacancy_freshness

        return Response(
            VacancyResultSerializer(
                {
                    "id": unit.pk,
                    "vacant_count": unit.vacant_count,
                    "total_count": unit.total_count,
                    "vacancy_freshness": vacancy_freshness(unit),
                    "vacancy_age_days": vacancy_age_days(unit),
                    "vacant_count_updated_at": unit.vacant_count_updated_at,
                    # Read back from the row rather than from the request:
                    # what is returned is what was actually written.
                    "vacant_count_updated_by_name": (
                        display_name_for(unit.vacant_count_updated_by)
                        if unit.vacant_count_updated_by is not None
                        else ""
                    ),
                }
            ).data
        )


@extend_schema_view(
    patch=extend_schema(
        summary="Set a unit's availability",
        description=(
            "Separately delegable from editing the unit: a caretaker may be "
            "trusted to say a room is off the market without being trusted to "
            "change its rent.\n\n"
            "`is_active: false` hides a unit without deleting it. Prefer it — "
            "tenancy records point at units, and a deleted unit takes "
            "somebody's rental history with it."
        ),
        request=AvailabilitySerializer,
        responses=UnitSummarySerializer,
    )
)
class UnitAvailabilityView(ManagedPropertyMixin, APIView):
    """Set availability."""

    permission_classes = [IsAuthenticated, CanSetAvailability]
    throttle_scope = Scope.WRITE

    def patch(self, request, slug: str, pk: int):
        unit = self.get_unit(request, slug, pk)

        serializer = AvailabilitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        unit = set_availability(unit, **serializer.validated_data)
        return Response(UnitSummarySerializer(unit).data)


@extend_schema_view(
    get=extend_schema(
        summary="Photos on a unit, as its manager sees them",
        description=(
            "Includes `processing_status` and `processing_error`, which the "
            "public serializer does not: a landlord whose photo failed to "
            "resize is owed the reason, and a student is not."
        ),
        responses=PhotoSerializer(many=True),
    ),
    post=extend_schema(
        summary="Upload a photo",
        description=(
            "Stored immediately and resized in the background (ADR-007), so a "
            "fresh photo has no variants and the API serves the original. The "
            "first photo on a unit becomes its cover.\n\n"
            "JPEG, PNG or WebP, checked by content type rather than by "
            "filename -- an extension is whatever the client typed, and the "
            "resize step is a decoder pointed at whatever arrives."
        ),
        request=PhotoUploadSerializer,
        responses={201: PhotoSerializer},
    ),
)
class UnitPhotoListView(ManagedPropertyMixin, APIView):
    """List and upload photos."""

    permission_classes = [IsAuthenticated, CanManagePhotos]
    throttle_scope = Scope.WRITE

    def get(self, request, slug: str, pk: int):
        unit = self.get_unit(request, slug, pk)
        photos = UnitPhoto.all_objects.filter(unit=unit).order_by("sort_order", "created_at")
        return Response(PhotoSerializer(photos, many=True).data)

    def post(self, request, slug: str, pk: int):
        unit = self.get_unit(request, slug, pk)

        serializer = PhotoUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        photo = add_photo(
            unit=unit,
            upload=serializer.validated_data["image"],
            caption=serializer.validated_data.get("caption", ""),
            uploaded_by=request.user,
        )
        return Response(PhotoSerializer(photo).data, status=201)


@extend_schema_view(
    put=extend_schema(
        summary="Reorder a unit's photos",
        description=(
            "Send **every** photo id on the unit, in the order you want. A "
            "partial list is refused: it means the page you are ordering from "
            "is stale, and applying it would silently drop the photos it does "
            "not mention.\n\n"
            "The first becomes the cover."
        ),
        request=PhotoOrderSerializer,
        responses=PhotoSerializer(many=True),
    )
)
class UnitPhotoOrderView(ManagedPropertyMixin, APIView):
    """Set photo order."""

    permission_classes = [IsAuthenticated, CanManagePhotos]
    throttle_scope = Scope.WRITE

    def put(self, request, slug: str, pk: int):
        unit = self.get_unit(request, slug, pk)

        serializer = PhotoOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        photos = reorder_photos(unit=unit, ordered_ids=serializer.validated_data["photo_ids"])
        return Response(PhotoSerializer(photos, many=True).data)


@extend_schema_view(
    delete=extend_schema(
        summary="Delete a photo",
        description=(
            "Removes the row. The stored object is left in the bucket -- a "
            "delete that also removed the file could not be undone by an "
            "operator, and a landlord who deleted the wrong photo of a room "
            "they no longer have access to has lost it for good.\n\n"
            "Deleting the cover promotes the next photo."
        ),
        responses={204: None},
    )
)
class UnitPhotoDetailView(ManagedPropertyMixin, APIView):
    """Delete one photo."""

    permission_classes = [IsAuthenticated, CanManagePhotos]
    throttle_scope = Scope.WRITE

    def delete(self, request, slug: str, pk: int, photo_id: int):
        unit = self.get_unit(request, slug, pk)

        photo = UnitPhoto.all_objects.filter(unit=unit, pk=photo_id).first()
        if photo is None:
            raise NotFound("No such photo on this unit.")

        delete_photo(photo)
        return Response(status=204)


@extend_schema_view(
    get=extend_schema(
        summary="Properties you manage",
        description=(
            "Owned properties for a landlord, assigned ones for a caretaker, "
            "**including drafts** -- which is the difference from the public "
            "listing endpoint. This is where an unpublished property lives "
            "until it is pinned."
        ),
        responses=PropertyDetailSerializer(many=True),
    )
)
class ManagedPropertyListView(APIView):
    """Everything the caller may manage."""

    permission_classes = [IsAuthenticated]
    throttle_scope = Scope.AUTHENTICATED_READ

    def get(self, request):
        from accounts.capabilities import managed_property_ids

        # Owned OR assigned, combined here rather than inside
        # `managed_property_ids` -- that helper means "as a caretaker" on
        # purpose, and conflating the two would let an assignment's permission
        # subset restrict the person who granted it.
        owned = Q(landlord__user=request.user)
        assigned = Q(pk__in=managed_property_ids(request.user))

        queryset = (
            Property.all_objects.filter(owned | assigned)
            .distinct()
            .select_related("landlord__user")
            .prefetch_related(
                Prefetch("units", queryset=Unit.all_objects.order_by("label")),
                # Both relations, not just the rows. `CampusDistanceSerializer`
                # renders `campus_name` and `university_name`, so prefetching
                # the distances alone leaves two queries per distance row --
                # eighteen queries for six properties, which fixtures with one
                # campus each could never show. The public detail view already
                # does this; the management view did not.
                "campus_distances__campus",
                "campus_distances__university",
            )
            .order_by("name")
        )

        return Response(PropertyDetailSerializer(queryset, many=True).data)
