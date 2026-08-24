"""Serializers for the universities app."""

from __future__ import annotations

from rest_framework import serializers

from .models import University


class TenantThemeSerializer(serializers.Serializer):
    """The three tokens ADR-005 overrides.

    Foregrounds, `--ring` and the light/dark primary variants are derived
    client-side by WCAG contrast rather than sent, so a tenant cannot configure
    an unreadable button.
    """

    primary = serializers.CharField(help_text='HSL triple, e.g. "142 71% 45%".')
    secondary = serializers.CharField()
    accent = serializers.CharField()


class TenantConfigSerializer(serializers.ModelSerializer):
    """The public tenant configuration (ADR-005).

    Deliberately small. It is fetched before first paint on every cold visit,
    so anything not needed to render the shell does not belong here.
    """

    theme = TenantThemeSerializer(source="theme_tokens", read_only=True)

    class Meta:
        model = University
        fields = [
            "subdomain",
            "name",
            "display_name",
            "logo_url",
            "favicon_url",
            "theme",
        ]
        read_only_fields = fields
