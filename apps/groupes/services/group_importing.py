"""Importe une répartition de groupes depuis un tableur ou un fichier CSV."""
import csv
import difflib
import io
import re
import zipfile
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from ..models import Groupe, Membre, Participation, normalize

MAX_BYTES = 2 * 1024 * 1024
MAX_XLSX_UNPACKED_BYTES = 30 * 1024 * 1024
MAX_ROWS = 3000
MAX_MEMBERS = 5000
MAX_GROUP_NUMBER = 500
ALLOWED_EXT = {".xlsx", ".csv"}

GROUP_HEADERS = {
    "groupe", "groupes", "group", "numero groupe", "numero de groupe", "numero du groupe",
    "n groupe", "num groupe", "group number",
}
MEMBER_HEADERS = {
    "membre", "membres", "membre s", "membres du groupe", "personne", "personnes",
    "participant", "participants", "nom", "noms", "noms membres", "nom s et prenoms",
    "etudiant", "etudiants", "eleve", "eleves", "student", "students", "members",
}


class ImportError_(Exception):
    """Erreur de format ou de contenu affichable dans l'interface."""


@dataclass(frozen=True)
class GroupRow:
    number: int
    names: tuple[str, ...]


def _header_key(value) -> str:
    value = normalize(str(value or ""))
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _group_number(value) -> int | None:
    raw = str(value or "").strip()
    if isinstance(value, float) and not value.is_integer():
        return None
    if re.fullmatch(r"\d+\.\d+", raw):
        return None
    match = re.search(r"\d+", raw)
    if not match:
        return None
    number = int(match.group())
    return number if 0 < number <= MAX_GROUP_NUMBER else None


def _parse_rows(rows) -> list[GroupRow]:
    rows = [list(row) for row in rows if any(str(value or "").strip() for value in row)]
    if not rows:
        raise ImportError_("Le fichier ne contient aucune ligne.")
    headers = [_header_key(value) for value in rows[0]]
    group_i = next((i for i, value in enumerate(headers) if value in GROUP_HEADERS), None)
    members_i = next((i for i, value in enumerate(headers) if value in MEMBER_HEADERS), None)
    if group_i is None or members_i is None or group_i == members_i:
        raise ImportError_("Ajoute deux en-têtes : « Groupe » et « Membres ».")

    body = rows[1:]
    if len(body) > MAX_ROWS:
        raise ImportError_(f"Maximum {MAX_ROWS} lignes par import.")

    parsed = []
    member_count = 0
    for line_number, row in enumerate(body, start=2):
        group_value = row[group_i] if group_i < len(row) else ""
        if members_i < len(row):
            member_cells = row[members_i:group_i] if members_i < group_i else row[members_i:]
            members_value = ",".join(str(value or "") for value in member_cells)
        else:
            members_value = ""
        number = _group_number(group_value)
        if number is None:
            raise ImportError_(f"Ligne {line_number} : indique un numéro de groupe entre 1 et {MAX_GROUP_NUMBER}.")
        # La cellule Membres accepte plusieurs personnes séparées par une virgule ou un point-virgule.
        names = tuple(name.strip() for name in re.split(r"[,;\n]+", str(members_value or "")) if name.strip())
        if any(len(name) > 120 for name in names):
            raise ImportError_(f"Ligne {line_number} : chaque nom doit faire 120 caractères maximum.")
        member_count += len(names)
        if member_count > MAX_MEMBERS:
            raise ImportError_(f"Maximum {MAX_MEMBERS} noms par import.")
        parsed.append(GroupRow(number=number, names=names))

    if not parsed:
        raise ImportError_("Aucun groupe trouvé sous les en-têtes du fichier.")
    return parsed


def _read_csv(raw: bytes):
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")

    sample_lines = [line for line in text.splitlines() if line.strip()]
    if not sample_lines:
        return []
    # La première ligne contient les en-têtes. On choisit le séparateur qui permet de
    # reconnaître les deux colonnes attendues, même si une cellule contient des virgules.
    candidates = []
    for delimiter in (",", ";", "\t"):
        try:
            header = next(csv.reader([sample_lines[0]], delimiter=delimiter))
        except csv.Error:
            continue
        normalized = [_header_key(value) for value in header]
        has_group = any(value in GROUP_HEADERS for value in normalized)
        has_members = any(value in MEMBER_HEADERS for value in normalized)
        candidates.append((int(has_group) + int(has_members), delimiter))
    score, delimiter = max(candidates, default=(0, ","), key=lambda item: item[0])
    if score != 2:
        raise ImportError_("Ajoute deux en-têtes : « Groupe » et « Membres ».")
    return list(csv.reader(io.StringIO(text), delimiter=delimiter))


