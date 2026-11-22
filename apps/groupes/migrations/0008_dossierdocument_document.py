import apps.groupes.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("groupes", "0007_audit_commentaire_leader"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DossierDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, verbose_name="Nom du dossier")),
                ("is_public", models.BooleanField(default=False, verbose_name="Visible sur la page publique")),
                ("allow_public_uploads", models.BooleanField(default=False, verbose_name="Autoriser l’ajout public de fichiers")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("activite", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="dossiers_documents", to="groupes.activite")),
                ("classe", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="dossiers_documents", to="groupes.classe")),
                ("parent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="sous_dossiers", to="groupes.dossierdocument")),
            ],
            options={
                "ordering": ["name", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="dossierdocument",
            constraint=models.UniqueConstraint(condition=models.Q(("activite__isnull", True), ("parent__isnull", True)), fields=("classe", "name"), name="dossier_nom_unique_racine_classe"),
        ),
        migrations.AddConstraint(
            model_name="dossierdocument",
            constraint=models.UniqueConstraint(condition=models.Q(("activite__isnull", False), ("parent__isnull", True)), fields=("classe", "activite", "name"), name="dossier_nom_unique_racine_activite"),
        ),
        migrations.AddConstraint(
            model_name="dossierdocument",
            constraint=models.UniqueConstraint(condition=models.Q(("parent__isnull", False)), fields=("classe", "parent", "name"), name="dossier_nom_unique_par_parent"),
        ),
        migrations.CreateModel(
            name="Document",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(max_length=255, upload_to=apps.groupes.models.document_upload_path)),
                ("display_name", models.CharField(max_length=255, verbose_name="Nom du fichier")),
                ("byte_size", models.PositiveBigIntegerField(default=0)),
                ("uploaded_at", models.DateTimeField(auto_now_add=True)),
                ("folder", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="documents", to="groupes.dossierdocument")),
                ("uploaded_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="documents_ajoutes", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["display_name", "id"],
            },
        ),
    ]
