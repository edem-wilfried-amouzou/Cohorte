from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class AccountTests(TestCase):
    def test_signup_logs_in(self):
        r = self.client.post(reverse("accounts:signup"), {
            "username": "nouveau", "password1": "un-mot-de-passe-long-9", "password2": "un-mot-de-passe-long-9",
        })
        self.assertRedirects(r, reverse("groupes:dashboard"))
        self.assertTrue(User.objects.filter(username="nouveau").exists())

    def test_weak_password_rejected(self):
        r = self.client.post(reverse("accounts:signup"), {"username": "x", "password1": "123", "password2": "123"})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(User.objects.filter(username="x").exists())

    def test_logout_requires_post(self):
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
