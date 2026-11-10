"""Lecture d'une liste de classe (xlsx, csv, txt ou texte collé) et import en base."""
import csv
import io
import re
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from ..models import Groupe, Membre, normalize

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 3000
MAX_GROUP_NUMBER = 500
ALLOWED_EXT = {".xlsx", ".csv", ".txt"}

NAME_KEYS = {
    "nom", "noms", "name", "nom complet", "nom et prenom", "nom et prenoms", "nom prenom",
    "nom prenoms", "etudiant", "eleve", "student", "full name",
}
FIRST_NAME_KEYS = {"prenom", "prenoms", "first name", "firstname"}
LAST_NAME_KEYS = {"nom de famille", "nom", "surname", "last name", "lastname"}
GROUP_KEYS = {"groupe", "group", "numero de groupe", "numero groupe", "n groupe", "num groupe"}
REF_KEYS = {"matricule", "reference", "ref"}
SEX_KEYS = {"sexe", "genre", "sex", "gender"}


class ImportError_(Exception):
    """Erreur lisible par l'utilisateur."""


@dataclass
class Row:
    name: str
    group: int | None = None
    reference: str = ""
    sex: str = ""


def _clean(value) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _group_number(value: str) -> int | None:
    match = re.search(r"\d+", value or "")
    if not match:
        return None
    number = int(match.group())
    return number if 0 < number <= MAX_GROUP_NUMBER else None


def _rows_to_entries(rows: list[list[str]]) -> list[Row]:
    rows = [r for r in rows if any(_clean(c) for c in r)]
    if not rows:
        return []
    header = [normalize(_clean(c)).replace("°", "").replace(".", "") for c in rows[0]]
    name_i = next((i for i, h in enumerate(header) if h in NAME_KEYS), None)
    first_i = next((i for i, h in enumerate(header) if h in FIRST_NAME_KEYS), None)
    last_i = next((i for i, h in enumerate(header) if h in LAST_NAME_KEYS), None)
    group_i = next((i for i, h in enumerate(header) if h in GROUP_KEYS), None)
    ref_i = next((i for i, h in enumerate(header) if h in REF_KEYS), None)
    sex_i = next((i for i, h in enumerate(header) if h in SEX_KEYS), None)
    if first_i is not None and last_i is not None:
        name_i = None
    if name_i is not None:
        body = rows[1:]
    elif first_i is not None or last_i is not None:
        name_i, body = None, rows[1:]
    else:
        name_i, first_i, last_i, group_i, ref_i, sex_i, body = 0, None, None, None, None, None, rows
    entries = []
    for r in body[:MAX_ROWS]:
        get = lambda i: _clean(r[i]) if i is not None and i < len(r) else ""
        name = get(name_i) if name_i is not None else " ".join(part for part in (get(first_i), get(last_i)) if part)
        sex_value = normalize(get(sex_i))
        sex = "F" if sex_value in {"f", "femme", "feminin", "female"} else "M" if sex_value in {"m", "homme", "masculin", "male"} else "X" if sex_value else ""
        name = name[:120]
        if name:
            entries.append(Row(name=name, group=_group_number(get(group_i)), reference=get(ref_i)[:40], sex=sex))
    return entries


def parse_text(text: str) -> list[Row]:
    lines = [[line.split("\t")[0]] for line in (text or "").splitlines()]
    if len(lines) > MAX_ROWS:
        raise ImportError_(f"Maximum {MAX_ROWS} lignes par import.")
    return _rows_to_entries(lines)


def parse_upload(upload) -> list[Row]:
    name = (upload.name or "").lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if ext not in ALLOWED_EXT:
        raise ImportError_("Format non pris en charge. Utilise un fichier .xlsx, .csv ou .txt.")
    if upload.size > MAX_BYTES:
        raise ImportError_("Fichier trop volumineux (2 Mo maximum).")
    raw = upload.read()

    if ext == ".xlsx":
        if not raw.startswith(b"PK"):
            raise ImportError_("Ce fichier n'est pas un vrai fichier Excel (.xlsx).")
        from openpyxl import load_workbook

        try:
            wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            rows = [[_clean(c) for c in r] for r in wb.active.iter_rows(values_only=True)]
        except Exception as exc:  # fichier corrompu
            raise ImportError_("Impossible de lire ce fichier Excel.") from exc
        return _rows_to_entries(rows)

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    if ext == ".txt":
        return parse_text(text)

    sample = text[:2048]
    delimiter = max(",;\t", key=sample.count) if any(d in sample for d in ",;\t") else ","
    return _rows_to_entries([row for row in csv.reader(io.StringIO(text), delimiter=delimiter)])


@transaction.atomic
def import_rows(classe, rows: list[Row]) -> dict:
    existing = {m.search_name: m for m in classe.membres.all()}
    groups = {g.number: g for g in classe.groupes.filter(activite__isnull=True)}
    now = timezone.now()
    result = {"created": 0, "duplicates": 0, "assigned": 0, "groups_created": 0}
    to_create, to_update = [], []

    for row in rows:
        key = normalize(row.name)
        if not key:
            continue
        group = None
        if row.group:
            group = groups.get(row.group)
            if group is None:
                group = Groupe.objects.create(classe=classe, number=row.group)
                groups[row.group] = group
                result["groups_created"] += 1
        member = existing.get(key)
        if member is not None:
            result["duplicates"] += 1
            if group and member.groupe_id is None:
                member.groupe, member.responded_at = group, member.responded_at or now
                to_update.append(member)
                result["assigned"] += 1
            continue
        member = Membre(
                classe=classe, full_name=row.name, search_name=key, reference=row.reference,
            sex=row.sex, groupe=group, responded_at=now if group else None, source=Membre.Source.IMPORT,
        )
        existing[key] = member
        to_create.append(member)
        result["created"] += 1
        if group:
            result["assigned"] += 1

    Membre.objects.bulk_create(to_create)
    Membre.objects.bulk_update(to_update, ["groupe", "responded_at"])
    return result
