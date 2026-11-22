from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("groupes", "0008_dossierdocument_document"),
    ]

    operations = [
        migrations.AddField(
            model_name="classe",
            name="public_documents",
            field=models.BooleanField(
                default=False,
                help_text="Active la rubrique Documents sur la page complète partagée avec la classe.",
                verbose_name="Afficher la section Documents sur la page publique",
            ),
        ),
        migrations.AddField(
            model_name="dossierdocument",
            name="allow_public_downloads",
            field=models.BooleanField(default=True, verbose_name="Autoriser le téléchargement public"),
        ),
        migrations.AddField(
            model_name="document",
            name="is_public",
            field=models.BooleanField(default=True, verbose_name="Visible sur la page publique"),
        ),
    ]
