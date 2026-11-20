from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm
from django.contrib.auth.models import User
from django.utils.crypto import constant_time_compare


class LoginForm(AuthenticationForm):
    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Nom d'utilisateur ou mot de passe incorrect. Vérifie les majuscules et réessaie.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Nom d'utilisateur"
        self.fields["username"].widget.attrs.update({"autocomplete": "username", "autofocus": True})
        self.fields["password"].label = "Mot de passe"
        self.fields["password"].widget.attrs.update({"autocomplete": "current-password"})


class SignupForm(UserCreationForm):
    email = forms.EmailField(
        label="E-mail (facultatif)", required=False,
        help_text="Sert uniquement à réinitialiser ton mot de passe si tu l'oublies.",
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )
    invite_code = forms.CharField(label="Code d'invitation", required=False, strip=True)

    class Meta:
        model = User
        fields = ("username", "email")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "Nom d'utilisateur"
        self.fields["username"].help_text = "Lettres, chiffres et @/./+/-/_ uniquement."
        self.fields["username"].widget.attrs.update({"autocomplete": "username", "autofocus": True})
        self.fields["password1"].label = "Mot de passe"
        self.fields["password1"].widget.attrs["autocomplete"] = "new-password"
        self.fields["password2"].label = "Confirmer le mot de passe"
        self.fields["password2"].help_text = ""
        self.fields["password2"].widget.attrs["autocomplete"] = "new-password"
        if not settings.SIGNUP_INVITE_CODE:
            del self.fields["invite_code"]
        else:
            self.fields["invite_code"].required = True
            self.fields["invite_code"].help_text = "Demande ce code à l'organisateur."

    def clean_invite_code(self):
        code = self.cleaned_data.get("invite_code", "")
        if not constant_time_compare(code, settings.SIGNUP_INVITE_CODE):
            raise forms.ValidationError("Code d'invitation incorrect.")
        return code

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if email and User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Cette adresse est déjà utilisée par un autre compte.")
        return email


class ProfileForm(forms.ModelForm):
    email = forms.EmailField(label="E-mail", required=False, widget=forms.EmailInput(attrs={"autocomplete": "email"}))

    class Meta:
        model = User
        fields = ("email",)

    def clean_email(self):
        email = self.cleaned_data.get("email", "").strip().lower()
        if email and User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("Cette adresse est déjà utilisée par un autre compte.")
        return email


class DeleteAccountForm(forms.Form):
    password = forms.CharField(label="Mot de passe actuel", widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_password(self):
        password = self.cleaned_data["password"]
        if not self.user.check_password(password):
            raise forms.ValidationError("Mot de passe incorrect.")
        return password
