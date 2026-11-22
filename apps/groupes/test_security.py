from django.contrib.auth.models import User
from django.core import mail
from django.core.cache import cache
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from .models import AuditLog, Classe, Membre
from .throttle import client_ip, too_many


class ClientIpTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()

    @override_settings(TRUSTED_PROXY_COUNT=0)
    def test_without_proxy_uses_remote_addr_and_ignores_header(self):
        request = self.rf.get("/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="6.6.6.6")
        self.assertEqual(client_ip(request), "10.0.0.1")

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_behind_proxy_uses_last_forwarded_ip_not_client_supplied_one(self):
        request = self.rf.get("/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="6.6.6.6, 203.0.113.9")
        self.assertEqual(client_ip(request), "203.0.113.9")

    @override_settings(TRUSTED_PROXY_COUNT=1)
    def test_two_clients_behind_proxy_do_not_share_a_counter(self):
        cache.clear()
        a = self.rf.get("/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.1")
        b = self.rf.get("/", REMOTE_ADDR="10.0.0.1", HTTP_X_FORWARDED_FOR="203.0.113.2")
        for _ in range(3):
            too_many(a, "t", 2)
        self.assertTrue(too_many(a, "t", 2))
        self.assertFalse(too_many(b, "t", 2))


class AuthHardeningTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("delegue", password="motdepasse-solide-1", email="d@example.com")

    def test_login_is_throttled_per_username(self):
        url = reverse("accounts:login")
        for _ in range(8):
            r = self.client.post(url, {"username": "delegue", "password": "faux"})
            self.assertEqual(r.status_code, 200)
        r = self.client.post(url, {"username": "delegue", "password": "motdepasse-solide-1"})
        self.assertEqual(r.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_works_and_is_logged(self):
        r = self.client.post(reverse("accounts:login"), {"username": "delegue", "password": "motdepasse-solide-1"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(AuditLog.objects.filter(action="connexion", user=self.user).exists())

    @override_settings(SIGNUP_INVITE_CODE="secret-2026")
    def test_signup_requires_invite_code_when_configured(self):
        data = {"username": "nouveau", "password1": "un-mot-de-passe-long-9", "password2": "un-mot-de-passe-long-9"}
        self.assertEqual(self.client.post(reverse("accounts:signup"), {**data, "invite_code": "faux"}).status_code, 200)
        self.assertFalse(User.objects.filter(username="nouveau").exists())
        self.client.post(reverse("accounts:signup"), {**data, "invite_code": "secret-2026"})
        self.assertTrue(User.objects.filter(username="nouveau").exists())

    def test_signup_is_throttled(self):
        for i in range(10):
            self.client.post(reverse("accounts:signup"), {"username": f"u{i}", "password1": "x", "password2": "x"})
        r = self.client.post(reverse("accounts:signup"), {
            "username": "legit", "password1": "un-mot-de-passe-long-9", "password2": "un-mot-de-passe-long-9",
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username="legit").exists())

    def test_password_reset_sends_mail_without_revealing_accounts(self):
        url = reverse("accounts:password_reset")
        r = self.client.post(url, {"email": "d@example.com"})
        self.assertRedirects(r, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("/compte/mot-de-passe/reinitialiser/", mail.outbox[0].body)
        r = self.client.post(url, {"email": "inconnu@example.com"})
        self.assertRedirects(r, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)

    def test_duplicate_email_rejected_at_signup(self):
        r = self.client.post(reverse("accounts:signup"), {
            "username": "autre", "email": "D@example.com",
            "password1": "un-mot-de-passe-long-9", "password2": "un-mot-de-passe-long-9",
        })
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username="autre").exists())


class AccountDataTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("delegue", password="motdepasse-solide-1")
        self.client.force_login(self.user)
        self.classe = Classe.objects.create(owner=self.user, name="L3")
        Membre.objects.create(classe=self.classe, first_name="Kossi", last_name="AMEGAH")

    def test_export_contains_members(self):
        r = self.client.get(reverse("accounts:export_data"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("AMEGAH", r.content.decode())

    def test_delete_account_requires_password(self):
        url = reverse("accounts:delete_account")
        self.assertEqual(self.client.post(url, {"d-password": "faux"}).status_code, 400)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())
        r = self.client.post(url, {"d-password": "motdepasse-solide-1"})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())
        self.assertFalse(Classe.objects.filter(pk=self.classe.pk).exists())


class PublicListPrivacyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("delegue", password="motdepasse-solide-1")
        self.classe = Classe.objects.create(owner=self.user, name="L3", public_show_reference=False)
        Membre.objects.create(classe=self.classe, first_name="Kossi", last_name="AMEGAH", reference="MAT-4242", custom_data={"secret": "zebra"})

    def test_reference_hidden_and_not_searchable(self):
        url = reverse("groupes:public_members", args=[self.classe.token])
        page = self.client.get(url).content.decode()
        self.assertNotIn("MAT-4242", page)
        self.assertNotIn("Matricule</th>", page)
        found = self.client.get(url, {"q": "MAT-4242"}).content.decode()
        self.assertNotIn("AMEGAH", found)

    def test_private_custom_fields_not_searchable(self):
        url = reverse("groupes:public_members", args=[self.classe.token])
        self.assertNotIn("AMEGAH", self.client.get(url, {"q": "zebra"}).content.decode())

    def test_tokens_are_long_enough(self):
        self.assertGreaterEqual(len(self.classe.token), 22)


class InfraTests(TestCase):
    def test_health(self):
        r = self.client.get(reverse("health"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})

    def test_service_worker_scope_and_manifest(self):
        r = self.client.get("/sw.js")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r["Service-Worker-Allowed"], "/")
        self.assertIn("javascript", r["Content-Type"])

    def test_csp_has_no_external_script_host(self):
        r = self.client.get(reverse("landing"))
        csp = r["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertNotIn("jsdelivr", csp)
        self.assertIn("object-src 'none'", csp)

    def test_public_pages_render(self):
        for name in ("privacy", "offline", "accounts:password_reset", "accounts:login", "accounts:signup"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)
