import re
import secrets
from pathlib import PurePath
from typing import TYPE_CHECKING
from uuid import uuid4
import unicodedata

from django.conf import settings
from django.db import models
from django.db.models import F
from django.db.models.functions import Lower
from django.utils import timezone

MAX_DOCUMENT_SIZE = 25 * 1024 * 1024
MAX_CLASS_STORAGE = 250 * 1024 * 1024
MAX_ARCHIVE_SIZE = 100 * 1024 * 1024


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
    return secrets.token_urlsafe(16)


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
    public_show_reference = models.BooleanField(
        "Afficher les matricules sur la liste partagée", default=True,
        help_text="Décoche pour masquer les matricules aux personnes qui ouvrent le lien.",
    )
    public_documents = models.BooleanField(
        "Afficher la section Documents sur la page publique", default=False,
        help_text="Active la rubrique Documents sur la page complète partagée avec la classe.",
    )
    allow_public_folder_creation = models.BooleanField(
        "Autoriser la création de dossiers depuis le lien public", default=False,
        help_text="Les visiteurs peuvent créer des dossiers publics, à la racine ou dans un dossier visible.",
    )
    allow_public_uploads = models.BooleanField(
        "Autoriser les dépôts depuis le lien public", default=False,
        help_text="Autorise les dépôts dans les dossiers publics dont l’option d’ajout est aussi activée.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "classe"
        indexes = [models.Index(fields=["owner", "-created_at"], name="classe_owner_created_idx")]

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
        indexes = [models.Index(fields=["user", "classe"], name="collaborateur_user_classe_idx")]


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
        indexes = [models.Index(fields=["classe", "-created_at"], name="activite_classe_created_idx")]

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
    leader = models.ForeignKey(
        "Membre", null=True, blank=True, on_delete=models.SET_NULL, related_name="groupes_diriges",
        verbose_name="Chef de groupe",
    )

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
    if TYPE_CHECKING:
        # Django ajoute cet attribut à partir du ForeignKey `groupe`.
        groupe_id: int | None

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
        indexes = [models.Index(F("classe"), Lower("last_name"), Lower("first_name"), name="membre_classe_name_order_idx")]

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
    if TYPE_CHECKING:
        # Django ajoute cet attribut à partir du ForeignKey `groupe`.
        groupe_id: int | None

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
        indexes = [
            models.Index(fields=["activite", "groupe"], name="part_act_group_idx"),
            models.Index(fields=["activite", "responded_at"], name="part_act_answer_idx"),
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
        indexes = [models.Index(fields=["classe", "status", "-submitted_at"], name="demande_classe_status_date_idx")]

    @property
    def status_label(self) -> str:
        return dict(self.Status.choices).get(self.status, self.status)

    def __str__(self):
        return f"{self.full_name} · {self.status_label}"


class Commentaire(models.Model):
    """Note laissée sur une activité (consignes, rappels). Peut être affichée sur les pages publiques."""

    activite = models.ForeignKey(Activite, on_delete=models.CASCADE, related_name="commentaires")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="commentaires")
    body = models.CharField("Message", max_length=1000)
    is_public = models.BooleanField(
        "Afficher sur la page publique", default=False,
        help_text="Visible par les personnes qui ouvrent le lien de l'activité.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.body[:60]


def document_upload_path(instance, filename):
    """Range les pièces jointes sous une clé courte et imprévisible."""
    suffix = PurePath((filename or "document").replace("\\", "/")).suffix.lower()
    suffix = re.sub(r"[^a-z0-9.]", "", suffix)[:16]
    folder_id = instance.folder_id or "racine"
    return f"documents/{instance.classe_id}/{folder_id}/{uuid4().hex}{suffix}"


class DossierDocument(models.Model):
    """Dossier de fichiers, éventuellement lié à une activité et partagé publiquement."""

    classe = models.ForeignKey(Classe, on_delete=models.CASCADE, related_name="dossiers_documents")
    activite = models.ForeignKey(
        Activite, null=True, blank=True, on_delete=models.CASCADE, related_name="dossiers_documents",
    )
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="sous_dossiers",
    )
    name = models.CharField("Nom du dossier", max_length=100)
    is_public = models.BooleanField("Visible sur la page publique", default=False)
    allow_public_downloads = models.BooleanField("Autoriser le téléchargement public", default=True)
    allow_public_uploads = models.BooleanField("Autoriser l’ajout public de fichiers", default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["classe", "name"],
                condition=models.Q(parent__isnull=True, activite__isnull=True),
                name="dossier_nom_unique_racine_classe",
            ),
            models.UniqueConstraint(
                fields=["classe", "activite", "name"],
                condition=models.Q(parent__isnull=True, activite__isnull=False),
                name="dossier_nom_unique_racine_activite",
            ),
            models.UniqueConstraint(
                fields=["classe", "parent", "name"], condition=models.Q(parent__isnull=False),
                name="dossier_nom_unique_par_parent",
            ),
        ]
        indexes = [models.Index(fields=["classe", "parent"], name="dossier_classe_parent_idx")]

    def __str__(self):
        return self.name

    @property
    def public(self) -> bool:
        current = self
        if not self.classe.public_documents:
            return False
        while current is not None:
            activity = current.activite
            if not current.is_public or (activity is not None and not activity.is_public):
                return False
            current = current.parent
        return True


