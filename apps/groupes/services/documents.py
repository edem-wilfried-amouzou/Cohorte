"""Petits outils pour les dossiers et leurs exports ZIP."""

import mimetypes
import os
import shutil
import tempfile
import zipfile
from xml.etree import ElementTree
from pathlib import PurePosixPath

from django.http import FileResponse

from ..middleware import CSP
from ..models import MAX_ARCHIVE_SIZE, Document

MAX_PREVIEW_UNPACKED_SIZE = 100 * 1024 * 1024
MAX_PREVIEW_ARCHIVE_ENTRIES = 10000


def set_preview_headers(response):
    """Autorise l’aperçu même origine en gardant les autres règles CSP du site."""
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = CSP.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
    return response


def can_preview_in_browser(document) -> bool:
    """Formats affichés directement ou rendus en HTML sûr dans l’aperçu intégré."""
    content_type, _ = mimetypes.guess_type(document.display_name, strict=False)
    return os.path.splitext(document.display_name)[1].lower() in {".docx", ".xlsx", ".pptx"} or content_type in {
        "application/pdf", "text/plain", "text/csv",
        "text/markdown", "application/json", "application/xml", "text/xml",
        "image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp",
        "audio/mpeg", "audio/ogg", "audio/wav", "audio/mp4", "audio/aac", "video/mp4", "video/webm",
    }


def needs_html_preview(document) -> bool:
    return os.path.splitext(document.display_name)[1].lower() in {".docx", ".xlsx", ".pptx"}


def _office_archive_is_safe(source) -> bool:
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist()
            return len(entries) <= MAX_PREVIEW_ARCHIVE_ENTRIES and sum(entry.file_size for entry in entries) <= MAX_PREVIEW_UNPACKED_SIZE
    except (OSError, zipfile.BadZipFile):
        return False


def build_document_preview(document):
    """Extrait les formats Office installés sans exécuter ni intégrer leur contenu actif."""
    extension = os.path.splitext(document.display_name)[1].lower()
    if extension == ".docx":
        try:
            from docx import Document as WordDocument

            with document.file.open("rb") as source:
                if not _office_archive_is_safe(source):
                    return {"kind": "unsupported"}
                source.seek(0)
                word = WordDocument(source)
            paragraphs = [paragraph.text[:5000] for paragraph in word.paragraphs if paragraph.text.strip()][:1000]
            tables = []
            cell_budget = 5000
            for table in word.tables[:20]:
                rows = []
                for row in table.rows[:100]:
                    cells = [cell.text[:500] for cell in row.cells[:20]]
                    rows.append(cells)
                    cell_budget -= len(cells)
                    if cell_budget <= 0:
                        break
                tables.append(rows)
                if cell_budget <= 0:
                    break
            return {"kind": "docx", "paragraphs": paragraphs, "tables": tables}
        except Exception:
            return {"kind": "unsupported"}

    if extension == ".xlsx":
        try:
            from openpyxl import load_workbook

            with document.file.open("rb") as source:
                if not _office_archive_is_safe(source):
                    return {"kind": "unsupported"}
                source.seek(0)
                workbook = load_workbook(source, read_only=True, data_only=True)
                sheets = []
                cell_budget = 10000
                for sheet in workbook.worksheets[:10]:
                    rows = []
                    for row_index, row in enumerate(sheet.iter_rows(values_only=True)):
                        if row_index >= 200 or cell_budget <= 0:
                            break
                        cells = ["" if value is None else str(value)[:250] for value in row[:30]]
                        rows.append(cells)
                        cell_budget -= len(cells)
                    sheets.append({"name": sheet.title[:100], "rows": rows})
                workbook.close()
            return {"kind": "xlsx", "sheets": sheets}
        except Exception:
            return {"kind": "unsupported"}

    if extension == ".pptx":
        try:
            slides = []
            with document.file.open("rb") as source, zipfile.ZipFile(source) as archive:
                if sum(entry.file_size for entry in archive.infolist()) > MAX_PREVIEW_UNPACKED_SIZE:
                    return {"kind": "unsupported"}
                names = sorted(
                    (name for name in archive.namelist() if name.startswith("ppt/slides/slide") and name.endswith(".xml")),
                    key=lambda name: int(os.path.basename(name)[5:-4]) if os.path.basename(name)[5:-4].isdigit() else 0,
                )
                for name in names[:150]:
                    root = ElementTree.fromstring(archive.read(name))
                    text = [node.text for node in root.iter() if node.tag.endswith("}t") and node.text]
                    slides.append(" · ".join(text)[:5000])
            return {"kind": "pptx", "slides": slides}
        except Exception:
            return {"kind": "unsupported"}

    return {"kind": "unsupported"}


def folder_is_public(folder) -> bool:
    """Un dossier n'est public que si lui et tous ses parents le sont."""
    return folder.public


def descendants(folder):
    """Retourne le dossier et ses sous-dossiers, sans dépendre d'une extension d'arbre."""
    found = [folder]
    known = {folder.pk}
    position = 0
    while position < len(found):
        children = list(folder.classe.dossiers_documents.filter(parent=found[position]).order_by("name"))
        for child in children:
            if child.pk not in known:
                found.append(child)
                known.add(child.pk)
        position += 1
    return found


def archive_response(folder, *, public_only=False):
    folders = descendants(folder)
    if public_only:
        folders = [item for item in folders if folder_is_public(item)]
    documents = list(
        Document.objects.filter(folder__in=folders).select_related("folder", "folder__parent").order_by("folder__name", "display_name", "id")
    )
    if public_only:
        documents = [
            item for item in documents
            if item.is_public and item.allow_public_downloads and item.folder.allow_public_downloads
        ]
    total_size = sum(item.byte_size for item in documents)
    if total_size > MAX_ARCHIVE_SIZE:
        raise ValueError("Ce dossier dépasse la limite de 100 Mo pour un téléchargement ZIP.")
    if not documents:
        raise ValueError("Ce dossier ne contient aucun fichier visible.")

    archive = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
    used_names = set()
    try:
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for document in documents:
                parts = [document.display_name]
                current = document.folder
                while current is not None:
                    parts.insert(0, current.name)
                    if current.pk == folder.pk:
                        break
                    current = current.parent
                path = str(PurePosixPath(*parts))
                if path in used_names:
                    path = f"{path.rsplit('.', 1)[0]} ({document.pk}).{path.rsplit('.', 1)[1]}" if "." in path else f"{path} ({document.pk})"
                used_names.add(path)
                with document.file.open("rb") as source, output.open(path, "w") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
        if archive.tell() > MAX_ARCHIVE_SIZE:
            raise ValueError("L’archive compressée dépasse la limite de 100 Mo.")
        archive.seek(0)
        return FileResponse(archive, as_attachment=True, filename=f"{folder.pk}-documents.zip", content_type="application/zip")
    except Exception:
        archive.close()
        raise
