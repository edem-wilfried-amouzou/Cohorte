import mimetypes
import os

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError
from django.db import transaction
from django.db.models import Sum
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_GET, require_POST

from ..audit import log_action
from ..forms import DocumentAccessForm, DossierDocumentAccessForm, DossierDocumentForm, DocumentUploadForm
from ..models import (
    MAX_CLASS_STORAGE, Classe, Document, DossierDocument,
)
from ..services.documents import archive_response, can_preview_in_browser, folder_is_public, set_preview_headers
from ..throttle import too_many
from ._common import _classe


def _used_space(classe):
    return classe.documents.aggregate(total=Sum("byte_size"))["total"] or 0


def _private_file_response(response):
    """Empêche les navigateurs et proxys de conserver les documents téléchargés."""
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


def _clean_filename(value):
    name = os.path.basename((value or "document").replace("\\", "/"))
    name = "".join(character for character in name if character.isprintable()).strip()
    return name[:255] if name not in {"", ".", ".."} else "document"


def _save_document(form, classe, uploaded_by):
    """Réserve de la place pour les fichiers de la classe avant de les enregistrer."""
    uploaded = form.cleaned_data["file"]
    with transaction.atomic():
        locked_class = Classe.objects.select_for_update().get(pk=classe.pk)
        if _used_space(locked_class) + uploaded.size > MAX_CLASS_STORAGE:
            return None
        document = form.save(commit=False)
        document.classe = locked_class
        document.display_name = _clean_filename(uploaded.name)
        document.byte_size = uploaded.size
        document.uploaded_by = uploaded_by
        if document.folder_id:
            document.is_public = document.folder.is_public
            document.allow_public_downloads = document.folder.allow_public_downloads
        else:
            document.is_public = False
        document.save()
    return document


def _documents_redirect(request, classe):
    target_id = request.POST.get("return_folder") or request.GET.get("return_folder")
    if target_id and DossierDocument.objects.filter(pk=target_id, classe=classe).exists():
        return redirect(f"{reverse('groupes:documents', args=[classe.pk])}?folder={target_id}")
    return redirect("groupes:documents", pk=classe.pk)


def _document_page(request, classe, folder_form=None, upload_form=None, *, status=200):
    all_folders = list(
        classe.dossiers_documents.select_related("classe", "activite", "parent").prefetch_related("documents").order_by(
            "activite__title", "parent__name", "name",
        )
    )
    children_by_parent = {}
    for folder in all_folders:
        files = list(folder.documents.all())
        folder.file_count = len(files)
        folder.total_size = sum(item.byte_size for item in files)
        folder.public_access = folder_is_public(folder)
        children_by_parent.setdefault(folder.parent_id, []).append(folder)

    def count_nested_files(folder):
        children = children_by_parent.get(folder.pk, [])
        folder.subfolder_count = len(children)
        folder.total_file_count = folder.file_count + sum(count_nested_files(child) for child in children)
        return folder.total_file_count

    for root_folder in children_by_parent.get(None, []):
        count_nested_files(root_folder)
    folder_id = request.GET.get("folder") or request.POST.get("return_folder")
    if folder_id:
        get_object_or_404(DossierDocument, pk=folder_id, classe=classe)
        current_folder = next(folder for folder in all_folders if str(folder.pk) == str(folder_id))
    else:
        current_folder = None
    folders = [folder for folder in all_folders if folder.parent_id == (current_folder.pk if current_folder else None)]
    documents = list(current_folder.documents.all()) if current_folder else list(classe.documents.filter(folder__isnull=True))
    for document in documents:
        document.public_access = bool(
            document.is_public and classe.public_documents
            and (document.folder_id is None or folder_is_public(document.folder))
        )
        document.previewable = can_preview_in_browser(document)
    breadcrumbs = []
    parent = current_folder
    while parent:
        breadcrumbs.insert(0, parent)
        parent = parent.parent
    used_space = _used_space(classe)
    default_parent = current_folder.pk if current_folder else None
    default_activity = current_folder.activite_id if current_folder else None
    folder_form = folder_form if folder_form is not None else DossierDocumentForm(
        classe=classe, initial={"parent": default_parent, "activite": default_activity},
    )
    upload_form = upload_form if upload_form is not None else DocumentUploadForm(
        classe=classe, initial={"folder": current_folder.pk if current_folder else None},
    )
    return render(request, "groupes/documents.html", {
        "classe": classe,
        "folders": folders,
        "all_folders": all_folders,
        "current_folder": current_folder,
        "breadcrumbs": breadcrumbs,
        "documents": documents,
        "folder_form": folder_form,
        "upload_form": upload_form,
        "used_space": used_space,
        "usage_percent": min(100, round(used_space * 100 / MAX_CLASS_STORAGE)),
        "max_space": MAX_CLASS_STORAGE,
        "can_edit": request.class_role != "viewer",
        "can_manage": request.class_role in ("owner", "co_delegate"),
        "can_publish": request.class_role in ("owner", "co_delegate"),
        "create_folder_open": bool(folder_form.errors),
        "upload_form_open": bool(upload_form.errors),
        "tab": "documents",
    }, status=status)


