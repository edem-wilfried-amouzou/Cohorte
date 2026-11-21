from django.apps import AppConfig


class GroupesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.groupes"
    verbose_name = "Groupes"

    def ready(self):
        from . import signals  # noqa: F401
