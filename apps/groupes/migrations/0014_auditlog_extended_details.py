from django.db import migrations, models


def backfill_audit_context(apps, schema_editor):
    AuditLog = apps.get_model("groupes", "AuditLog")
    database = schema_editor.connection.alias
    batch = []
    for entry in AuditLog.objects.using(database).select_related("user", "classe").iterator(chunk_size=500):
        if entry.user_id and entry.user:
            entry.actor_user_id = entry.user_id
            entry.actor_identifier = entry.user.get_username()[:150]
            entry.actor_role = "super_admin" if entry.user.is_superuser else "staff" if entry.user.is_staff else "utilisateur"
        elif entry.action == "connexion_echec":
            entry.actor_identifier = (entry.detail or "")[:150].split(" ")[0]
            entry.actor_role = "anonyme"
        if entry.classe_id:
            entry.object_type = "groupes.classe"
            entry.object_id = str(entry.classe_id)
            entry.object_label = (entry.classe.name if entry.classe else "")[:200]
        if entry.action == "connexion_echec":
            entry.succeeded = False
            entry.response_status = 401
        batch.append(entry)
        if len(batch) >= 500:
            AuditLog.objects.using(database).bulk_update(
                batch, ["actor_user_id", "actor_identifier", "actor_role", "object_type", "object_id", "object_label", "succeeded", "response_status"],
            )
            batch = []
    if batch:
        AuditLog.objects.using(database).bulk_update(
            batch, ["actor_user_id", "actor_identifier", "actor_role", "object_type", "object_id", "object_label", "succeeded", "response_status"],
        )


class Migration(migrations.Migration):
    dependencies = [
        ("groupes", "0013_classe_allow_public_folder_creation_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="auditlog",
            name="actor_identifier",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="actor_role",
            field=models.CharField(blank=True, max_length=30),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="actor_user_id",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="city",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="latitude",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="longitude",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="object_id",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="object_label",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="object_type",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="path",
            field=models.CharField(blank=True, max_length=300),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="region",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="response_status",
            field=models.PositiveSmallIntegerField(default=200),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="succeeded",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="before_data",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="auditlog",
            name="after_data",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_audit_context, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="auditlog",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, db_index=True),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["user", "-created_at"], name="audit_user_created_idx"),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["actor_user_id", "-created_at"], name="audit_actor_id_created_idx"),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["action", "-created_at"], name="audit_action_created_idx"),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["object_type", "object_id"], name="audit_object_idx"),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["country_code", "-created_at"], name="audit_country_created_idx"),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["city", "-created_at"], name="audit_city_created_idx"),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["ip", "-created_at"], name="audit_ip_created_idx"),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(fields=["response_status", "-created_at"], name="audit_status_created_idx"),
        ),
    ]
