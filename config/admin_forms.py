from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.auth.password_validation import validate_password


class StaffProfileForm(forms.Form):
    username = forms.CharField(label="Identifiant", max_length=150)
    first_name = forms.CharField(label="Prénom", max_length=150, required=False)
    last_name = forms.CharField(label="Nom", max_length=150, required=False)
    email = forms.EmailField(label="E-mail", required=False)
    password1 = forms.CharField(label="Mot de passe provisoire", widget=forms.PasswordInput, strip=False)
    password2 = forms.CharField(label="Confirmer le mot de passe", widget=forms.PasswordInput, strip=False)
    permissions = forms.ModelMultipleChoiceField(
        label="Permissions du profil",
        queryset=Permission.objects.filter(content_type__app_label="groupes").select_related("content_type").order_by("content_type__model", "codename"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Ces permissions contrôlent les opérations disponibles dans l’administration Django.",
    )

    def clean_username(self):
        username = self.cleaned_data["username"].strip()
        User = get_user_model()
        if User.objects.filter(**{f"{User.USERNAME_FIELD}__iexact": username}).exists():
            raise forms.ValidationError("Cet identifiant est déjà utilisé.")
        return username

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password1") and cleaned.get("password2"):
            if cleaned["password1"] != cleaned["password2"]:
                self.add_error("password2", "Les mots de passe ne correspondent pas.")
            else:
                User = get_user_model()
                candidate = User(
                    **{User.USERNAME_FIELD: cleaned.get("username", "")},
                    email=cleaned.get("email", ""),
                    first_name=cleaned.get("first_name", ""),
                    last_name=cleaned.get("last_name", ""),
                )
                try:
                    validate_password(cleaned["password1"], candidate)
                except forms.ValidationError as error:
                    self.add_error("password1", error)
        return cleaned

    def save(self):
        if not self.is_valid():
            raise ValueError("Le formulaire doit être valide avant sa sauvegarde.")
        User = get_user_model()
        user = User(
            **{User.USERNAME_FIELD: self.cleaned_data["username"]},
            email=self.cleaned_data.get("email", ""),
            first_name=self.cleaned_data.get("first_name", ""),
            last_name=self.cleaned_data.get("last_name", ""),
            is_staff=True,
            is_superuser=False,
            is_active=True,
        )
        user.set_password(self.cleaned_data["password1"])
        user.save()
        user.user_permissions.set(self.cleaned_data["permissions"])
        return user