@login_required
@require_POST
def documents(request, pk):
    classe = _classe(request, pk, write=True)
    action = request.POST.get("action")
    if action == "create_folder":
        form = DossierDocumentForm(request.POST, classe=classe)
        if form.is_valid():
            folder = form.save(commit=False)
            folder.classe = classe
            if request.class_role not in ("owner", "co_delegate"):
                folder.is_public = False
                folder.allow_public_uploads = False
            try:
                folder.save()
            except IntegrityError:
                form.add_error("name", "Un dossier porte déjà ce nom à cet endroit.")
            else:
                log_action(request, "dossier_document_cree", classe=classe, detail=folder.name)
                messages.success(request, f"Dossier « {folder.name} » créé.")
                return _documents_redirect(request, classe)
        return _document_page(request, classe, folder_form=form, status=400)

    if action == "upload":
        form = DocumentUploadForm(request.POST, request.FILES, classe=classe)
        if form.is_valid():
            document = _save_document(form, classe, request.user)
            if document is None:
                form.add_error("file", "La classe a atteint sa limite de stockage de 250 Mo.")
            else:
                log_action(request, "document_ajoute", classe=classe, detail=document.display_name)
                messages.success(request, f"Fichier « {document.display_name} » ajouté.")
                return _documents_redirect(request, classe)
        return _document_page(request, classe, upload_form=form, status=400)

    messages.error(request, "Action inconnue.")
    return _documents_redirect(request, classe)


@login_required
@require_GET
def documents_page(request, pk):
    classe = _classe(request, pk)
    return _document_page(request, classe)


@login_required
@require_POST
def document_folder_access(request, pk, fpk):
    classe = _classe(request, pk, write=True, full=True)
    folder = get_object_or_404(DossierDocument, pk=fpk, classe=classe)
    form = DossierDocumentAccessForm(request.POST, instance=folder)
    if form.is_valid():
        form.save()
        log_action(request, "visibilite_dossier_document", classe=classe, detail=folder.name)
        messages.success(request, f"Visibilité de « {folder.name} » mise à jour.")
    else:
        messages.error(request, " ".join(str(error) for errors in form.errors.values() for error in errors))
    return _documents_redirect(request, classe)


@login_required
@require_POST
def document_folder_delete(request, pk, fpk):
    classe = _classe(request, pk, write=True, full=True)
    folder = get_object_or_404(DossierDocument, pk=fpk, classe=classe)
    name = folder.name
    folder.delete()
    log_action(request, "dossier_document_supprime", classe=classe, detail=name)
    messages.success(request, f"Dossier « {name} » et son contenu supprimés.")
    return _documents_redirect(request, classe)


@login_required
@require_POST
def document_delete(request, pk, dpk):
    classe = _classe(request, pk, write=True, full=True)
    document = get_object_or_404(Document, pk=dpk, classe=classe)
    name = document.display_name
    document.delete()
    log_action(request, "document_supprime", classe=classe, detail=name)
    messages.success(request, f"Fichier « {name} » supprimé.")
    return _documents_redirect(request, classe)


@login_required
@require_POST
def document_access(request, pk, dpk):
    classe = _classe(request, pk, write=True, full=True)
    document = get_object_or_404(Document, pk=dpk, classe=classe)
    form = DocumentAccessForm(request.POST, instance=document)
    if form.is_valid():
        form.save()
        log_action(request, "visibilite_document", classe=classe, detail=document.display_name)
        messages.success(request, f"Visibilité de « {document.display_name} » mise à jour.")
    else:
        messages.error(request, " ".join(str(error) for errors in form.errors.values() for error in errors))
    return _documents_redirect(request, classe)


