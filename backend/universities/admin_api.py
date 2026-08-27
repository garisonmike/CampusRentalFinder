"""
University administration endpoints (ADR-003, ADR-005).

What a school administers about itself: its verification policy, its grace
period, its student email domains, and its theme.

**The lockout guard is enforced here, at the boundary.** A school cannot set
`signup_policy = verification_required` until at least one student there is
actually verified. That guard exists because of a specific, likely failure:

> A school enables email-domain verification, sets the policy to required, and
> only then discovers it has not yet issued addresses to its first-years. An
> entire intake is locked out in the week they most need the platform.

The guard therefore checks an **outcome** — has anyone actually got through? —
rather than configuration, because "are any methods enabled?" is exactly the
question that returns yes in the scenario above.
"""

from __future__ import annotations

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsUniversityStaffForTenant
from config.api.throttling import Scope

from .constants import SignupPolicy, VerificationMethod
from .models import University
from .services import assert_signup_policy_is_safe


class UniversityPolicySerializer(serializers.ModelSerializer):
    """The settings a school administers about itself."""

    #: Declared explicitly so blank entries reach `validate_student_email_domains`
    #: rather than being rejected by the child field first. A settings form with
    #: a trailing empty row is the normal way this arrives, and refusing the
    #: whole request over it would be a worse experience than dropping it.
    student_email_domains = serializers.ListField(
        child=serializers.CharField(allow_blank=True, max_length=255),
        required=False,
        help_text=(
            'Domains that prove enrolment, e.g. `["s.kyu.ac.ke"]`. Matched '
            "EXACTLY on the full domain -- list every subdomain you want "
            "accepted, because `alumni.kyu.ac.ke` is not `kyu.ac.ke` and the "
            "platform will not infer enrolment from DNS hierarchy. Blank "
            "entries are dropped; whitespace and case are normalised."
        ),
    )

    class Meta:
        model = University
        fields = (
            "name",
            "display_name",
            "signup_policy",
            "verification_methods_enabled",
            "student_email_domains",
            "verification_grace_period_days",
            "verification_required_to_review",
            "verification_enforced_from",
            "primary_hsl",
            "secondary_hsl",
            "accent_hsl",
        )
        read_only_fields = ("name",)
        extra_kwargs = {
            "signup_policy": {
                "help_text": (
                    "`open` (verification not mentioned at signup), "
                    "`verification_encouraged` (prompted, can skip), or "
                    "`verification_required`.\n\n"
                    "**Cannot be set to `verification_required` until at least "
                    "one student here is verified.** A school that sets it "
                    "before issuing student addresses locks out an entire "
                    "intake in the week they most need the platform.\n\n"
                    "Raising this affects NEW signups only. Existing students "
                    "keep what they had -- gating reads the policy frozen at "
                    "each student's own registration (ADR-003)."
                )
            },
            "verification_methods_enabled": {
                "help_text": (
                    "Which paths this school offers. Empty means none, which "
                    "is the default. A school with no document reviewers "
                    "should not enable `student_id_upload`: uploads would "
                    "arrive into a queue nobody works, and since the retention "
                    "clock starts at upload that means collecting national IDs "
                    "purely to delete them thirty days later."
                )
            },
            "verification_grace_period_days": {
                "help_text": (
                    "How long a new student may use gated actions before "
                    "verifying. Verification waits on a registry or a human "
                    "reviewer, neither of which the student controls."
                )
            },
            "verification_enforced_from": {
                "help_text": (
                    "Announce a change before it bites. Until this date the "
                    "policy is treated as `open` for new signups."
                )
            },
        }

    def validate(self, attrs):
        """Run the lockout guard before anything is written.

        In `validate` rather than in the view so the admin and any future path
        go through it too -- the guard is the whole reason the policy is an
        enum rather than a boolean.
        """
        policy = attrs.get("signup_policy", self.instance.signup_policy)

        if policy == SignupPolicy.REQUIRED:
            assert_signup_policy_is_safe(self.instance, policy)

        return attrs

    def validate_verification_methods_enabled(self, methods):
        unknown = sorted(set(methods) - set(VerificationMethod.values))
        if unknown:
            raise serializers.ValidationError(
                f"Unknown verification method(s): {', '.join(unknown)}."
            )
        return methods

    def validate_student_email_domains(self, domains):
        cleaned = [domain.strip().lower() for domain in domains if domain.strip()]

        for domain in cleaned:
            if "@" in domain or "/" in domain:
                raise serializers.ValidationError(
                    f"{domain!r} is not a domain -- list `s.kyu.ac.ke`, not an address or a URL."
                )
        return cleaned


@extend_schema_view(
    get=extend_schema(
        summary="Your university's policy and theme",
        description="University staff only, and only for their own institution.",
    ),
    patch=extend_schema(
        summary="Update your university's policy or theme",
        description=(
            "**The lockout guard runs here.** Setting `signup_policy` to "
            "`verification_required` is refused until at least one student at "
            "this university is verified -- the guard checks an outcome, not "
            "configuration, because 'are any methods enabled?' returns yes in "
            "exactly the case that locks out an intake (ADR-003)."
        ),
        request=UniversityPolicySerializer,
    ),
)
class UniversityPolicyView(APIView):
    """A school's own settings."""

    permission_classes = [IsAuthenticated, IsUniversityStaffForTenant]
    throttle_scope = Scope.WRITE

    def get_university(self, request) -> University:
        """From the staff profile, never from the request host.

        A host header is caller-supplied. Scoping a write endpoint by one would
        let a member of staff at one school edit another's policy by changing
        a header.
        """
        profile = getattr(request.user, "staff_profile", None)
        if profile is None:
            raise NotFound("No university staff profile.")
        return profile.university

    def get(self, request):
        return Response(UniversityPolicySerializer(self.get_university(request)).data)

    def patch(self, request):
        university = self.get_university(request)

        serializer = UniversityPolicySerializer(university, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        return Response(serializer.data)
