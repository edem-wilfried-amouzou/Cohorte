import tempfile
from io import BytesIO

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    Activite, AuditLog, Classe, Collaborateur, Commentaire, Document, DossierDocument,
    Groupe, Membre, Participation,
)


class FeatureBase(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("delegue", password="motdepasse-solide-1")
        self.viewer = User.objects.create_user("lecteur", password="motdepasse-solide-2")
        self.classe = Classe.objects.create(owner=self.user, name="L3 Info")
        Collaborateur.objects.create(classe=self.classe, user=self.viewer, role=Collaborateur.Role.VIEWER)
        self.activity = Activite.objects.create(classe=self.classe, title="Exposé IA", is_public=True)
        self.group = Groupe.objects.create(classe=self.classe, activite=self.activity, number=1)
        self.other_group = Groupe.objects.create(classe=self.classe, activite=self.activity, number=2)
        self.ama = Membre.objects.create(classe=self.classe, first_name="Kossi", last_name="AMEGAH")
        self.afi = Membre.objects.create(classe=self.classe, first_name="Afi", last_name="TCHALIM")
        for member, group in ((self.ama, self.group), (self.afi, self.other_group)):
            Participation.objects.create(activite=self.activity, membre=member, groupe=group, responded_at=timezone.now())
        self.client.force_login(self.user)

    def args(self, *extra):
        return [self.classe.pk, self.activity.pk, *extra]


class GroupLeaderTests(FeatureBase):
    def test_set_and_clear_leader(self):
        url = reverse("groupes:activity_group_leader", args=self.args(self.group.pk))
        self.client.post(url, {"leader": self.ama.pk})
        self.group.refresh_from_db()
        self.assertEqual(self.group.leader, self.ama)
        self.client.post(url, {"leader": ""})
        self.group.refresh_from_db()
        self.assertIsNone(self.group.leader)

    def test_leader_must_belong_to_group(self):
        url = reverse("groupes:activity_group_leader", args=self.args(self.group.pk))
        self.client.post(url, {"leader": self.afi.pk})
        self.group.refresh_from_db()
        self.assertIsNone(self.group.leader)
        self.client.post(url, {"leader": "abc"})
        self.group.refresh_from_db()
        self.assertIsNone(self.group.leader)

    def test_viewer_cannot_set_leader(self):
        self.client.force_login(self.viewer)
        r = self.client.post(reverse("groupes:activity_group_leader", args=self.args(self.group.pk)), {"leader": self.ama.pk})
        self.assertEqual(r.status_code, 404)

    def test_leader_shown_on_detail_and_public_view(self):
        self.group.leader = self.ama
        self.group.save()
        detail = self.client.get(reverse("groupes:activity_detail", args=self.args()))
        self.assertContains(detail, "Chef")
        public = self.client.get(reverse("groupes:activity_public_view", args=[self.activity.view_token]))
        self.assertContains(public, "Chef de groupe")


class CommentTests(FeatureBase):
    def test_add_public_comment_visible_on_join_and_view(self):
        self.client.post(reverse("groupes:comment_add", args=self.args()), {"body": "Salle B12 à 9h", "is_public": "on"})
        self.assertEqual(Commentaire.objects.count(), 1)
        join = self.client.get(reverse("groupes:activity_join", args=[self.activity.token]))
        self.assertContains(join, "Salle B12 à 9h")
        view = self.client.get(reverse("groupes:activity_public_view", args=[self.activity.view_token]))
        self.assertContains(view, "Salle B12 à 9h")

    def test_private_comment_not_public(self):
        self.client.post(reverse("groupes:comment_add", args=self.args()), {"body": "Note interne"})
        self.assertNotContains(self.client.get(reverse("groupes:activity_join", args=[self.activity.token])), "Note interne")
        self.assertContains(self.client.get(reverse("groupes:activity_detail", args=self.args())), "Note interne")

    def test_comment_html_is_escaped(self):
        self.client.post(reverse("groupes:comment_add", args=self.args()), {"body": "<script>alert(1)</script>", "is_public": "on"})
        page = self.client.get(reverse("groupes:activity_join", args=[self.activity.token])).content.decode()
        self.assertNotIn("<script>alert(1)</script>", page)

    def test_viewer_cannot_comment_or_delete(self):
        note = Commentaire.objects.create(activite=self.activity, author=self.user, body="x")
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.post(reverse("groupes:comment_add", args=self.args()), {"body": "y"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("groupes:comment_delete", args=self.args(note.pk))).status_code, 404)

    def test_delete_comment(self):
        note = Commentaire.objects.create(activite=self.activity, author=self.user, body="x")
        self.client.post(reverse("groupes:comment_delete", args=self.args(note.pk)))
        self.assertFalse(Commentaire.objects.exists())

    def test_cannot_delete_comment_of_another_activity(self):
        other = Activite.objects.create(classe=self.classe, title="Autre")
        note = Commentaire.objects.create(activite=other, author=self.user, body="x")
        r = self.client.post(reverse("groupes:comment_delete", args=self.args(note.pk)))
        self.assertEqual(r.status_code, 404)
        self.assertTrue(Commentaire.objects.exists())


class PosterTests(FeatureBase):
    def test_class_and_activity_posters(self):
        r = self.client.get(reverse("groupes:poster", args=[self.classe.pk]))
        self.assertContains(r, self.classe.token)
        self.assertContains(r, "window.print")
        r = self.client.get(reverse("groupes:activity_poster", args=self.args()))
        self.assertContains(r, self.activity.token)

    def test_stranger_cannot_open_poster(self):
        self.client.force_login(User.objects.create_user("intrus", password="motdepasse-solide-3"))
        self.assertEqual(self.client.get(reverse("groupes:poster", args=[self.classe.pk])).status_code, 404)


class GroupImportAndAssignmentTests(FeatureBase):
    def test_csv_import_creates_groups_and_matches_class_members(self):
        upload = SimpleUploadedFile(
            "groupes.csv",
            "Groupe,Membres\nGroupe 3,Kossi AMEGAH,Afi TCHALIM\n".encode(),
            content_type="text/csv",
        )
        response = self.client.post(
            reverse("groupes:activity_groups_import", args=self.args()), {"file": upload},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["result"]["assigned"], 2)
        imported = Groupe.objects.get(classe=self.classe, activite=self.activity, number=3)
        self.assertSetEqual(
            set(Participation.objects.filter(activite=self.activity, groupe=imported).values_list("membre_id", flat=True)),
            {self.ama.pk, self.afi.pk},
        )

    def test_bulk_assign_moves_selected_members_to_existing_group(self):
        response = self.client.post(
            reverse("groupes:activity_assign_bulk", args=self.args()),
            {"group_id": self.group.pk, "member_ids": [self.ama.pk, self.afi.pk]},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Participation.objects.filter(activite=self.activity, groupe=self.group).count(), 2)
        self.assertFalse(Participation.objects.filter(activite=self.activity, groupe=self.other_group).exists())


class PublicLibraryTests(FeatureBase):
    def setUp(self):
        super().setUp()
        self.temp_media = tempfile.TemporaryDirectory()
        self.settings_override = override_settings(
            MEDIA_ROOT=self.temp_media.name,
            STORAGES={
                "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
            },
        )
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.addCleanup(self.temp_media.cleanup)
        self.classe.public_documents = True
        self.classe.allow_public_folder_creation = True
        self.classe.allow_public_uploads = True
        self.classe.save(update_fields=["public_documents", "allow_public_folder_creation", "allow_public_uploads"])

    def test_public_page_can_create_folder_and_upload_document(self):
        create_response = self.client.post(
            reverse("groupes:public_document_folder_create", args=[self.classe.token]), {"name": "Ressources"},
        )
        folder = DossierDocument.objects.get(classe=self.classe, name="Ressources")
        self.assertTrue(folder.is_public)
        self.assertTrue(folder.allow_public_uploads)
        self.assertRedirects(
            create_response,
            f"{reverse('groupes:public_portal', args=[self.classe.token])}?view=documents&folder={folder.pk}",
            fetch_redirect_response=False,
        )

        upload = SimpleUploadedFile("consigne.txt", b"Bonjour", content_type="text/plain")
        response = self.client.post(
            reverse("groupes:public_document_upload", args=[self.classe.token, folder.pk]),
            {"folder": folder.pk, "file": upload},
        )
        self.assertEqual(response.status_code, 302)
        document = Document.objects.get(classe=self.classe, folder=folder)
        self.assertEqual(document.display_name, "consigne.txt")
        preview = self.client.get(
            reverse("groupes:public_document_download", args=[self.classe.token, document.pk]), {"inline": "1"},
        )
        self.assertEqual(preview.status_code, 200)
        self.assertIn("text/plain", preview["Content-Type"])

    def test_class_upload_permission_is_required_even_if_folder_allows_uploads(self):
        folder = DossierDocument.objects.create(
            classe=self.classe, name="Ouvert", is_public=True, allow_public_uploads=True,
        )
        self.classe.allow_public_uploads = False
        self.classe.save(update_fields=["allow_public_uploads"])
        response = self.client.post(
            reverse("groupes:public_document_upload", args=[self.classe.token, folder.pk]),
            {"folder": folder.pk, "file": SimpleUploadedFile("x.txt", b"x")},
        )
        self.assertEqual(response.status_code, 404)

    def test_word_document_is_rendered_inside_the_preview(self):
        from docx import Document as WordDocument

        word = WordDocument()
        word.add_paragraph("Consigne consultable dans Cohorte")
        content = BytesIO()
        word.save(content)
        folder = DossierDocument.objects.create(
            classe=self.classe, name="Documents", is_public=True, allow_public_uploads=True,
        )
        upload = SimpleUploadedFile(
            "consigne.docx", content.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.client.post(
            reverse("groupes:public_document_upload", args=[self.classe.token, folder.pk]),
            {"folder": folder.pk, "file": upload},
        )
        document = Document.objects.get(classe=self.classe, folder=folder)
        preview = self.client.get(
            reverse("groupes:public_document_download", args=[self.classe.token, document.pk]), {"inline": "1"},
        )
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "Consigne consultable dans Cohorte")


class AuditTests(FeatureBase):
    def test_sensitive_actions_are_logged(self):
        self.client.post(reverse("groupes:activity_regenerate", args=self.args()))
        self.client.post(reverse("groupes:member_delete", args=[self.classe.pk, self.afi.pk]))
        self.client.post(reverse("groupes:delete", args=[self.classe.pk]))
        actions = set(AuditLog.objects.values_list("action", flat=True))
        self.assertTrue({"lien_activite_renouvele", "membre_supprime", "classe_supprimee"} <= actions)

    def test_audit_survives_class_deletion(self):
        self.client.post(reverse("groupes:delete", args=[self.classe.pk]))
        entry = AuditLog.objects.get(action="classe_supprimee")
        self.assertEqual(entry.detail, "L3 Info")
        self.assertEqual(entry.user, self.user)
