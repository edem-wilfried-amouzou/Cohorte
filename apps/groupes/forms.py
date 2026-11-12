import re
from django import forms
from django.contrib.auth import get_user_model
from django.db.models import Q

from .models import Activite, ChampMembre, Classe, Collaborateur, Groupe, Membre, compose_full_name, normalize


class ClasseForm(forms.ModelForm):
    class Meta:
        model = Classe
        fields = ["name", "description", "allow_unlisted", "external_intake_open"]
        widgets = {
            "deadline": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.update({"placeholder": "Ex. L3 Informatique, promo 2026", "autofocus": True})
        self.fields["description"].widget.attrs["placeholder"] = "Ex. Licence informatique, année 2026"


class GroupeForm(forms.ModelForm):
    class Meta:
        model = Groupe
        fields = ["number", "name", "capacity", "theme", "presentation_order"]

    def __init__(self, *args, classe=None, activite=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.classe = classe
        self.activite = activite
        self.fields["number"].widget.attrs["min"] = 1
        self.fields["capacity"].widget.attrs["min"] = 1
        self.fields["capacity"].help_text = "Laisse vide pour ne pas limiter."

    def clean_number(self):
        number = self.cleaned_data["number"]
        if not 0 < number <= 500:
            raise forms.ValidationError("Choisis un numéro entre 1 et 500.")
        clash = Groupe.objects.filter(classe=self.classe, activite=self.activite, number=number).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(f"Le groupe {number} existe déjà.")
        return number


class BulkGroupsForm(forms.Form):
    count = forms.IntegerField(label="Nombre de groupes", min_value=1, max_value=100)
    capacity = forms.IntegerField(label="Capacité de chacun", min_value=1, max_value=500, required=False)


class ActiviteForm(forms.ModelForm):
    class Meta:
        model = Activite
        fields = ["title", "kind", "theme", "is_open", "deadline", "is_public"]
        widgets = {
            "deadline": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["deadline"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["is_public"].help_text = "Le lien de consultation affiche les thèmes, les groupes et leurs membres, sans permettre de modifier les réponses."


class CollaborateurForm(forms.Form):
    account = forms.CharField(label="Nom d’utilisateur ou e-mail", max_length=254)
    role = forms.ChoiceField(label="Rôle", choices=Collaborateur.Role.choices)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].help_text = (
            "Co-délégué : gère la liste, les groupes et les activités, mais pas les réglages, l'équipe ni la suppression de la classe. "
            "Secrétaire : modifie la liste et les groupes, sans supprimer de personnes ni importer. "
            "Lecteur : consultation seulement."
        )

    def clean_account(self):
        value = self.cleaned_data["account"].strip()
        User = get_user_model()
        user = User.objects.filter(Q(username__iexact=value) | Q(email__iexact=value)).first()
        if user is None:
            raise forms.ValidationError("Aucun compte ne correspond à ce nom ou à cet e-mail.")
        self.user = user
        return value


class MembreForm(forms.ModelForm):
    class Meta:
        model = Membre
        fields = ["number", "last_name", "first_name", "sex", "reference", "verified"]
        labels = {"number": "N°", "last_name": "Nom", "first_name": "Prénoms", "verified": "Nom vérifié"}

    def __init__(self, *args, classe=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.classe = classe
        self.fields["number"].widget.attrs.update({"min": 1, "inputmode": "numeric"})
        self.fields["last_name"].widget.attrs.update({"autocomplete": "off", "placeholder": "Ex. KLUTSE"})
        self.fields["first_name"].widget.attrs.update({"autocomplete": "off", "placeholder": "Ex. Abatékoe Berma"})
        self.extra_custom_data = {}
        if classe is not None:
            for field in classe.champs_membres.order_by("position", "id"):
                value = self.instance.custom_data.get(field.key) if self.instance and self.instance.pk else ""
                widget = forms.TextInput(attrs={"placeholder": field.label})
                if field.kind == ChampMembre.Kind.TEXTAREA:
                    widget = forms.Textarea(attrs={"rows": 3, "placeholder": field.label})
                elif field.kind == ChampMembre.Kind.NUMBER:
                    widget = forms.NumberInput(attrs={"placeholder": field.label})
                elif field.kind == ChampMembre.Kind.EMAIL:
                    widget = forms.EmailInput(attrs={"placeholder": field.label})
                elif field.kind == ChampMembre.Kind.DATE:
                    widget = forms.DateInput(attrs={"type": "date", "placeholder": field.label})
                elif field.kind == ChampMembre.Kind.CHOICE:
                    choices = [(choice, choice) for choice in (field.choices or [])]
                    widget = forms.Select(choices=choices)
                field_kwargs = {"label": field.label, "required": field.required, "initial": value, "widget": widget}
                if field.kind == ChampMembre.Kind.CHOICE:
                    field_kwargs["choices"] = choices or [("", "—")]
                self.fields[field.key] = forms.CharField(**field_kwargs) if field.kind in {
                    ChampMembre.Kind.TEXT, ChampMembre.Kind.TEXTAREA, ChampMembre.Kind.EMAIL,
                } else forms.IntegerField(**field_kwargs) if field.kind == ChampMembre.Kind.NUMBER else forms.DateField(**field_kwargs) if field.kind == ChampMembre.Kind.DATE else forms.ChoiceField(**field_kwargs)
                if field.kind in {ChampMembre.Kind.TEXT, ChampMembre.Kind.TEXTAREA, ChampMembre.Kind.EMAIL, ChampMembre.Kind.NUMBER, ChampMembre.Kind.DATE}:
                    self.fields[field.key].widget.attrs.update({"placeholder": field.label})

    def clean(self):
        cleaned = super().clean()
        if self.classe is None:
            return cleaned
        first = " ".join((cleaned.get("first_name") or "").split())
        last = " ".join((cleaned.get("last_name") or "").split())
        if not (first or last):
            raise forms.ValidationError("Indique au moins un nom ou des prénoms.")
        name = compose_full_name(first, last)
        clash = Membre.objects.filter(classe=self.classe).by_name(name).exclude(pk=self.instance.pk)
        if clash.exists():
            self.add_error("last_name", "Cette personne est déjà dans la liste.")
        for field in self.classe.champs_membres.order_by("position", "id"):
            key = field.key
            if key in self.fields and key in cleaned:
                value = cleaned.get(key)
                if value in (None, ""):
                    self.extra_custom_data[key] = ""
                else:
                    self.extra_custom_data[key] = value
        return cleaned


class ChampMembreForm(forms.ModelForm):
    class Meta:
        model = ChampMembre
        fields = ["key", "label", "kind", "required", "public"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["key"].help_text = "Identifiant unique, sans espace ni accent."
        self.fields["label"].help_text = "Nom affiché dans la liste et dans le formulaire externe."

    def clean_key(self):
        value = self.cleaned_data["key"].strip()
        normalized = re.sub(r"[^a-z0-9_]+", "_", value.lower()).strip("_")
        if not normalized:
            raise forms.ValidationError("Le code doit contenir au moins un caractère alphanumérique.")
        return normalized


class ExternalRequestForm(forms.Form):
    full_name = forms.CharField(label="Nom complet", max_length=120)
    sex = forms.ChoiceField(label="Sexe", choices=Membre.Sex.choices, required=False)
    reference = forms.CharField(label="Matricule", max_length=40, required=False)

    def __init__(self, *args, classe=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.classe = classe
        if classe is None:
            return
        for field in classe.champs_membres.filter(public=True).order_by("position", "id"):
            if field.kind == ChampMembre.Kind.CHOICE:
                choices = [(choice, choice) for choice in (field.choices or [])]
                self.fields[field.key] = forms.ChoiceField(label=field.label, required=field.required, choices=choices or [('', '—')])
            elif field.kind == ChampMembre.Kind.TEXTAREA:
                self.fields[field.key] = forms.CharField(label=field.label, required=field.required, widget=forms.Textarea(attrs={"rows": 3}))
            elif field.kind == ChampMembre.Kind.NUMBER:
                self.fields[field.key] = forms.IntegerField(label=field.label, required=field.required)
            elif field.kind == ChampMembre.Kind.DATE:
                self.fields[field.key] = forms.DateField(label=field.label, required=field.required, widget=forms.DateInput(attrs={"type": "date"}))
            elif field.kind == ChampMembre.Kind.EMAIL:
                self.fields[field.key] = forms.EmailField(label=field.label, required=field.required)
            else:
                self.fields[field.key] = forms.CharField(label=field.label, required=field.required)


class PublicListForm(ExternalRequestForm):
    """Formulaire du lien public : modifier ses informations (edit) ou s'ajouter à la liste (add)."""

    def __init__(self, *args, classe=None, mode="edit", **kwargs):
        super().__init__(*args, classe=classe, **kwargs)
        self.mode = mode
        self.fields.pop("full_name", None)
        self.fields["sex"].choices = [("", "—"), *Membre.Sex.choices]
        self.fields["sex"].required = False
        self.fields["reference"].max_length = 40
        if mode == "add":
            names = {
                "last_name": forms.CharField(label="Nom", max_length=80, required=False),
                "first_name": forms.CharField(label="Prénoms", max_length=100, required=False),
            }
            self.fields = {**names, **self.fields}

    def clean(self):
        cleaned = super().clean()
        if self.mode == "add":
            first = " ".join((cleaned.get("first_name") or "").split())
            last = " ".join((cleaned.get("last_name") or "").split())
            if not (first or last):
                raise forms.ValidationError("Indique ton nom et tes prénoms.")
            cleaned["first_name"], cleaned["last_name"] = first, last
        return cleaned


class ImportForm(forms.Form):
    file = forms.FileField(label="Fichier (.xlsx, .csv ou .txt)", required=False)
    text = forms.CharField(
        label="Ou colle les noms, un par ligne",
        required=False,
        widget=forms.Textarea(attrs={"rows": 8, "placeholder": "AMEGAH Kossi\nTCHALIM Afi\n..."}),
    )

    def clean(self):
        data = super().clean()
        if not data.get("file") and not (data.get("text") or "").strip():
            raise forms.ValidationError("Choisis un fichier ou colle une liste de noms.")
        return data