def parse_upload(upload) -> list[GroupRow]:
    name = (upload.name or "").lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if ext not in ALLOWED_EXT:
        raise ImportError_("Format non pris en charge. Utilise un fichier .xlsx ou .csv.")
    if upload.size > MAX_BYTES:
        raise ImportError_("Fichier trop volumineux (2 Mo maximum).")
    raw = upload.read()

    if ext == ".xlsx":
        if not raw.startswith(b"PK"):
            raise ImportError_("Ce fichier n'est pas un vrai fichier Excel (.xlsx).")
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if len(entries) > 10000 or sum(entry.file_size for entry in entries) > MAX_XLSX_UNPACKED_BYTES:
                    raise ImportError_("Ce fichier Excel est trop complexe ou trop volumineux à décompresser.")
        except zipfile.BadZipFile as exc:
            raise ImportError_("Ce fichier n'est pas un vrai fichier Excel (.xlsx).") from exc
        from openpyxl import load_workbook

        workbook = None
        try:
            workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
            sheet = workbook.active
            rows = []
            for index, row in enumerate(sheet.iter_rows(values_only=True)):
                if index > MAX_ROWS:
                    raise ImportError_(f"Maximum {MAX_ROWS} lignes par import.")
                rows.append(row)
        except ImportError_:
            raise
        except Exception as exc:  # fichier Excel corrompu
            raise ImportError_("Impossible de lire ce fichier Excel.") from exc
        finally:
            if workbook is not None:
                workbook.close()
        return _parse_rows(rows)

    try:
        rows = _read_csv(raw)
    except (csv.Error, UnicodeError) as exc:
        raise ImportError_("Impossible de lire ce fichier CSV.") from exc
    return _parse_rows(rows)


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", normalize(value)))


def _match_score(query: str, member: Membre) -> tuple[float, bool]:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0, False
    query_set = set(query_tokens)
    full_tokens = set(_tokens(member.full_name))
    if query_set == full_tokens:
        return 1.0, True
    if query_set and query_set.issubset(full_tokens):
        # Un nom de famille ou des prénoms seuls sont acceptés quand ils désignent
        # clairement une seule personne de la classe.
        return (0.93 if len(query_set) == 1 else 0.95), True

    query_key = " ".join(sorted(query_tokens))
    candidates = [member.full_name, member.last_name, member.first_name]
    best = 0.0
    for candidate in candidates:
        candidate_tokens = _tokens(candidate)
        if not candidate_tokens:
            continue
        candidate_key = " ".join(sorted(candidate_tokens))
        # Les noms très courts sont trop ambigus pour une comparaison approximative.
        if len(query_key.replace(" ", "")) >= 4:
            best = max(best, difflib.SequenceMatcher(None, query_key, candidate_key).ratio())
    return best, False


def _find_member(query: str, members: list[Membre]):
    scored = [(member, *_match_score(query, member)) for member in members]
    scored.sort(key=lambda item: (-item[1], item[0].search_name, item[0].pk))
    if not scored or scored[0][1] < 0.78:
        return None, "introuvable", []
    best_member, best_score, best_direct = scored[0]
    if best_direct and best_score == 1.0:
        return best_member, "exacte", []
    if len(scored) > 1 and best_score - scored[1][1] < 0.08:
        return None, "plusieurs correspondances possibles", [member.full_name for member, _, _ in scored[:3]]
    method = "exacte" if best_direct else "approximative"
    return best_member, method, []


