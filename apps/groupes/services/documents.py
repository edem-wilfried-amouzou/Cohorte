"""Petits outils pour les dossiers et leurs exports ZIP."""

import mimetypes
import shutil
import tempfile
import zipfile
from pathlib import PurePosixPath

from django.http import FileResponse

from ..middleware import CSP
from ..models import MAX_ARCHIVE_SIZE, Document


def set_preview_headers(response):
    """Autorise l’aperçu même origine en gardant les autres règles CSP du site."""
    response["X-Content-Type-Options"] = "nosniff"
    response["Content-Security-Policy"] = CSP.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
    return response


def can_preview_in_browser(document) -> bool:
    """N'autorise en aperçu public que les formats passifs couramment affichés par un navigateur."""
    content_type, _ = mimetypes.guess_type(document.display_name, strict=False)
    return content_type in {
        "application/pdf", "text/plain", "text/csv",
        "image/png", "image/jpeg", "image/gif", "image/webp", "image/avif",
        "audio/mpeg", "audio/ogg", "audio/wav", "video/mp4", "video/webm",
    }


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
