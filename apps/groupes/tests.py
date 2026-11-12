import io
from importlib import import_module

from django.contrib.auth.models import User
from django.apps import apps as django_apps
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from .models import Activite, Classe, Collaborateur, DemandeMembre, Groupe, Membre, Participation, normalize
from .services import importing
from .services.allocation import auto_assign


class Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("delegue", password="motdepasse-solide-1")
        self.other = User.objects.create_user("autre", password="motdepasse-solide-2")
        self.classe = Classe.objects.create(owner=self.user, name="L3 Info")
        self.g1 = Groupe.objects.create(classe=self.classe, number=1, capacity=2)
        self.g2 = Groupe.objects.create(classe=self.classe, number=2)
        self.ama = Membre.objects.create(classe=self.classe, full_name="AMEGAH Kossi")
        self.afi = Membre.objects.create(classe=self.classe, full_name="Tchalim Afi")
        self.yao = Membre.objects.create(classe=self.classe, full_name="Éric Yao")


class ModelTests(Base):
    def test_normalize_removes_accents_and_case(self):
        self.assertEqual(normalize("  Éric   YAO "), "eric yao")

    def test_status(self):
        self.assertEqual(self.ama.status, "en_attente")
        self.ama.responded_at = timezone.now()
        self.assertEqual(self.ama.status, "sans_groupe")
        self.ama.groupe = self.g1
        self.assertEqual(self.ama.status, "affecte")


class MigrationCompatibilityTests(TransactionTestCase):
    def test_existing_activity_receives_a_view_token(self):
        user = User.objects.create_user("migration-user", password="motdepasse-solide-3")
        classe = Classe.objects.create(owner=user, name="Classe migration")
        activity = Activite.objects.create(classe=classe, title="Déjà créé", view_token="")
        migration = import_module("apps.groupes.migrations.0003_collaborateur_public_view")
        activity.view_token = ""
        activity.save(update_fields=["view_token"])
        with connection.schema_editor() as editor:
            migration.populate_view_tokens(django_apps, editor)
        activity.refresh_from_db()
        self.assertTrue(activity.view_token)
        self.assertNotEqual(activity.view_token, "")


class ImportTests(Base):
    def test_text_import_skips_duplicates(self):
        rows = importing.parse_text("AMEGAH Kossi\nNouveau Nom\n\neric yao")
        result = importing.import_rows(self.classe, rows)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["duplicates"], 2)

    def test_csv_with_header_and_group_creates_groups(self):
        data = "Nom;Groupe;Matricule\nBaba Ali;7;M1\nCoco Lee;;M2\n".encode()
        rows = importing.parse_upload(SimpleUploadedFile("liste.csv", data))
        result = importing.import_rows(self.classe, rows)
        self.assertEqual(result["created"], 2)
        self.assertEqual(result["groups_created"], 1)
        self.assertEqual(Membre.objects.get(classe=self.classe, full_name="Baba Ali").groupe.number, 7)

    def test_xlsx_import(self):
        from openpyxl import Workbook

        wb = Workbook()
        wb.active.append(["Nom complet", "Groupe"])
        wb.active.append(["Zoe Mensah", 3])
        buf = io.BytesIO()
        wb.save(buf)
        rows = importing.parse_upload(SimpleUploadedFile("l.xlsx", buf.getvalue()))
        self.assertEqual((rows[0].name, rows[0].group), ("Zoe Mensah", 3))

    def test_csv_import_with_separate_names_and_sex(self):
        data = "Nom;Prénom;Sexe;Matricule;Téléphone\nMensah;Ama;Femme;A12;+22890000000\n".encode()
        rows = importing.parse_upload(SimpleUploadedFile("classe.csv", data))
        result = importing.import_rows(self.classe, rows)
        member = Membre.objects.get(classe=self.classe, full_name="Ama Mensah")
        self.assertEqual(result["created"], 1)
        self.assertEqual((member.sex, member.reference), ("F", "A12"))
        self.assertEqual(member.custom_data["telephone"], "+22890000000")
        self.assertTrue(self.classe.champs_membres.filter(key="telephone", label="Téléphone").exists())

    def test_rejects_bad_files(self):
        with self.assertRaises(importing.ImportError_):
            importing.parse_upload(SimpleUploadedFile("virus.exe", b"MZ"))
        with self.assertRaises(importing.ImportError_):
            importing.parse_upload(SimpleUploadedFile("faux.xlsx", b"pas un zip"))


