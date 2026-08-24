"""Reference data for the Kenyan market."""

from __future__ import annotations

from django.db import models
from django.utils.translation import gettext_lazy as _


class SignupPolicy(models.TextChoices):
    """How hard a university gates signup on student verification (ADR-003).

    Replaces the earlier ``verification_required_to_signup`` boolean. The
    boolean's guard checked configuration — "are any methods enabled?" — and so
    missed the failure that actually happens: a school enables email-domain
    verification, sets the flag, and only then discovers it has not yet issued
    addresses to its first-years. An entire intake is locked out in the week
    they most need the platform.

    The guard now checks an outcome instead. See
    ``universities.services.assert_signup_policy_is_safe``.
    """

    OPEN = "open", _("Open — verification is not mentioned at signup")
    ENCOURAGED = (
        "verification_encouraged",
        _("Encouraged — the student is prompted and can skip"),
    )
    REQUIRED = (
        "verification_required",
        _("Required — signup completes only for a verified student"),
    )


class VerificationMethod(models.TextChoices):
    """Ways a university can prove a student is a student (ADR-003)."""

    EMAIL_DOMAIN = "email_domain", _("Student email domain")
    STUDENT_ID_UPLOAD = "student_id_upload", _("Student ID document upload")


class VerificationStatus(models.TextChoices):
    """Where a student's verification stands (ADR-003).

    Lives here rather than in ``accounts`` because ``universities.services``
    reads it, and importing from accounts would be a cycle.
    """

    UNVERIFIED = "unverified", _("Unverified")
    PENDING = "pending", _("Pending review")
    VERIFIED = "verified", _("Verified")
    REJECTED = "rejected", _("Rejected")


#: Kenya's 47 counties, as gazetted. Used for property and campus addresses;
#: a Kenyan address is county/town/estate, not state/ZIP (docs/AUDIT.md §3).
KENYAN_COUNTIES: list[tuple[str, str]] = [
    ("baringo", "Baringo"),
    ("bomet", "Bomet"),
    ("bungoma", "Bungoma"),
    ("busia", "Busia"),
    ("elgeyo_marakwet", "Elgeyo-Marakwet"),
    ("embu", "Embu"),
    ("garissa", "Garissa"),
    ("homa_bay", "Homa Bay"),
    ("isiolo", "Isiolo"),
    ("kajiado", "Kajiado"),
    ("kakamega", "Kakamega"),
    ("kericho", "Kericho"),
    ("kiambu", "Kiambu"),
    ("kilifi", "Kilifi"),
    ("kirinyaga", "Kirinyaga"),
    ("kisii", "Kisii"),
    ("kisumu", "Kisumu"),
    ("kitui", "Kitui"),
    ("kwale", "Kwale"),
    ("laikipia", "Laikipia"),
    ("lamu", "Lamu"),
    ("machakos", "Machakos"),
    ("makueni", "Makueni"),
    ("mandera", "Mandera"),
    ("marsabit", "Marsabit"),
    ("meru", "Meru"),
    ("migori", "Migori"),
    ("mombasa", "Mombasa"),
    ("muranga", "Murang'a"),
    ("nairobi", "Nairobi"),
    ("nakuru", "Nakuru"),
    ("nandi", "Nandi"),
    ("narok", "Narok"),
    ("nyamira", "Nyamira"),
    ("nyandarua", "Nyandarua"),
    ("nyeri", "Nyeri"),
    ("samburu", "Samburu"),
    ("siaya", "Siaya"),
    ("taita_taveta", "Taita-Taveta"),
    ("tana_river", "Tana River"),
    ("tharaka_nithi", "Tharaka-Nithi"),
    ("trans_nzoia", "Trans Nzoia"),
    ("turkana", "Turkana"),
    ("uasin_gishu", "Uasin Gishu"),
    ("vihiga", "Vihiga"),
    ("wajir", "Wajir"),
    ("west_pokot", "West Pokot"),
]

#: shadcn stores colours as three space-separated HSL components with no
#: ``hsl()`` wrapper, which is what makes ``hsl(var(--primary) / 0.5)`` work
#: for opacity variants (ADR-005). Anything else breaks the token system.
HSL_TRIPLE_PATTERN = r"^\d{1,3}(\.\d+)?\s+\d{1,3}(\.\d+)?%\s+\d{1,3}(\.\d+)?%$"