@login_required
@require_GET
@xframe_options_sameorigin
def document_download(request, pk, dpk):
    classe = _classe(request, pk)
    document = get_object_or_404(Document, pk=dpk, classe=classe)
    inline = request.GET.get("inline") == "1"
    content_type = mimetypes.guess_type(document.display_name, strict=False)[0]
    if inline and not can_preview_in_browser(document):
        raise Http404("Ce type de fichier ne peut pas être affiché dans le navigateur.")
    try:
        response = FileResponse(
            document.file.open("rb"), as_attachment=not inline, filename=document.display_name,
            content_type=(content_type if inline else "application/octet-stream"),
        )
        _private_file_response(response)
        if inline:
            set_preview_headers(response)
        return response
    except (OSError, ValueError):
        raise Http404("Fichier introuvable.")


@login_required
@require_GET
def document_folder_zip(request, pk, fpk):
    classe = _classe(request, pk)
    folder = get_object_or_404(DossierDocument, pk=fpk, classe=classe)
    try:
        return _private_file_response(archive_response(folder))
    except (OSError, ValueError) as error:
        messages.error(request, str(error))
        return _documents_redirect(request, classe)


@require_POST
def public_document_upload(request, token, fpk):
    classe = get_object_or_404(Classe, token=token)
    folder = get_object_or_404(DossierDocument.objects.select_related("activite"), pk=fpk, classe=classe)
    return_url = f"{reverse('groupes:public_portal', args=[classe.token])}?view=documents&folder={folder.pk}"
    if not folder_is_public(folder) or not folder.allow_public_uploads:
        raise Http404
    if too_many(request, "public_document_upload", 5, 3600):
        messages.error(request, "Trop de fichiers envoyés depuis ce réseau. Réessaie plus tard.")
        return redirect(return_url)
    if request.POST.get("website"):
        return redirect(return_url)
    form = DocumentUploadForm(request.POST, request.FILES, classe=classe)
    if form.is_valid() and form.cleaned_data["folder"] and form.cleaned_data["folder"].pk == folder.pk:
        document = _save_document(form, classe, None)
        if document is None:
            form.add_error("file", "La classe a atteint sa limite de stockage de 250 Mo.")
        else:
            log_action(request, "document_public_ajoute", classe=classe, detail=f"{folder.name} · {document.display_name}")
            messages.success(request, f"Fichier « {document.display_name} » ajouté.")
            return redirect(return_url)
    if not form.is_valid():
        errors = [str(error) for field_errors in form.errors.values() for error in field_errors]
        messages.error(request, " ".join(errors) or "Fichier invalide.")
    else:
        messages.error(request, "Ce fichier ne correspond pas au dossier sélectionné.")
    return redirect(return_url)


@require_GET
@xframe_options_sameorigin
def public_document_download(request, token, dpk):
    classe = get_object_or_404(Classe, token=token)
    document = get_object_or_404(Document.objects.select_related("classe", "folder", "folder__activite"), pk=dpk, classe=classe)
    if not document.is_public or not classe.public_documents:
        raise Http404
    if document.folder_id and not folder_is_public(document.folder):
        raise Http404
    try:
        can_download = document.allow_public_downloads and (
            document.folder.allow_public_downloads if document.folder_id else True
        )
        inline = request.GET.get("inline") == "1"
        previewable = can_preview_in_browser(document)
        if inline and not previewable:
            raise Http404("Ce type de fichier ne peut pas être affiché dans le navigateur.")
        if can_download and not inline:
            response = FileResponse(
                document.file.open("rb"), as_attachment=True,
                filename=document.display_name, content_type="application/octet-stream",
            )
            return _private_file_response(response)
        if not previewable:
            raise Http404("Ce type de fichier ne peut pas être affiché dans le navigateur.")
        content_type = mimetypes.guess_type(document.display_name, strict=False)[0]
        response = FileResponse(
            document.file.open("rb"), as_attachment=False,
            filename=document.display_name, content_type=content_type,
        )
        _private_file_response(response)
        set_preview_headers(response)
        return response
    except (OSError, ValueError):
        raise Http404("Fichier introuvable.")


@require_GET
def public_document_folder_zip(request, token, fpk):
    classe = get_object_or_404(Classe, token=token)
    folder = get_object_or_404(DossierDocument.objects.select_related("activite"), pk=fpk, classe=classe)
    if not folder_is_public(folder):
        raise Http404
    try:
        return _private_file_response(archive_response(folder, public_only=True))
    except (OSError, ValueError) as error:
        messages.error(request, str(error))
        return redirect(f"{reverse('groupes:public_portal', args=[classe.token])}?view=documents&folder={folder.pk}")
