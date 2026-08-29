from django.apps import AppConfig


class ConfigAppConfig(AppConfig):
    name = "config"
    verbose_name = "Project configuration"

    def ready(self) -> None:
        # Imported for the side effect of registering system checks.
        #
        # `@register()` only fires when the module is imported, and both of
        # these live in modules nothing imports at startup -- `config.storage`
        # is loaded lazily by Django's storage machinery, which is after
        # checks run. A check that does not register is indistinguishable from
        # a check that passes.
        from . import security_checks, storage  # noqa: F401
