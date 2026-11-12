import django.conf
import django.db.models.deletion
from django.db import migrations, models

from apps.groupes.models import new_token


def populate_view_tokens(apps, schema_editor):
    Activite = apps.get_model("groupes", "Activite")
    database = schema_editor.connection.alias
    for activity in Activite.objects.using(database).all().iterator():
        activity.view_token = new_token()
        activity.save(update_fields=["view_token"], using=database)


class Migration(migrations.Migration):
    dependencies = [
        ("groupes", "0002_activite_participation_and_more"),
        migrations.swappable_dependency(django.conf.settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Collaborateur",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("role", models.CharField(choices=[("co_delegate", "Co-délégué (gestion complète)"), ("secretary", "Secrétaire (gestion de classe)"), ("viewer", "Consultation seulement")], default="secretary", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("classe", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="collaborateurs", to="groupes.classe")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="classes_partagees", to=django.conf.settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="collaborateur",
            constraint=models.UniqueConstraint(fields=("classe", "user"), name="collaborateur_unique_par_classe"),
        ),
        migrations.AddField(
            model_name="activite",
            name="is_public",
            field=models.BooleanField(default=False, verbose_name="Autoriser la consultation publique"),
        ),
        migrations.AddField(
            model_name="activite",
            name="view_token",
            field=models.CharField(default="", editable=False, max_length=24),
        ),
        migrations.RunPython(populate_view_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="activite",
            name="view_token",
            field=models.CharField(default=new_token, editable=False, max_length=24, unique=True),
        ),
    ]
