import django.db.models.deletion
import apps.groupes.models
from django.db import migrations, models


def backfill_document_class(apps, schema_editor):
    Document = apps.get_model("groupes", "Document")
    for document in Document.objects.filter(classe__isnull=True).select_related("folder").iterator():
        document.classe_id = document.folder.classe_id
        document.save(update_fields=["classe"])


class Migration(migrations.Migration):

    dependencies = [
        ("groupes", "0009_public_document_controls"),
    ]

    operations = [
        migrations.AddField(
            model_name="document",
            name="classe",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="documents",
                to="groupes.classe",
            ),
        ),
        migrations.AddField(
            model_name="document",
            name="allow_public_downloads",
            field=models.BooleanField(default=True, verbose_name="Autoriser le téléchargement public"),
        ),
        migrations.RunPython(backfill_document_class, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="document",
            name="classe",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="documents",
                to="groupes.classe",
            ),
        ),
        migrations.AlterField(
            model_name="document",
            name="folder",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="documents",
                to="groupes.dossierdocument",
            ),
        ),
    ]