class AllocationTests(Base):
    def test_auto_assign_balances_and_respects_capacity(self):
        for i in range(5):
            Membre.objects.create(classe=self.classe, full_name=f"Extra {i}")
        result = auto_assign(self.classe)
        self.assertEqual(result["left"], 0)
        self.assertLessEqual(self.g1.membres.count(), 2)
        self.assertEqual(self.g1.membres.count() + self.g2.membres.count(), 8)


class PublicJoinTests(Base):
    def url(self, name):
        return reverse(f"groupes:{name}", args=[self.classe.token])

    def test_join_flow(self):
        r = self.client.post(self.url("join"), {"name": "amegah kossi", "groupe": self.g1.pk})
        self.assertRedirects(r, self.url("join_done"))
        self.ama.refresh_from_db()
        self.assertEqual(self.ama.groupe, self.g1)

    def test_no_group_choice(self):
        self.client.post(self.url("join"), {"name": "Tchalim Afi", "groupe": "none"})
        self.afi.refresh_from_db()
        self.assertEqual(self.afi.status, "sans_groupe")

    def test_cannot_overwrite_existing_answer(self):
        self.client.post(self.url("join"), {"name": "Tchalim Afi", "groupe": self.g1.pk})
        r = self.client.post(self.url("join"), {"name": "Tchalim Afi", "groupe": self.g2.pk})
        self.assertEqual(r.status_code, 400)
        self.afi.refresh_from_db()
        self.assertEqual(self.afi.groupe, self.g1)

    def test_capacity_enforced(self):
        self.client.post(self.url("join"), {"name": "AMEGAH Kossi", "groupe": self.g1.pk})
        self.client.post(self.url("join"), {"name": "Tchalim Afi", "groupe": self.g1.pk})
        r = self.client.post(self.url("join"), {"name": "Éric Yao", "groupe": self.g1.pk})
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "complet", status_code=400)

    def test_unlisted_name_rejected_or_flagged(self):
        r = self.client.post(self.url("join"), {"name": "Inconnu Total", "groupe": "none"})
        self.assertEqual(r.status_code, 400)
        self.classe.allow_unlisted = True
        self.classe.save()
        self.client.post(self.url("join"), {"name": "Inconnu Total", "groupe": "none"})
        self.assertFalse(Membre.objects.get(full_name="Inconnu Total").verified)

    def test_closed_link(self):
        self.classe.is_open = False
        self.classe.save()
        r = self.client.post(self.url("join"), {"name": "AMEGAH Kossi", "groupe": "none"})
        self.assertContains(r, "n'accepte plus")
        self.ama.refresh_from_db()
        self.assertIsNone(self.ama.responded_at)

    def test_search_only_returns_unanswered(self):
        self.client.post(self.url("join"), {"name": "Tchalim Afi", "groupe": "none"})
        data = self.client.get(self.url("join_search"), {"q": "a"}).json()
        self.assertEqual(data["results"], [])
        data = self.client.get(self.url("join_search"), {"q": "eric"}).json()
        self.assertEqual([r["name"] for r in data["results"]], ["Éric Yao"])

    def test_honeypot(self):
        self.client.post(self.url("join"), {"name": "AMEGAH Kossi", "groupe": "none", "website": "http://spam"})
        self.ama.refresh_from_db()
        self.assertIsNone(self.ama.responded_at)


