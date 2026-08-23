from django.apps import AppConfig


class ReviewsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "reviews"
    verbose_name = "Reviews"

    def ready(self) -> None:
        from reviews import models  # noqa: F401
