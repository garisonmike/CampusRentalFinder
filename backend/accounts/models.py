"""
Identity and roles (ADR-003).

``User`` holds identity and authentication **only**. Capability lives in
separate models that describe relationships, because a role is not a global
fact about a person — it is a fact about their relationship to a university or
to particular properties.

The field this replaces, ``user_type``, was one string doing the work of an
authorization model. It was client-supplied at registration and never
validated, and the object-permission checks in the draft apps trusted it, so
anyone could register as an ``admin`` and edit or delete any listing or review
on the platform (docs/AUDIT.md §4.4).
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from accounts.capabilities import CaretakerPermission
from config.tenancy import TenantScopedModel
from universities.constants import SignupPolicy, VerificationMethod, VerificationStatus
from universities.models import University

#: Kenyan mobile numbers in E.164. The draft's regex was `^\+?1?\d{9,15}$` —
#: the optional 1 is a North American country code (docs/AUDIT.md §3).
kenyan_phone_validator = RegexValidator(
    regex=r"^\+254[17]\d{8}$",
    message=_("Enter a Kenyan number in international format, e.g. +254712345678."),
)

kra_pin_validator = RegexValidator(
    regex=r"^[AP]\d{9}[A-Z]$",
    message=_("A KRA PIN looks like A123456789Z."),
)


class UserManager(BaseUserManager["User"]):
    """Creates users keyed by email.

    The draft inherited Django's ``UserManager``, which demands a username even
    though ``USERNAME_FIELD`` was already ``email`` — so ``create_user()`` and
    ``createsuperuser`` both revolved around a vestigial column.
    """

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra: Any) -> User:
        if not email:
            raise ValueError("Users must have an email address.")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra: Any) -> User:
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra: Any) -> User:
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if extra.get("is_staff") is not True:
            raise ValueError("A superuser must have is_staff=True.")
        if extra.get("is_superuser") is not True:
            raise ValueError("A superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """Identity and authentication. No roles.

    ``is_staff`` is the **only** flag meaning platform administrator, and it is
    settable through the Django admin or a management command, never through
    the API.
    """

    email = models.EmailField(
        _("email address"),
        unique=True,
        error_messages={"unique": _("A user with that email already exists.")},
    )
    first_name = models.CharField(_("first name"), max_length=100)
    last_name = models.CharField(_("last name"), max_length=100)
    phone_number = models.CharField(
        _("phone number"),
        max_length=13,
        blank=True,
        validators=[kenyan_phone_validator],
    )
    phone_verified = models.BooleanField(_("phone verified"), default=False)
    email_verified = models.BooleanField(_("email verified"), default=False)
    avatar_url = models.URLField(_("avatar URL"), blank=True)

    is_active = models.BooleanField(_("active"), default=True)
    is_staff = models.BooleanField(
        _("platform staff"),
        default=False,
        help_text=_("The only meaning of 'platform administrator'. Not settable via the API."),
    )

    date_joined = models.DateTimeField(_("date joined"), default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "last_name"]

    class Meta:
        verbose_name = _("User")
        verbose_name_plural = _("Users")
        ordering = ["-date_joined"]
        indexes = [
            models.Index(fields=["email"], name="user_email_idx"),
            models.Index(fields=["-date_joined"], name="user_joined_idx"),
        ]
        constraints = [
            # Unique when set, but many users may leave it blank.
            models.UniqueConstraint(
                fields=["phone_number"],
                condition=~Q(phone_number=""),
                name="user_phone_unique_when_set",
            ),
        ]

    def __str__(self) -> str:
        return self.get_full_name() or self.email

    def save(self, *args: Any, **kwargs: Any) -> None:
        if self.email:
            self.email = self.email.lower()
        super().save(*args, **kwargs)

    def get_full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def get_short_name(self) -> str:
        return self.first_name or self.email.split("@")[0]


class LandlordProfile(models.Model):
    """A user who may own properties.

    Not tenant-scoped: a landlord near two campuses serves both universities,
    which is the whole reason ADR-002 exists.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="landlord_profile")
    business_name = models.CharField(_("business name"), max_length=200, blank=True)
    kra_pin = models.CharField(
        _("KRA PIN"), max_length=11, blank=True, validators=[kra_pin_validator]
    )
    national_id = models.CharField(
        _("national ID"),
        max_length=20,
        blank=True,
        help_text=_("Write-only in the API. Never returned in a response."),
    )
    id_document_key = models.CharField(
        _("ID document key"),
        max_length=500,
        blank=True,
        help_text=_("Key in the PRIVATE documents bucket. Never the public CDN (ADR-007)."),
    )
    verification_status = models.CharField(
        _("verification status"),
        max_length=16,
        choices=VerificationStatus.choices,
        default=VerificationStatus.UNVERIFIED,
    )
    verified_at = models.DateTimeField(_("verified at"), null=True, blank=True)
    verified_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="landlord_verifications_performed",
    )
    payout_phone = models.CharField(
        _("payout phone"),
        max_length=13,
        blank=True,
        validators=[kenyan_phone_validator],
        help_text=_("M-Pesa number. Reserved for when payments ship."),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Landlord profile")
        verbose_name_plural = _("Landlord profiles")
        indexes = [
            models.Index(fields=["verification_status"], name="landlord_verif_status_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(verification_status=VerificationStatus.VERIFIED)
                | Q(verified_at__isnull=False),
                name="landlord_verified_has_timestamp",
            ),
        ]

    def __str__(self) -> str:
        return self.business_name or self.user.get_full_name()


class StudentProfile(TenantScopedModel):
    """A user who belongs to an institution.

    Verification is optional by default and earns a badge rather than gating
    access (ADR-003). Its mechanism is per-university policy.
    """

    tenant_lookup = "university"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="student_profile")
    university = models.ForeignKey(University, on_delete=models.PROTECT, related_name="students")
    student_email = models.EmailField(
        _("student email"),
        blank=True,
        help_text=_("Must match one of the university's student email domains."),
    )
    verification_status = models.CharField(
        _("verification status"),
        max_length=16,
        choices=VerificationStatus.choices,
        default=VerificationStatus.UNVERIFIED,
    )
    verification_method = models.CharField(
        _("verification method"),
        max_length=24,
        choices=VerificationMethod.choices,
        blank=True,
        help_text=_("Blank until verification succeeds."),
    )
    verified_at = models.DateTimeField(_("verified at"), null=True, blank=True)
    verified_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student_verifications_performed",
        help_text=_("Null for the automated email-domain path."),
    )
    rejection_reason = models.CharField(_("rejection reason"), max_length=255, blank=True)
    #: The policy in force WHEN THIS STUDENT REGISTERED, frozen here.
    #:
    #: Gating reads this, never the university's current value. Without it,
    #: a school raising its policy blocks every existing unverified student
    #: instantly -- and blocks them with "your grace period expired", when
    #: they never had one, because nobody told them anything was expected.
    #: ADR-003 says policy changes apply to new signups only; this field is
    #: what makes that true rather than aspirational.
    signup_policy_at_registration = models.CharField(
        _("signup policy at registration"),
        max_length=24,
        choices=SignupPolicy.choices,
        default=SignupPolicy.OPEN,
    )
    #: As above: whether reviews were gated when this student registered.
    review_gated_at_registration = models.BooleanField(
        _("reviews gated at registration"), default=False
    )
    grace_period_ends_at = models.DateTimeField(
        _("grace period ends"),
        null=True,
        blank=True,
        help_text=_(
            "Set at signup when the university gates actions on verification. "
            "Read access never depends on it (ADR-003)."
        ),
    )
    year_of_study = models.PositiveSmallIntegerField(_("year of study"), null=True, blank=True)
    course = models.CharField(_("course"), max_length=200, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Student profile")
        verbose_name_plural = _("Student profiles")
        base_manager_name = "all_objects"
        default_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["university", "verification_status"], name="student_verif_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["student_email"],
                condition=~Q(student_email=""),
                name="student_email_unique_when_set",
            ),
            # A verified profile must say how, or the badge means nothing and
            # a later audit cannot tell the two paths apart.
            models.CheckConstraint(
                condition=~Q(verification_status=VerificationStatus.VERIFIED)
                | ~Q(verification_method=""),
                name="student_verified_has_a_method",
            ),
            models.CheckConstraint(
                condition=~Q(verification_status=VerificationStatus.REJECTED)
                | ~Q(rejection_reason=""),
                name="student_rejected_has_a_reason",
            ),
            models.CheckConstraint(
                condition=Q(year_of_study__isnull=True)
                | (Q(year_of_study__gte=1) & Q(year_of_study__lte=8)),
                name="student_year_of_study_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user.get_full_name()} ({self.university.display_name})"

    @property
    def is_verified(self) -> bool:
        return self.verification_status == VerificationStatus.VERIFIED


class UniversityStaffProfile(TenantScopedModel):
    """A member of university staff, scoped to one institution.

    Its only capability today is that tenant's student verification queue. It
    can read student ID documents for its own university, which widens the
    blast radius of a compromised account, so the scope is deliberately narrow
    and every document read is logged.
    """

    tenant_lookup = "university"

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="staff_profile")
    university = models.ForeignKey(University, on_delete=models.PROTECT, related_name="staff")
    job_title = models.CharField(_("job title"), max_length=120, blank=True)
    can_review_verifications = models.BooleanField(_("can review verifications"), default=True)
    is_active = models.BooleanField(_("active"), default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("University staff profile")
        verbose_name_plural = _("University staff profiles")
        base_manager_name = "all_objects"
        default_manager_name = "all_objects"
        indexes = [
            models.Index(fields=["university", "is_active"], name="unistaff_active_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user.get_full_name()} — {self.university.display_name}"


class CaretakerAssignment(TenantScopedModel):
    """A user authorised to manage ONE property, granted by its landlord.

    Scoped to the property, not to the person: "caretaker" is not a global fact
    about someone, it is a fact about their relationship to a particular
    building. A fourth ``user_type`` string would have given every caretaker
    authority over every property (ADR-003).

    Revocation is a flag, never a delete, so the grant history survives.
    """

    tenant_lookup = "property__campus_distances__university"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="caretaker_assignments")
    property = models.ForeignKey(
        "properties.Property", on_delete=models.CASCADE, related_name="caretaker_assignments"
    )
    granted_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="caretaker_assignments_granted",
        help_text=_("The landlord. PROTECT so the audit trail cannot be deleted away."),
    )
    permissions = ArrayField(
        models.CharField(max_length=32, choices=CaretakerPermission.choices),
        verbose_name=_("permissions"),
        default=list,
        blank=True,
        help_text=_(
            "A subset of CaretakerPermission. Values outside it are rejected on "
            "write, so the list cannot drift from the code that checks it."
        ),
    )

    is_active = models.BooleanField(_("active"), default=True)
    granted_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(_("revoked at"), null=True, blank=True)
    revoked_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="caretaker_assignments_revoked",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Caretaker assignment")
        verbose_name_plural = _("Caretaker assignments")
        ordering = ["-granted_at"]
        base_manager_name = "all_objects"
        default_manager_name = "all_objects"
        indexes = [
            # The object-permission check, on every write to a property.
            models.Index(fields=["property", "is_active"], name="caretaker_property_idx"),
            models.Index(fields=["user", "is_active"], name="caretaker_user_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "property"],
                condition=Q(is_active=True),
                name="caretaker_one_active_assignment",
            ),
            models.CheckConstraint(
                condition=Q(is_active=True) | Q(revoked_at__isnull=False),
                name="caretaker_revoked_has_timestamp",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user.get_full_name()} → {self.property.name}"

    def clean(self) -> None:
        """Reject permissions outside the delegable set.

        ADR-003 fixes what a landlord may hand over. A value not in
        CaretakerPermission is either a typo or an attempt to grant something
        that is never delegable, and both should fail loudly.
        """
        super().clean()
        allowed = set(CaretakerPermission.values)
        unknown = sorted(set(self.permissions) - allowed)
        if unknown:
            raise ValidationError(
                {
                    "permissions": _(
                        "Not delegable to a caretaker: %(values)s. Allowed: %(allowed)s."
                    )
                    % {"values": ", ".join(unknown), "allowed": ", ".join(sorted(allowed))}
                }
            )

    def save(self, *args: Any, **kwargs: Any) -> None:
        self.full_clean(exclude=[f.name for f in self._meta.fields if f.name != "permissions"])
        super().save(*args, **kwargs)

    def has_permission(self, permission: str) -> bool:
        """Whether this assignment grants ``permission`` right now."""
        return self.is_active and permission in self.permissions


# Django discovers models through `models`. EmailVerificationToken lives beside
# the logic that issues and consumes it, because reading either without the
# other is how single-use tokens quietly stop being single-use.
from .documents import (  # noqa: E402,F401
    DocumentAccessLog,
    VerificationDocument,
    VerificationRequest,
)
from .verification import EmailVerificationToken  # noqa: E402,F401