class DashboardSecurityTests(Base):
    def test_requires_login(self):
        r = self.client.get(reverse("groupes:dashboard"))
        self.assertEqual(r.status_code, 302)

    def test_cannot_access_other_users_class(self):
        self.client.login(username="autre", password="motdepasse-solide-2")
        for name in ("detail", "members", "groups", "import", "settings", "qr"):
            self.assertEqual(self.client.get(reverse(f"groupes:{name}", args=[self.classe.pk])).status_code, 404, name)
        r = self.client.get(reverse("groupes:export", args=[self.classe.pk, "pdf"]))
        self.assertEqual(r.status_code, 404)

    def test_security_headers(self):
        r = self.client.get(reverse("landing"))
        self.assertIn("frame-ancestors 'none'", r["Content-Security-Policy"])

    def test_all_pages_render(self):
        self.client.login(username="delegue", password="motdepasse-solide-1")
        for name in ("detail", "members", "import", "settings", "activities"):
            r = self.client.get(reverse(f"groupes:{name}", args=[self.classe.pk]))
            self.assertEqual(r.status_code, 200, name)
        self.assertRedirects(
            self.client.get(reverse("groupes:groups", args=[self.classe.pk])),
            reverse("groupes:activities", args=[self.classe.pk]),
        )
        for name in ("dashboard", "create"):
            self.assertEqual(self.client.get(reverse(f"groupes:{name}")).status_code, 200, name)
        self.assertEqual(self.client.get(reverse("groupes:member_edit", args=[self.classe.pk, self.ama.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("groupes:group_edit", args=[self.classe.pk, self.g1.pk])).status_code, 200)
        self.assertEqual(self.client.get(self.classe_join()).status_code, 200)

    def classe_join(self):
        return reverse("groupes:join", args=[self.classe.token])


class OwnerActionsTests(Base):
    def setUp(self):
        super().setUp()
        self.client.login(username="delegue", password="motdepasse-solide-1")

    def test_assign_htmx_and_json_capacity(self):
        url = lambda m: reverse("groupes:member_assign", args=[self.classe.pk, m.pk])
        r = self.client.post(url(self.ama), {"groupe": self.g1.pk}, headers={"HX-Request": "true"})
        self.assertEqual(r.status_code, 200)
        self.client.post(url(self.afi), {"groupe": self.g1.pk})
        r = self.client.post(url(self.yao), {"groupe": self.g1.pk})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.g1.membres.count(), 2)

    def test_add_member_manually_and_duplicate(self):
        url = reverse("groupes:members", args=[self.classe.pk])
        self.client.post(url, {"full_name": "Nouveau Venu", "sex": "F", "reference": "A23", "verified": "on"})
        member = Membre.objects.get(full_name="Nouveau Venu")
        self.assertIsNone(member.groupe)
        self.assertEqual((member.sex, member.reference), ("F", "A23"))
        r = self.client.post(url, {"full_name": "nouveau venu"})
        self.assertContains(r, "déjà dans la liste")

    def test_bulk_groups(self):
        self.client.post(reverse("groupes:groups_bulk", args=[self.classe.pk]), {"count": 5})
        self.assertEqual(self.classe.groupes.count(), 5)

    def test_exports(self):
        self.ama.groupe = self.g1
        self.ama.save()
        self.yao.full_name = "=CMD()"
        self.yao.save()
        expectations = {"pdf": b"%PDF", "xlsx": b"PK", "docx": b"PK", "csv": b"\xef\xbb\xbfGroupe"}
        for fmt, magic in expectations.items():
            r = self.client.get(reverse("groupes:export", args=[self.classe.pk, fmt]))
            self.assertEqual(r.status_code, 200, fmt)
            self.assertTrue(r.content.startswith(magic), fmt)
            self.assertIn("attachment", r["Content-Disposition"])
        csv_text = self.client.get(reverse("groupes:export", args=[self.classe.pk, "csv"])).content.decode("utf-8-sig")
        self.assertIn("'=CMD()", csv_text)  # injection de formule neutralisée
        self.assertEqual(self.client.get(reverse("groupes:export", args=[self.classe.pk, "exe"])).status_code, 404)

    def test_delete_group_keeps_members(self):
        self.ama.groupe = self.g2
        self.ama.save()
        self.client.post(reverse("groupes:group_delete", args=[self.classe.pk, self.g2.pk]))
        self.ama.refresh_from_db()
        self.assertIsNone(self.ama.groupe)

    def test_regenerate_token(self):
        old = self.classe.token
        self.client.post(reverse("groupes:regenerate", args=[self.classe.pk]))
        self.classe.refresh_from_db()
        self.assertNotEqual(old, self.classe.token)

    def test_owner_can_manage_custom_fields(self):
        url = reverse("groupes:member_fields", args=[self.classe.pk])
        response = self.client.post(url, {"key": "phone", "label": "Téléphone", "kind": "text", "required": "on"})
        self.assertEqual(response.status_code, 302)
        field = self.classe.champs_membres.get(key="phone")
        self.assertEqual(field.label, "Téléphone")
        self.client.post(url, {"field_id": field.pk, "key": "phone", "label": "Téléphone portable", "kind": "text", "required": "on"})
        field.refresh_from_db()
        self.assertEqual(field.label, "Téléphone portable")
        self.client.post(url, {"delete_field": field.pk})
        self.assertFalse(self.classe.champs_membres.filter(pk=field.pk).exists())

    def test_member_list_displays_custom_fields(self):
        field = self.classe.champs_membres.create(key="phone", label="Téléphone", position=1)
        self.ama.custom_data = {field.key: "+22890000000"}
        self.ama.save(update_fields=["custom_data"])

        response = self.client.get(reverse("groupes:members", args=[self.classe.pk]))

        self.assertContains(response, "Téléphone")
        self.assertContains(response, "+22890000000")

    def test_public_member_list_excludes_private_fields_and_search(self):
        public_field = self.classe.champs_membres.create(key="city", label="Ville", position=1, public=True)
        private_field = self.classe.champs_membres.create(key="phone", label="Téléphone privé", position=2, public=False)
        self.ama.custom_data = {public_field.key: "Lomé", private_field.key: "secret-228"}
        self.ama.save(update_fields=["custom_data"])
        url = reverse("groupes:public_members", args=[self.classe.token])

        response = self.client.get(url)
        self.assertContains(response, "Ville")
        self.assertContains(response, "Lomé")
        self.assertNotContains(response, "Téléphone privé")
        self.assertNotContains(response, "secret-228")

        response = self.client.get(url, {"q": "secret-228"})
        self.assertNotContains(response, "AMEGAH Kossi")

    def test_external_submission_requires_owner_approval(self):
        self.classe.external_intake_open = True
        self.classe.save(update_fields=["external_intake_open"])
        response = self.client.post(reverse("groupes:external_join", args=[self.classe.token]), {
            "full_name": "Nouveau Externe",
            "sex": "F",
            "reference": "EXT-1",
            "custom_phone": "+22890000000",
        })
        self.assertEqual(response.status_code, 302)
        request = self.classe.demandes_membres.get(full_name="Nouveau Externe")
        self.assertEqual(request.status, DemandeMembre.Status.PENDING)
        self.assertFalse(Membre.objects.filter(full_name="Nouveau Externe").exists())
        accept = self.client.post(reverse("groupes:demande_review", args=[self.classe.pk, request.pk]), {"action": "accept"})
        self.assertEqual(accept.status_code, 302)
        self.assertTrue(Membre.objects.filter(full_name="Nouveau Externe", classe=self.classe).exists())
        self.assertEqual(Membre.objects.get(full_name="Nouveau Externe").custom_data["phone"], "+22890000000")

    def test_external_submission_can_be_rejected_without_adding_member(self):
        self.classe.external_intake_open = True
        self.classe.save(update_fields=["external_intake_open"])
        self.client.post(reverse("groupes:external_join", args=[self.classe.token]), {
            "full_name": "Externe Refusé",
        })
        request = self.classe.demandes_membres.get(full_name="Externe Refusé")

        response = self.client.post(
            reverse("groupes:demande_review", args=[self.classe.pk, request.pk]),
            {"action": "reject"},
        )

        self.assertEqual(response.status_code, 302)
        request.refresh_from_db()
        self.assertEqual(request.status, DemandeMembre.Status.REJECTED)
        self.assertFalse(Membre.objects.filter(classe=self.classe, full_name="Externe Refusé").exists())

    def test_activity_has_independent_groups_and_public_answers(self):
        activity = Activite.objects.create(classe=self.classe, title="Exposé énergie")
        group = Groupe.objects.create(classe=self.classe, activite=activity, number=1, theme="Solaire")
        activity_two = Activite.objects.create(classe=self.classe, title="Projet réseau")
        other_group = Groupe.objects.create(classe=self.classe, activite=activity_two, number=1)

        detail = self.client.get(reverse("groupes:activity_detail", args=[self.classe.pk, activity.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.context["stats"]["attente"], 3)
        response = self.client.post(reverse("groupes:activity_join", args=[activity.token]), {
            "name": self.ama.full_name, "member_id": self.ama.pk, "groupe": group.pk,
        })
        self.assertRedirects(response, reverse("groupes:activity_join_done", args=[activity.token]))
        Participation.objects.create(activite=activity_two, membre=self.ama, groupe=other_group)
        self.assertEqual(activity.participations.get(membre=self.ama).groupe, group)
        self.assertEqual(activity_two.participations.get(membre=self.ama).groupe, other_group)

    def test_pending_activity_participation_can_submit(self):
        activity = Activite.objects.create(classe=self.classe, title="Exposé hérité")
        group = Groupe.objects.create(classe=self.classe, activite=activity, number=1)
        pending = Participation.objects.create(activite=activity, membre=self.ama)
        search = self.client.get(reverse("groupes:activity_join_search", args=[activity.token]), {"q": "amegah"})
        self.assertEqual([row["id"] for row in search.json()["results"]], [self.ama.pk])
        response = self.client.post(reverse("groupes:activity_join", args=[activity.token]), {
            "name": self.ama.full_name, "member_id": self.ama.pk, "groupe": group.pk,
        })
        self.assertEqual(response.status_code, 302)
        pending.refresh_from_db()
        self.assertEqual(pending.groupe, group)
        self.assertIsNotNone(pending.responded_at)

    def test_activity_group_can_be_edited(self):
        activity = Activite.objects.create(classe=self.classe, title="Exposé énergie")
        group = Groupe.objects.create(classe=self.classe, activite=activity, number=1)
        response = self.client.post(reverse("groupes:activity_group_edit", args=[self.classe.pk, activity.pk, group.pk]), {
            "number": 1, "name": "Équipe soleil", "capacity": 4, "theme": "Énergies renouvelables", "presentation_order": 2,
        })
        self.assertEqual(response.status_code, 302)
        group.refresh_from_db()
        self.assertEqual((group.name, group.theme, group.presentation_order), ("Équipe soleil", "Énergies renouvelables", 2))

    def test_bulk_assign_members_to_activity_group(self):
        activity = Activite.objects.create(classe=self.classe, title="Projet collaboratif")
        group = Groupe.objects.create(classe=self.classe, activite=activity, number=1, capacity=2)
        response = self.client.post(reverse("groupes:members", args=[self.classe.pk]), {
            "bulk_action": "assign", "member_ids": [self.ama.pk, self.afi.pk], "group_id": group.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Participation.objects.filter(activite=activity, groupe=group).count(), 2)

    def test_bulk_assign_respects_capacity(self):
        activity = Activite.objects.create(classe=self.classe, title="Projet limité")
        group = Groupe.objects.create(classe=self.classe, activite=activity, number=1, capacity=1)
        response = self.client.post(reverse("groupes:members", args=[self.classe.pk]), {
            "bulk_action": "assign", "member_ids": [self.ama.pk, self.afi.pk], "group_id": group.pk,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Participation.objects.filter(activite=activity, groupe=group).count(), 0)

    def test_legacy_group_routes_cannot_change_activity_groups(self):
        activity = Activite.objects.create(classe=self.classe, title="Projet réseau")
        group = Groupe.objects.create(classe=self.classe, activite=activity, number=1)
        self.assertEqual(
            self.client.get(reverse("groupes:group_edit", args=[self.classe.pk, group.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(reverse("groupes:group_delete", args=[self.classe.pk, group.pk])).status_code,
            404,
        )
        group.refresh_from_db()
        self.assertEqual(group.activite, activity)

    def test_activity_export_contains_activity_rows(self):
        activity = Activite.objects.create(classe=self.classe, title="Projet solaire")
        group = Groupe.objects.create(classe=self.classe, activite=activity, number=1)
        Participation.objects.create(activite=activity, membre=self.ama, groupe=group, responded_at=timezone.now())
        expectations = {"pdf": b"%PDF", "xlsx": b"PK", "docx": b"PK", "csv": b"\xef\xbb\xbfGroupe"}
        for fmt, magic in expectations.items():
            response = self.client.get(reverse("groupes:activity_export", args=[self.classe.pk, activity.pk, fmt]))
            self.assertEqual(response.status_code, 200, fmt)
            self.assertTrue(response.content.startswith(magic), fmt)
            if fmt == "csv":
                self.assertIn(b"AMEGAH Kossi", response.content)

    def test_class_export_summarizes_all_activities_and_pending_members(self):
        activity = Activite.objects.create(classe=self.classe, title="Projet énergie")
        group = Groupe.objects.create(classe=self.classe, activite=activity, number=1, theme="Solaire")
        Participation.objects.create(activite=activity, membre=self.ama, groupe=group, responded_at=timezone.now())
        csv_text = self.client.get(
            reverse("groupes:export", args=[self.classe.pk, "csv"]),
        ).content.decode("utf-8-sig")
        self.assertIn("Projet énergie · Groupe 1", csv_text)
        self.assertIn("Solaire", csv_text)
        self.assertIn("Éric Yao", csv_text)
        self.assertIn("Pas encore répondu", csv_text)

    def test_public_activity_view_is_read_only_and_revocable(self):
        activity = Activite.objects.create(classe=self.classe, title="Exposé ouvert", is_public=True)
        group = Groupe.objects.create(classe=self.classe, activite=activity, number=1, theme="Énergie")
        Participation.objects.create(activite=activity, membre=self.ama, groupe=group, responded_at=timezone.now())
        url = reverse("groupes:activity_public_view", args=[activity.view_token])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "AMEGAH Kossi")
        self.assertContains(response, "Énergie")
        group_url = reverse("groupes:activity_public_group_view", args=[activity.view_token, group.pk])
        self.assertEqual(self.client.get(group_url).status_code, 200)
        activity.is_public = False
        activity.save(update_fields=["is_public"])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_activity_public_link_can_be_rotated(self):
        activity = Activite.objects.create(classe=self.classe, title="Projet public", is_public=True)
        previous = activity.view_token
        self.client.post(reverse("groupes:activity_view_regenerate", args=[self.classe.pk, activity.pk]))
        activity.refresh_from_db()
        self.assertNotEqual(activity.view_token, previous)
        self.assertEqual(self.client.get(reverse("groupes:activity_public_view", args=[previous])).status_code, 404)

    def test_owner_can_add_existing_account_to_class(self):
        response = self.client.post(reverse("groupes:collaborators", args=[self.classe.pk]), {
            "account": "autre", "role": Collaborateur.Role.SECRETARY,
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Collaborateur.objects.get(classe=self.classe, user=self.other).role, Collaborateur.Role.SECRETARY)

    def test_viewer_can_read_but_cannot_edit_class(self):
        Collaborateur.objects.create(classe=self.classe, user=self.other, role=Collaborateur.Role.VIEWER)
        self.client.logout()
        self.client.login(username="autre", password="motdepasse-solide-2")
        self.assertContains(self.client.get(reverse("groupes:dashboard")), self.classe.name)
        self.assertEqual(self.client.get(reverse("groupes:detail", args=[self.classe.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("groupes:members", args=[self.classe.pk])).status_code, 200)
        response = self.client.post(reverse("groupes:members", args=[self.classe.pk]), {"bulk_action": "verify", "member_ids": [self.ama.pk]})
        self.assertEqual(response.status_code, 404)

    def test_secretary_can_manage_activity_but_not_collaborators(self):
        Collaborateur.objects.create(classe=self.classe, user=self.other, role=Collaborateur.Role.SECRETARY)
        self.client.logout()
        self.client.login(username="autre", password="motdepasse-solide-2")
        self.assertEqual(self.client.get(reverse("groupes:activities", args=[self.classe.pk])).status_code, 200)
        response = self.client.post(reverse("groupes:activity_create", args=[self.classe.pk]), {
            "title": "Projet secrétaire", "kind": "project", "theme": "Sécurité", "is_open": "on",
        })
        self.assertEqual(response.status_code, 302)
        activity = Activite.objects.get(classe=self.classe, title="Projet secrétaire")
        self.assertEqual(
            self.client.post(reverse("groupes:activity_delete", args=[self.classe.pk, activity.pk])).status_code,
            404,
        )
        self.assertEqual(self.client.get(reverse("groupes:collaborators", args=[self.classe.pk])).status_code, 404)
