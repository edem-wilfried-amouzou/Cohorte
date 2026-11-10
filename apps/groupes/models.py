import secrets
import unicodedata

from django.conf import settings
from django.db import models
from django.utils import timezone


def normalize(value: str) -> str:
    """Minuscules, sans accents, espaces simplifiés : sert à comparer et chercher les noms."""
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join(value.casefold().split())


def new_token() -> str:
    return secrets.token_urlsafe(9)


class Classe(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="classes")
    name = models.CharField("Nom", max_length=120)
    description = models.CharField("Description", max_length=240, blank=True)
    token = models.CharField(max_length=24, unique=True, default=new_token, editable=False)
    is_open = models.BooleanField("Lien ouvert aux réponses", default=True)
    deadline = models.DateTimeField("Date limite", null=True, blank=True)
    allow_unlisted = models.BooleanField(
        "Accepter les noms absents de la liste",
        default=False,
        help_text="Ces personnes sont ajoutées avec la mention « À vérifier ».",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "classe"

    def __str__(self):
        return self.name

    @property
    def accepting(self) -> bool:
        if not self.is_open:
            return False
        return self.deadline is None or self.deadline > timezone.now()

    def regenerate_token(self):
        self.token = new_token()
        self.save(update_fields=["token"])


class Activite(models.Model):
    class Kind(models.TextChoices):
        PRESENTATION = "presentation", "Exposé"
        PROJECT = "project", "Projet"
        OTHER = "other", "Autre"

    classe = models.ForeignKey(Classe, on_delete=models.CASCADE, related_name="activites")
    title = models.CharField("Nom", max_length=140)
    kind = models.CharField("Type", max_length=20, choices=Kind.choices, default=Kind.PRESENTATION)
    theme = models.CharField("Thème", max_length=180, blank=True)
    token = models.CharField(max_length=24, unique=True, default=new_token, editable=False)
    is_open = models.BooleanField("Lien ouvert", default=True)
    deadline = models.DateTimeField("Date limite", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "activité"

    def __str__(self):
        return self.title

    @property
    def accepting(self) -> bool:
        return self.is_open and (self.deadline is None or self.deadline > timezone.now())

    def regenerate_token(self):
        self.token = new_token()
        self.save(update_fields=["token"])


class Groupe(models.Model):
    classe = models.ForeignKey(Classe, on_delete=models.CASCADE, related_name="groupes")
    activite = models.ForeignKey(
        Activite, null=True, blank=True, on_delete=models.CASCADE, related_name="groupes",
    )
    number = models.PositiveIntegerField("Numéro")
    name = models.CharField("Nom (facultatif)", max_length=80, blank=True)
    capacity = models.PositiveSmallIntegerField("Capacité maximale", null=True, blank=True)
    theme = models.CharField("Thème", max_length=180, blank=True)
    presentation_order = models.PositiveSmallIntegerField("Ordre de passage", null=True, blank=True)

    class Meta:
        ordering = ["number"]
        constraints = [
            models.UniqueConstraint(
                fields=["classe", "number"], condition=models.Q(activite__isnull=True),
                name="groupe_numero_unique_hors_activite",
            ),
            models.UniqueConstraint(
                fields=["activite", "number"], condition=models.Q(activite__isnull=False),
                name="groupe_numero_unique_par_activite",
            ),
        ]

    def __str__(self):
        return self.label

    @property
    def short_label(self) -> str:
        return f"Groupe {self.number}"

    @property
    def label(self) -> str:
        return f"{self.short_label} ({self.name})" if self.name else self.short_label


class MembreQuerySet(models.QuerySet):
    def sans_groupe(self):
        return self.filter(groupe__isnull=True)

    def en_attente(self):
        return self.filter(groupe__isnull=True, responded_at__isnull=True)

    def declares_sans_groupe(self):
        return self.filter(groupe__isnull=True, responded_at__isnull=False)

    def affectes(self):
        return self.filter(groupe__isnull=False)

    def search(self, query: str):
        needle = normalize(query)
        return self.filter(search_name__contains=needle) if needle else self


class Membre(models.Model):
    class Sex(models.TextChoices):
        FEMALE = "F", "Femme"
        MALE = "M", "Homme"
        OTHER = "X", "Autre / non précisé"

    class Source(models.TextChoices):
        IMPORT = "import", "Import"
        MANUAL = "manual", "Ajout manuel"
        LINK = "link", "Lien public"

    classe = models.ForeignKey(Classe, on_delete=models.CASCADE, related_name="membres")
    full_name = models.CharField("Nom complet", max_length=120)
    sex = models.CharField("Sexe", max_length=1, choices=Sex.choices, blank=True)
    search_name = models.CharField(max_length=140, editable=False, db_index=True)
    reference = models.CharField("Matricule", max_length=40, blank=True)
    groupe = models.ForeignKey(Groupe, null=True, blank=True, on_delete=models.SET_NULL, related_name="membres")
    responded_at = models.DateTimeField(null=True, blank=True)
    verified = models.BooleanField(default=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = MembreQuerySet.as_manager()

    class Meta:
        ordering = ["search_name"]
        constraints = [
            models.UniqueConstraint(fields=["classe", "search_name"], name="membre_nom_unique_par_classe"),
        ]

    def __str__(self):
        return self.full_name

    def save(self, *args, **kwargs):
        self.full_name = " ".join(self.full_name.split())
        self.search_name = normalize(self.full_name)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"full_name", "search_name"}
        super().save(*args, **kwargs)

    @property
    def status(self) -> str:
        if self.groupe_id:
            return "affecte"
        return "sans_groupe" if self.responded_at else "en_attente"

    @property
    def status_label(self) -> str:
        return {
            "affecte": "Affecté",
            "sans_groupe": "Sans groupe",
            "en_attente": "Pas encore répondu",
        }[self.status]


class Participation(models.Model):
    activite = models.ForeignKey(Activite, on_delete=models.CASCADE, related_name="participations")
    membre = models.ForeignKey(Membre, on_delete=models.CASCADE, related_name="participations")
    groupe = models.ForeignKey(
        Groupe, null=True, blank=True, on_delete=models.SET_NULL, related_name="participations",
    )
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["membre__search_name"]
        constraints = [
            models.UniqueConstraint(fields=["activite", "membre"], name="participation_unique_par_activite"),
        ]

    @property
    def status_label(self) -> str:
        if self.groupe_id:
            return "Affecté"
        return "Sans groupe" if self.responded_at else "Pas encore répondu"
