"""
Tenant models (ADR-001), theming tokens (ADR-005) and verification policy
(ADR-003).

``University`` is the tenant. It is not itself tenant-scoped — it *is* the
scope — so it uses a plain manager. ``Campus`` belongs to one university and is
scoped through it.
"""

from __future__ import annotations

from django.contrib.postgres.fields import ArrayField
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from config.tenancy import TenantScopedModel

from .constants import (
    HSL_TRIPLE_PATTERN,
    KENYAN_COUNTIES,
    SignupPolicy,
    VerificationMethod,
)

hsl_triple_validator = RegexValidator(
    regex=HSL_TRIPLE_PATTERN,
    message=_(
        'Use three space-separated HSL components with no wrapper, e.g. "142 71% 45%". '
        "The token system needs this exact shape."
    ),
)


class University(models.Model):
    """A tenant.

    Resolved from the request subdomain by the tenancy middleware, or from an
    ``X-University`` header in development and tests only — production refuses
    to boot with that fallback enabled (ADR-001).
    """

    # -- Identity ---------------------------------------------------------
    name = models.CharField(_("name"), max_length=200)
    display_name = models.CharField(
        _("display name"),
        max_length=50,
        help_text=_('Short form used in the interface, e.g. "KyU".'),
    )
    slug = models.SlugField(_("slug"), max_length=50, unique=True)
    subdomain = models.CharField(
        _("subdomain"),
        max_length=63,
        unique=True,
        help_text=_('The tenant host label: "kyu" serves kyu.example.co.ke.'),
    )
    domain = models.CharField(
        _("institution domain"),
        max_length=255,
        blank=True,
        help_text=_('The university\'s own domain, e.g. "ku.ac.ke".'),
    )

    # -- Location ---------------------------------------------------------
    county = models.CharField(_("county"), max_length=50, choices=KENYAN_COUNTIES)
    town = models.CharField(_("town"), max_length=100)

    # -- Branding (ADR-005) -----------------------------------------------
    logo_url = models.URLField(_("logo URL"), blank=True)
    favicon_url = models.URLField(_("favicon URL"), blank=True)
    primary_hsl = models.CharField(
        _("primary colour"),
        max_length=32,
        default="142 71% 45%",
        validators=[hsl_triple_validator],
    )
    secondary_hsl = models.CharField(
        _("secondary colour"),
        max_length=32,
        default="30 50% 40%",
        validators=[hsl_triple_validator],
    )
    accent_hsl = models.CharField(
        _("accent colour"),
        max_length=32,
        default="142 71% 95%",
        validators=[hsl_triple_validator],
    )

    # -- Verification policy (ADR-003) ------------------------------------
    verification_methods_enabled = ArrayField(
        models.CharField(max_length=24, choices=VerificationMethod.choices),
        verbose_name=_("verification methods enabled"),
        default=list,
        blank=True,
        help_text=_("May be empty. Verification is off by default."),
    )
    student_email_domains = ArrayField(
        models.CharField(max_length=255),
        verbose_name=_("student email domains"),
        default=list,
        blank=True,
        help_text=_('Domains that prove enrolment, e.g. "s.kyu.ac.ke".'),
    )
    signup_policy = models.CharField(
        _("signup policy"),
        max_length=24,
        choices=SignupPolicy.choices,
        default=SignupPolicy.OPEN,
        help_text=_(
            "Cannot be set to 'required' until at least one student here is "
            "verified, so a school cannot lock out its own intake."
        ),
    )
    verification_enforced_from = models.DateField(
        _("verification enforced from"),
        null=True,
        blank=True,
        help_text=_("The signup policy is inert before this date. Blank means immediately."),
    )
    verification_grace_period_days = models.PositiveSmallIntegerField(
        _("verification grace period (days)"),
        default=14,
        validators=[MinValueValidator(1), MaxValueValidator(180)],
        help_text=_(
            "How long a new student may use gated actions while their "
            "verification is pending. Verification waits on the registry or on "
            "a human reviewer, neither of which the student controls."
        ),
    )
    verification_required_to_review = models.BooleanField(
        _("verification required to review"),
        default=False,
    )
    id_review_retention_days = models.PositiveSmallIntegerField(
        _("ID document retention (days)"),
        default=7,
        validators=[MinValueValidator(1), MaxValueValidator(90)],
        help_text=_(
            "Uploaded ID documents are deleted this many days after a decision. "
            "Kenya's Data Protection Act 2019 obliges us to keep them no longer "
            "than necessary."
        ),
    )

    is_active = models.BooleanField(_("active"), default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("University")
        verbose_name_plural = _("Universities")
        ordering = ["name"]
        indexes = [
            # Hit on every single request by the tenancy middleware.
            models.Index(fields=["subdomain", "is_active"], name="university_subdomain_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(primary_hsl__regex=HSL_TRIPLE_PATTERN),
                name="university_primary_hsl_shape",
            ),
            models.CheckConstraint(
                condition=Q(secondary_hsl__regex=HSL_TRIPLE_PATTERN),
                name="university_secondary_hsl_shape",
            ),
            models.CheckConstraint(
                condition=Q(accent_hsl__regex=HSL_TRIPLE_PATTERN),
                name="university_accent_hsl_shape",
            ),
            models.CheckConstraint(
                condition=Q(id_review_retention_days__gte=1) & Q(id_review_retention_days__lte=90),
                name="university_retention_window_sane",
            ),
            models.CheckConstraint(
                condition=Q(verification_grace_period_days__gte=1)
                & Q(verification_grace_period_days__lte=180),
                name="university_grace_period_sane",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def theme_tokens(self) -> dict[str, str]:
        """The three tokens ADR-005 overrides.

        Foregrounds, ``--ring`` and the light/dark primary variants are derived
        client-side by WCAG contrast rather than stored, so a tenant cannot
        configure an unreadable button.
        """
        return {
            "primary": self.primary_hsl,
            "secondary": self.secondary_hsl,
            "accent": self.accent_hsl,
        }


class Campus(TenantScopedModel):
    """One site of a university.

    Multi-campus institutions are common, and their campuses are sometimes in
    different towns entirely, so distances are computed per campus rather than
    per university (ADR-002).
    """

    tenant_lookup = "university"

    university = models.ForeignKey(
        University,
        on_delete=models.CASCADE,
        related_name="campuses",
        verbose_name=_("university"),
    )
    name = models.CharField(_("name"), max_length=100)
    town = models.CharField(_("town"), max_length=100)
    county = models.CharField(_("county"), max_length=50, choices=KENYAN_COUNTIES)
    latitude = models.FloatField(
        _("latitude"),
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.FloatField(
        _("longitude"),
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    is_main = models.BooleanField(_("main campus"), default=False)

    join_radius_km = models.FloatField(
        _("join radius (km)"),
        null=True,
        blank=True,
        help_text=_(
            "How far a property can be from this campus and still be listed "
            "against it. Blank uses the platform default "
            "(CAMPUS_JOIN_RADIUS_KM). A city campus with dense housing next "
            "door and a rural one where students commute from the nearest "
            "town are not the same question, and one number for both is a "
            "decision nobody made."
        ),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("Campus")
        verbose_name_plural = _("Campuses")
        ordering = ["university", "name"]
        base_manager_name = "all_objects"
        default_manager_name = "all_objects"
        constraints = [
            models.UniqueConstraint(
                fields=["university", "name"], name="campus_unique_name_per_university"
            ),
            models.UniqueConstraint(
                fields=["university"],
                condition=Q(is_main=True),
                name="campus_one_main_per_university",
            ),
            models.CheckConstraint(
                condition=Q(latitude__gte=-90) & Q(latitude__lte=90),
                name="campus_latitude_range",
            ),
            models.CheckConstraint(
                condition=Q(longitude__gte=-180) & Q(longitude__lte=180),
                name="campus_longitude_range",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.university.display_name} — {self.name}"
