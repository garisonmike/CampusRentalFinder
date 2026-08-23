from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "accounts"
    verbose_name = "Accounts"

    def ready(self) -> None:
        # Signal receivers currently live in models.py; importing the module
        # here keeps that working if they are moved to accounts/signals.py.
        from accounts import models  # noqa: F401
