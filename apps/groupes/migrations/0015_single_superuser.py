from django.conf import settings
from django.db import migrations, models


CONSTRAINT_NAME = "single_superuser_only"


def add_single_superuser_constraint(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    database = schema_editor.connection.alias
    if User.objects.using(database).filter(is_superuser=True).count() > 1:
        raise RuntimeError(
            "Plusieurs super-administrateurs existent déjà. Gardez-en un seul avant d’appliquer cette migration."
        )
    constraint = models.UniqueConstraint(
        fields=["is_superuser"],
        condition=models.Q(is_superuser=True),
        name=CONSTRAINT_NAME,
    )
    schema_editor.add_constraint(User, constraint)


def remove_single_superuser_constraint(apps, schema_editor):
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    constraint = models.UniqueConstraint(
        fields=["is_superuser"],
        condition=models.Q(is_superuser=True),
        name=CONSTRAINT_NAME,
    )
    schema_editor.remove_constraint(User, constraint)


class Migration(migrations.Migration):
    dependencies = [
        ("groupes", "0014_auditlog_extended_details"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(add_single_superuser_constraint, remove_single_superuser_constraint),
    ]
