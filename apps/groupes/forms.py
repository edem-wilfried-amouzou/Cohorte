from django import forms
from django.db.models import Q

from .models import Classe, Groupe, Membre, normalize


class ClasseForm(forms.ModelForm):
    class Meta:
        model = Classe
        fields = ["name", "description", "is_open", "deadline", "allow_unlisted"]
        widgets = {
            "deadline": forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["deadline"].input_formats = ["%Y-%m-%dT%H:%M"]
        self.fields["deadline"].help_text = "Après cette date, le lien n'accepte plus de réponses."
        self.fields["name"].widget.attrs.update({"placeholder": "Ex. L3 Informatique, promo 2026", "autofocus": True})
        self.fields["description"].widget.attrs["placeholder"] = "Ex. Groupes du projet de Base de données"


class GroupeForm(forms.ModelForm):
    class Meta:
        model = Groupe
        fields = ["number", "name", "capacity"]

    def __init__(self, *args, classe=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.classe = classe
        self.fields["number"].widget.attrs["min"] = 1
        self.fields["capacity"].widget.attrs["min"] = 1
        self.fields["capacity"].help_text = "Laisse vide pour ne pas limiter."

    def clean_number(self):
        number = self.cleaned_data["number"]
        if not 0 < number <= 500:
            raise forms.ValidationError("Choisis un numéro entre 1 et 500.")
        clash = Groupe.objects.filter(classe=self.classe, number=number).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError(f"Le groupe {number} existe déjà.")
        return number


class BulkGroupsForm(forms.Form):
    count = forms.IntegerField(label="Nombre de groupes", min_value=1, max_value=100)
    capacity = forms.IntegerField(label="Capacité de chacun", min_value=1, max_value=500, required=False)


class MembreForm(forms.ModelForm):
    class Meta:
        model = Membre
        fields = ["full_name", "reference", "groupe", "verified"]
        labels = {"verified": "Nom vérifié"}

    def __init__(self, *args, classe=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.classe = classe
        self.fields["groupe"].queryset = classe.groupes.all()
        self.fields["groupe"].empty_label = "Sans groupe"
        self.fields["groupe"].label = "Groupe"
        self.fields["full_name"].widget.attrs["autocomplete"] = "off"

    def clean_full_name(self):
        name = " ".join(self.cleaned_data["full_name"].split())
        clash = Membre.objects.filter(classe=self.classe, search_name=normalize(name)).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("Cette personne est déjà dans la liste.")
        return name


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