@transaction.atomic
def import_rows(activity, rows: list[GroupRow]) -> dict:
    members = list(activity.classe.membres.order_by("search_name", "pk"))
    entries = []
    seen = set()
    for row in rows:
        for raw_name in row.names:
            key = " ".join(sorted(_tokens(raw_name)))
            if not key:
                continue
            entry_key = (row.number, key)
            if entry_key in seen:
                continue
            seen.add(entry_key)
            member, method, candidates = _find_member(raw_name, members)
            entries.append({
                "number": row.number,
                "raw_name": raw_name,
                "key": key,
                "member": member,
                "method": method,
                "candidates": candidates,
            })

    # Une personne citée dans plusieurs groupes doit être signalée, pas déplacée
    # silencieusement vers le dernier groupe lu.
    claims = {}
    for entry in entries:
        if entry["member"] is not None:
            claims.setdefault(entry["member"].pk, {}).setdefault(entry["number"], []).append(entry)
    for member_id, by_group in claims.items():
        if len(by_group) > 1:
            for claimed_entries in by_group.values():
                for entry in claimed_entries:
                    entry["member"] = None
                    entry["method"] = "indiqué dans plusieurs groupes"

    # Plusieurs graphies de la même personne dans un même groupe ne créent qu'une
    # participation. On conserve la correspondance la plus précise pour le bilan.
    unique_assignments = {}
    unresolved = []
    for entry in entries:
        if entry["member"] is None:
            unresolved.append(entry)
            continue
        key = (entry["member"].pk, entry["number"])
        previous = unique_assignments.get(key)
        if previous is None or (previous["method"] != "exacte" and entry["method"] == "exacte"):
            unique_assignments[key] = entry
    entries = unresolved + list(unique_assignments.values())

    accepted = [entry for entry in entries if entry["member"] is not None]
    group_numbers = {row.number for row in rows}
    existing_groups = {
        group.number: group
        for group in activity.groupes.filter(number__in=group_numbers)
    }
    current_participations = {
        participation.membre_id: participation
        for participation in activity.participations.select_related("groupe").all()
    }

    # Vérifie les capacités avant toute écriture, en comptant les déplacements demandés.
    existing_sizes = dict(
        activity.groupes.filter(number__in=group_numbers)
        .annotate(size=Count("participations"))
        .values_list("number", "size")
    )
    projected = {number: existing_sizes.get(number, 0) for number in group_numbers}
    for entry in accepted:
        member = entry["member"]
        current = current_participations.get(member.pk)
        if current and current.groupe_id and current.groupe.number in projected:
            projected[current.groupe.number] -= 1
        projected[entry["number"]] = projected.get(entry["number"], 0) + 1
    for number, size in projected.items():
        group = existing_groups.get(number)
        current_size = existing_sizes.get(number, 0)
        if group and group.capacity is not None and size > group.capacity and size > current_size:
            raise ImportError_(f"L’import dépasse la capacité du groupe {number} ({group.capacity}).")

    groups_created = 0
    groups = dict(existing_groups)
    for number in sorted(group_numbers):
        if number not in groups:
            groups[number] = Groupe.objects.create(classe=activity.classe, activite=activity, number=number)
            groups_created += 1

    now = timezone.now()
    result = {
        "groups_created": groups_created,
        "assigned": len(accepted),
        "exact": sum(entry["method"] == "exacte" for entry in accepted),
        "fuzzy": sum(entry["method"] == "approximative" for entry in accepted),
        "created": 0,
        "moved": 0,
        "issues": [],
        "fuzzy_matches": [],
    }
    for entry in entries:
        if entry["member"] is None:
            result["issues"].append({
                "name": entry["raw_name"],
                "group_number": entry["number"],
                "reason": entry["method"],
                "candidates": entry["candidates"],
            })
            continue
        member = entry["member"]
        group = groups[entry["number"]]
        current = current_participations.get(member.pk)
        if current is None:
            Participation.objects.create(activite=activity, membre=member, groupe=group, responded_at=now)
            result["created"] += 1
        else:
            if current.groupe_id and current.groupe_id != group.pk:
                result["moved"] += 1
            current.groupe = group
            current.responded_at = current.responded_at or now
            current.save(update_fields=["groupe", "responded_at"])
        if entry["method"] == "approximative":
            result["fuzzy_matches"].append({
                "name": entry["raw_name"],
                "member": member.full_name,
                "group_number": entry["number"],
            })
    return result
