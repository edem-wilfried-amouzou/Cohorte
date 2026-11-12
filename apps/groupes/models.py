import re
import secrets
import unicodedata

from django.conf import settings
from django.db import models
from django.db.models import F
from django.utils import timezone


def normalize(value: str) -> str:
    """Minuscules, sans accents, espaces simplifiés : sert à comparer et chercher les noms."""
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join(value.casefold().split())


def compose_full_name(first_name: str, last_name: str) -> str:
    """Affichage « Prénoms NOM » (prénoms d'abord, nom de famille ensuite)."""
    return " ".join(part for part in (" ".join((first_name or "").split()), " ".join((last_name or "").split())) if part)


def split_full_name(raw: str, last_first: bool = False) -> tuple[str, str]:
    """Sépare un nom complet en (nom, prénoms).

    Les mots écrits en MAJUSCULES forment le nom de famille (« Koffi Alexis ADIGBLI » ou
    « ADIGBLI Koffi Alexis »). Sans indice de casse, l'ordre d'origine est conservé (`last_first` dit si le nom vient en premier).
    """
    tokens = (raw or "").split()
    if len(tokens) <= 1:
        return (" ".join(tokens), "")
    is_upper = [len(re.sub(r"\W", "", t)) > 1 and t.isupper() for t in tokens]
    if any(is_upper) and not all(is_upper):
        last = " ".join(t for t, up in zip(tokens, is_upper) if up)
        first = " ".join(t for t, up in zip(tokens, is_upper) if not up)
        return (last, first)
    if last_first:
        return (tokens[0], " ".join(tokens[1:]))
    return (tokens[-1], " ".join(tokens[:-1]))


def name_key(value: str) -> str:
    """Clé de comparaison indépendante de l'ordre des mots : « Kossi AMEGAH » = « amegah kossi »."""
    return " ".join(sorted(normalize(value).split()))


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
    external_intake_open = models.BooleanField(
        "Autoriser les modifications depuis le lien partagé", default=False,
        help_text="Tout le monde voit la liste. Si coché, chacun peut aussi corriger ses informations et s'ajouter.",
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


class Collaborateur(models.Model):
    class Role(models.TextChoices):
        CO_DELEGATE = "co_delegate", "Co-délégué (tout sauf réglages et équipe)"
        SECRETARY = "secretary", "Secrétaire (modifie, sans supprimer ni importer)"
        VIEWER = "viewer", "Consultation seulement"

    classe = models.ForeignKey(Classe, on_delete=models.CASCADE, related_name="collaborateurs")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="classes_partagees")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.SECRETARY)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["classe", "user"], name="collaborateur_unique_par_classe"),
        ]


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
    view_token = models.CharField(max_length=24, unique=True, default=new_token, editable=False)
    is_public = models.BooleanField("Autoriser la consultation publique", default=False)
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

    def regenerate_view_token(self):
        self.view_token = new_token()
        self.save(update_fields=["view_token"])


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

    def by_name(self, name: str):
        """Personnes dont le nom correspond exactement, quel que soit l'ordre des mots."""
        tokens = sorted(normalize(name).split())
        if not tokens:
            return self.none()
        candidates = self
        for token in tokens:
            candidates = candidates.filter(search_name__contains=token)
        ids = [pk for pk, key in candidates.values_list("pk", "search_name") if sorted(key.split()) == tokens]
        return self.filter(pk__in=ids)

    def search(self, query: str):
        """Cherche chaque mot dans n'importe quel ordre. Résultats dans l'ordre de la liste (N°, Nom, Prénoms)."""
        qs = self
        for token in normalize(query).split():
            qs = qs.filter(search_name__contains=token)
        return qs.order_by(F("number").asc(nulls_last=True), "last_name", "first_name", "search_name")


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
    number = models.PositiveIntegerField("N°", null=True, blank=True)
    last_name = models.CharField("Nom", max_length=80, blank=True)
    first_name = models.CharField("Prénoms", max_length=100, blank=True)
    full_name = models.CharField("Nom complet", max_length=120)
    sex = models.CharField("Sexe", max_length=1, choices=Sex.choices, blank=True)
    search_name = models.CharField(max_length=140, editable=False, db_index=True)
    reference = models.CharField("Matricule", max_length=40, blank=True)
    custom_data = models.JSONField(default=dict, blank=True)
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
        self.first_name = " ".join((self.first_name or "").split())
        self.last_name = " ".join((self.last_name or "").split())
        if not (self.first_name or self.last_name):
            # Seul le nom complet est connu (lien public, demande externe...) : on le sépare.
            self.last_name, self.first_name = split_full_name(self.full_name)
        # Le nom complet s'affiche toujours « Prénoms NOM ».
        self.full_name = compose_full_name(self.first_name, self.last_name)[:120]
        self.search_name = normalize(self.full_name)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {
                "full_name", "search_name", "first_name", "last_name",
            }
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


class ChampMembre(models.Model):
    class Kind(models.TextChoices):
        TEXT = "text", "Texte court"
        TEXTAREA = "textarea", "Texte long"
        NUMBER = "number", "Nombre"
        DATE = "date", "Date"
        EMAIL = "email", "E-mail"
        CHOICE = "choice", "Liste de choix"

    classe = models.ForeignKey(Classe, on_delete=models.CASCADE, related_name="champs_membres")
    key = models.SlugField(max_length=60)
    label = models.CharField(max_length=100)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.TEXT)
    required = models.BooleanField(default=False)
    public = models.BooleanField(default=True)
    choices = models.JSONField(default=list, blank=True)
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["classe", "key"], name="champ_membre_key_unique_par_classe"),
        ]

    def __str__(self):
        return self.label


class DemandeMembre(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "À examiner"
        ACCEPTED = "accepted", "Acceptée"
        REJECTED = "rejected", "Refusée"

    classe = models.ForeignKey(Classe, on_delete=models.CASCADE, related_name="demandes_membres")
    full_name = models.CharField("Nom complet", max_length=120)
    sex = models.CharField("Sexe", max_length=1, choices=Membre.Sex.choices, blank=True)
    reference = models.CharField("Matricule", max_length=40, blank=True)
    custom_data = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="demandes_membres_traitees",
    )

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.full_name} · {self.get_status_display()}"