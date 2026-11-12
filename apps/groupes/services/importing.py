"""Lecture d'une liste de classe (xlsx, csv, txt ou texte collé) et import en base."""
import csv
import io
import re
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from ..models import Groupe, Membre, compose_full_name, name_key, normalize, split_full_name

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 3000
MAX_GROUP_NUMBER = 500
ALLOWED_EXT = {".xlsx", ".csv", ".txt"}

NAME_KEYS = {
    "nom complet", "nom et prenom", "nom et prenoms", "nom prenom", "nom prenoms",
    "nom complet et prenom", "full name", "student name", "etudiant", "eleve", "student",
}
FIRST_NAME_KEYS = {"prenom", "prenoms", "first name", "firstname", "first_name", "given name", "given_name"}
LAST_NAME_KEYS = {"nom", "noms", "nom de famille", "surname", "last name", "lastname", "last_name", "family name"}
GROUP_KEYS = {"groupe", "group", "numero de groupe", "numero groupe", "n groupe", "num groupe", "group number"}
REF_KEYS = {"matricule", "reference", "ref", "student id", "student number", "numero etudiant"}
SEX_KEYS = {"sexe", "genre", "sex", "gender"}
NUMBER_KEYS = {"n", "no", "num", "numero", "numero d ordre", "ordre", "rang", "#"}
# En-têtes qui annoncent « Nom puis Prénoms » dans une seule colonne (sans majuscules pour les distinguer).
LAST_FIRST_HEADERS = {"nom et prenom", "nom et prenoms", "nom prenom", "nom prenoms", "nom complet et prenom"}
MAX_NUMBER = 1_000_000


class ImportError_(Exception):
    """Erreur lisible par l'utilisateur."""


@dataclass
class Row:
    name: str
    group: int | None = None
    reference: str = ""
    sex: str = ""
    number: int | None = None
    first_name: str = ""
    last_name: str = ""
    custom_data: dict[str, str] | None = None
    custom_labels: dict[str, str] | None = None


def _clean(value) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def _group_number(value: str) -> int | None:
    match = re.search(r"\d+", value or "")
    if not match:
        return None
    number = int(match.group())
    return number if 0 < number <= MAX_GROUP_NUMBER else None


def _number(value: str) -> int | None:
    match = re.search(r"\d+", value or "")
    if not match:
        return None
    number = int(match.group())
    return number if 0 < number <= MAX_NUMBER else None