class Document(models.Model):
    """Fichier de classe, rangé dans un dossier ou directement à la racine."""

    classe = models.ForeignKey(Classe, on_delete=models.CASCADE, related_name="documents")
    folder = models.ForeignKey(DossierDocument, null=True, blank=True, on_delete=models.CASCADE, related_name="documents")
    file = models.FileField(upload_to=document_upload_path, max_length=255)
    display_name = models.CharField("Nom du fichier", max_length=255)
    is_public = models.BooleanField("Visible sur la page publique", default=True)
    allow_public_downloads = models.BooleanField("Autoriser le téléchargement public", default=True)
    byte_size = models.PositiveBigIntegerField(default=0)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="documents_ajoutes",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["display_name", "id"]
        indexes = [models.Index(fields=["classe", "folder"], name="document_classe_folder_idx")]

    def __str__(self):
        return self.display_name


class RetourPlateforme(models.Model):
    """Suggestion ou signalement envoyé depuis le site à l’équipe Cohorte."""

    class Kind(models.TextChoices):
        BUG = "bug", "Signaler un bug"
        COMPLAINT = "complaint", "Réclamation"
        SUGGESTION = "suggestion", "Proposition"
        IMPROVEMENT = "improvement", "Demande d’amélioration"
        OTHER = "other", "Autre retour"

    class Status(models.TextChoices):
        NEW = "new", "Nouveau"
        REVIEWING = "reviewing", "En cours d’examen"
        PLANNED = "planned", "Planifié"
        DONE = "done", "Terminé"
        CLOSED = "closed", "Classé sans suite"

    kind = models.CharField("Type de retour", max_length=20, choices=Kind.choices)
    subject = models.CharField("Sujet", max_length=140)
    message = models.TextField("Message", max_length=5000)
    submitter_name = models.CharField("Nom", max_length=120, blank=True)
    submitter_email = models.EmailField("E-mail de contact", blank=True)
    source_path = models.CharField("Page concernée", max_length=255, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="retours_plateforme_envoyes",
    )
    status = models.CharField("État", max_length=20, choices=Status.choices, default=Status.NEW)
    admin_note = models.TextField("Note interne", blank=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="retours_plateforme_traites",
    )
    created_at = models.DateTimeField("Envoyé le", auto_now_add=True)
    updated_at = models.DateTimeField("Mis à jour le", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"], name="retour_status_created_idx")]
        verbose_name = "retour plateforme"
        verbose_name_plural = "retours plateforme"

    @property
    def kind_label(self) -> str:
        return dict(self.Kind.choices).get(self.kind, self.kind)

    @property
    def status_label(self) -> str:
        return dict(self.Status.choices).get(self.status, self.status)

    def __str__(self):
        return f"{self.kind_label} · {self.subject}"


class AuditLog(models.Model):
    """Journal des actions sensibles et des informations techniques du client."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    classe = models.ForeignKey(Classe, null=True, blank=True, on_delete=models.SET_NULL, related_name="journal")
    action = models.CharField(max_length=40)
    detail = models.CharField(max_length=300, blank=True)
    ip = models.CharField(max_length=64, blank=True)
    country = models.CharField(max_length=80, blank=True)
    country_code = models.CharField(max_length=2, blank=True)
    operating_system = models.CharField(max_length=80, blank=True)
    browser = models.CharField(max_length=80, blank=True)
    device = models.CharField(max_length=40, blank=True)
    user_agent = models.CharField(max_length=512, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["classe", "-created_at"])]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"
