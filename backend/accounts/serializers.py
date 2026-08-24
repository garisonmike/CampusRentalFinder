"""Serializers for the accounts app (ADR-003)."""

from __future__ import annotations

from typing import Any

from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from accounts.capabilities import Capabilities, capabilities_for

from .models import LandlordProfile, StudentProfile, UniversityStaffProfile, User


class CapabilitiesSerializer(serializers.Serializer):
    """What the caller may do (ADR-003).

    Sent explicitly so the client never re-derives authorization from raw model
    shapes. The previous frontend tried, guessed the field name wrong, and
    silently disabled its own navigation for every role.
    """

    is_student = serializers.BooleanField(read_only=True)
    is_landlord = serializers.BooleanField(read_only=True)
    is_university_staff = serializers.BooleanField(read_only=True)
    is_staff = serializers.BooleanField(read_only=True)
    is_verified_student = serializers.BooleanField(read_only=True)
    university = serializers.CharField(read_only=True, allow_null=True)
    manages_properties = serializers.ListField(child=serializers.IntegerField(), read_only=True)


class UserSerializer(serializers.ModelSerializer):
    """A user's own identity, with their capability set."""

    full_name = serializers.CharField(source="get_full_name", read_only=True)
    capabilities = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "phone_number",
            "phone_verified",
            "email_verified",
            "avatar_url",
            "is_active",
            "date_joined",
            "capabilities",
        ]
        read_only_fields = [
            "id",
            "email",
            "phone_verified",
            "email_verified",
            "is_active",
            "date_joined",
        ]

    @staticmethod
    def get_capabilities(obj: User) -> Capabilities:
        return capabilities_for(obj)


class UserUpdateSerializer(serializers.ModelSerializer):
    """The fields a user may change about themselves.

    Deliberately short. Nothing here grants any capability: that is the point
    of ADR-003, and a registration or profile payload must never be able to
    escalate.
    """

    class Meta:
        model = User
        fields = ["first_name", "last_name", "phone_number", "avatar_url"]


class UserRegistrationSerializer(serializers.ModelSerializer):
    """Create an account.

    No role field. The draft accepted ``user_type`` straight from the request
    body and never validated it, so anyone could register as an administrator
    and the object-permission checks trusted it (docs/AUDIT.md §4.4).

    When the request resolves a tenant, the new user gets a StudentProfile for
    that university — signing up on a university's own subdomain is the
    statement that you are its student. Landlord and staff profiles are granted,
    never self-declared.
    """

    password = serializers.CharField(
        write_only=True,
        min_length=8,
        style={"input_type": "password"},
    )
    password_confirm = serializers.CharField(write_only=True, style={"input_type": "password"})

    class Meta:
        model = User
        fields = [
            "email",
            "password",
            "password_confirm",
            "first_name",
            "last_name",
            "phone_number",
        ]
        extra_kwargs = {
            "first_name": {"required": True},
            "last_name": {"required": True},
            "email": {"required": True},
        }

    def validate_email(self, value: str) -> str:
        if User.objects.filter(email=value.lower()).exists():
            raise serializers.ValidationError(_("A user with that email already exists."))
        return value.lower()

    def validate_password(self, value: str) -> str:
        try:
            validate_password(value)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        return value

    def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        if data.get("password") != data.get("password_confirm"):
            raise serializers.ValidationError({"password_confirm": _("Passwords do not match.")})
        return data

    @transaction.atomic
    def create(self, validated_data: dict[str, Any]) -> User:
        validated_data.pop("password_confirm", None)
        password = validated_data.pop("password")

        user = User.objects.create_user(password=password, **validated_data)

        university = getattr(self.context.get("request"), "university", None)
        if university is not None:
            StudentProfile.all_objects.create(user=user, university=university)

        return user


class UserLoginSerializer(serializers.Serializer):
    """Exchange credentials for a user."""

    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={"input_type": "password"})

    def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        email = data.get("email", "").lower()
        password = data.get("password")

        if not (email and password):
            raise serializers.ValidationError(_("Must include email and password."))

        user = authenticate(request=self.context.get("request"), username=email, password=password)
        if user is None:
            raise serializers.ValidationError(_("Invalid email or password."))
        if not user.is_active:
            raise serializers.ValidationError(_("User account is disabled."))

        data["user"] = user
        return data


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)
    new_password_confirm = serializers.CharField(write_only=True)

    def validate_current_password(self, value: str) -> str:
        user = self.context["request"].user
        if not user.check_password(value):
            raise serializers.ValidationError(_("Your current password is incorrect."))
        return value

    def validate_new_password(self, value: str) -> str:
        try:
            validate_password(value, user=self.context["request"].user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError(exc.messages) from exc
        return value

    def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        if data.get("new_password") != data.get("new_password_confirm"):
            raise serializers.ValidationError(
                {"new_password_confirm": _("New passwords do not match.")}
            )
        return data

    def save(self, **kwargs: Any) -> User:
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password"])
        return user


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------


class LandlordProfileSerializer(serializers.ModelSerializer):
    """A landlord's own profile.

    ``national_id`` is write-only and ``id_document_key`` is never exposed:
    both are regulated personal data, and the document lives in the private
    bucket behind a signed URL (ADR-003, ADR-007).
    """

    class Meta:
        model = LandlordProfile
        fields = [
            "id",
            "business_name",
            "kra_pin",
            "national_id",
            "verification_status",
            "verified_at",
            "payout_phone",
        ]
        read_only_fields = ["id", "verification_status", "verified_at"]
        extra_kwargs = {"national_id": {"write_only": True}}


class StudentProfileSerializer(serializers.ModelSerializer):
    university: serializers.SlugRelatedField = serializers.SlugRelatedField(
        slug_field="subdomain", read_only=True
    )

    class Meta:
        model = StudentProfile
        fields = [
            "id",
            "university",
            "student_email",
            "verification_status",
            "verification_method",
            "verified_at",
            "year_of_study",
            "course",
        ]
        read_only_fields = [
            "id",
            "university",
            "verification_status",
            "verification_method",
            "verified_at",
        ]


class UniversityStaffProfileSerializer(serializers.ModelSerializer):
    university: serializers.SlugRelatedField = serializers.SlugRelatedField(
        slug_field="subdomain", read_only=True
    )

    class Meta:
        model = UniversityStaffProfile
        fields = ["id", "university", "job_title", "can_review_verifications", "is_active"]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Platform staff
# ---------------------------------------------------------------------------


class AdminUserSerializer(serializers.ModelSerializer):
    """User administration, for platform staff.

    ``is_staff`` stays read-only here too: elevating a user to platform staff
    is a Django admin or management-command action, never an API one.
    """

    full_name = serializers.CharField(source="get_full_name", read_only=True)
    capabilities = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "first_name",
            "last_name",
            "full_name",
            "phone_number",
            "is_active",
            "is_staff",
            "date_joined",
            "capabilities",
        ]
        read_only_fields = ["id", "email", "is_staff", "date_joined"]

    @staticmethod
    def get_capabilities(obj: User) -> Capabilities:
        return capabilities_for(obj)