def _rows_to_entries(rows: list[list[str]]) -> list[Row]:
    rows = [r for r in rows if any(_clean(c) for c in r)]
    if not rows:
        return []
    raw_headers = [_clean(cell) for cell in rows[0]]
    header = [normalize(value).replace("°", "").replace(".", "") for value in raw_headers]
    name_i = next((i for i, h in enumerate(header) if h in NAME_KEYS), None)
    first_i = next((i for i, h in enumerate(header) if h in FIRST_NAME_KEYS), None)
    last_i = next((i for i, h in enumerate(header) if h in LAST_NAME_KEYS), None)
    group_i = next((i for i, h in enumerate(header) if h in GROUP_KEYS), None)
    ref_i = next((i for i, h in enumerate(header) if h in REF_KEYS), None)
    sex_i = next((i for i, h in enumerate(header) if h in SEX_KEYS), None)
    number_i = next((i for i, h in enumerate(header) if h in NUMBER_KEYS), None)
    separate_names = first_i is not None and last_i is not None and first_i != last_i
    if separate_names:
        name_i = None
    has_header = separate_names or any(i is not None for i in (name_i, first_i, last_i))
    if has_header:
        body = rows[1:]
        if name_i is None and not separate_names:
            combined_index = first_i if first_i is not None else last_i
        else:
            combined_index = name_i
    else:
        # Pas de ligne de titres : une seule colonne de noms complets.
        name_i, first_i, last_i, group_i, ref_i, sex_i, number_i, body = 0, None, None, None, None, None, None, rows
        combined_index = None
    entries = []
    standard_indices = {
        index for index in (name_i, first_i, last_i, group_i, ref_i, sex_i, number_i) if index is not None
    }
    # Nom complet dans une seule colonne : sans indice (majuscules ou en-tête explicite), on garde l'ordre d'origine.
    combined_header = header[combined_index] if combined_index is not None else ""
    last_first = combined_header in LAST_FIRST_HEADERS
    extra_headers = []
    seen_keys = set()
    for index, label in enumerate(raw_headers):
        normalized_label = normalize(label)
        if index in standard_indices or not normalized_label:
            continue
        key = re.sub(r"[^a-z0-9]+", "_", normalized_label).strip("_")[:60]
        if key and key not in seen_keys:
            extra_headers.append((index, key, label[:100]))
            seen_keys.add(key)
    for r in body[:MAX_ROWS]:
        get = lambda i: _clean(r[i]) if i is not None and i < len(r) else ""
        if separate_names:
            last, first = get(last_i), get(first_i)
        else:
            raw = get(name_i) if name_i is not None else get(first_i) or get(last_i)
            last, first = split_full_name(raw, last_first)
        name = compose_full_name(first, last)
        sex_value = normalize(get(sex_i))
        sex = "F" if sex_value in {"f", "femme", "feminin", "female"} else "M" if sex_value in {"m", "homme", "masculin", "male"} else "X" if sex_value else ""
        name = name[:120]
        if name:
            entries.append(Row(
                name=name,
                group=_group_number(get(group_i)),
                reference=get(ref_i)[:40],
                sex=sex,
                number=_number(get(number_i)),
                first_name=first[:100],
                last_name=last[:80],
                custom_data={key: get(index) for index, key, _ in extra_headers if get(index)},
                custom_labels={key: label for _, key, label in extra_headers},
            ))
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
    from ..models import ChampMembre

    existing = {name_key(m.search_name): m for m in classe.membres.all()}
    groups = {g.number: g for g in classe.groupes.filter(activite__isnull=True)}
    now = timezone.now()
    result = {"created": 0, "duplicates": 0, "assigned": 0, "groups_created": 0, "fields_created": 0}
    to_create, to_update = [], []
    field_labels = {}
    for row in rows:
        field_labels.update(row.custom_labels or {})
    known_fields = {field.key for field in classe.champs_membres.all()}
    fields_to_create = [
        ChampMembre(classe=classe, key=key, label=label, position=100 + index)
        for index, (key, label) in enumerate(field_labels.items()) if key not in known_fields
    ]
    if fields_to_create:
        ChampMembre.objects.bulk_create(fields_to_create)
        result["fields_created"] = len(fields_to_create)

    for row in rows:
        key = name_key(row.name)
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
            changed = False
            if row.sex and not member.sex:
                member.sex = row.sex
                changed = True
            if row.reference and not member.reference:
                member.reference = row.reference
                changed = True
            if row.number and not member.number:
                member.number = row.number
                changed = True
            if (row.first_name or row.last_name) and not (member.first_name or member.last_name):
                member.first_name, member.last_name = row.first_name, row.last_name
                changed = True
            for key, value in (row.custom_data or {}).items():
                if value and not member.custom_data.get(key):
                    member.custom_data[key] = value
                    changed = True
            if group and member.groupe_id is None:
                member.groupe, member.responded_at = group, member.responded_at or now
                result["assigned"] += 1
                changed = True
            if changed:
                to_update.append(member)
            continue
        member = Membre(
            classe=classe, full_name=row.name, search_name=normalize(row.name), reference=row.reference,
            number=row.number, first_name=row.first_name, last_name=row.last_name,
            sex=row.sex, custom_data=row.custom_data or {}, groupe=group,
            responded_at=now if group else None, source=Membre.Source.IMPORT,
        )
        existing[key] = member
        to_create.append(member)
        result["created"] += 1
        if group:
            result["assigned"] += 1

    Membre.objects.bulk_create(to_create)
    Membre.objects.bulk_update(
        to_update,
        ["groupe", "responded_at", "sex", "reference", "custom_data", "number", "first_name", "last_name"],
    )
    return result