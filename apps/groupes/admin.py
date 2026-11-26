from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.admin.sites import NotRegistered

from .models import AuditLog, Classe, Commentaire, Document, DossierDocument, Groupe, Membre, RetourPlateforme

UserModel = get_user_model()
try:
    admin.site.unregister(UserModel)
except NotRegistered:
    pass


@admin.register(UserModel)
class ReadOnlyUserAdmin(admin.ModelAdmin):
    list_display = ("username", "email", "is_staff", "is_superuser", "is_active", "last_login")
    list_filter = ("is_staff", "is_superuser", "is_active")
    search_fields = ("username", "email", "first_name", "last_name")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Classe)
class ClasseAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "is_open", "public_documents", "created_at")
    list_filter = ("is_open", "public_documents")
    search_fields = ("name", "owner__username")


@admin.register(Groupe)
class GroupeAdmin(admin.ModelAdmin):
    list_display = ("classe", "number", "name", "capacity", "leader")


@admin.register(Membre)
class MembreAdmin(admin.ModelAdmin):
    list_display = ("full_name", "classe", "groupe", "verified", "source")
    search_fields = ("full_name",)


@admin.register(Commentaire)
class CommentaireAdmin(admin.ModelAdmin):
    list_display = ("activite", "author", "is_public", "created_at")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor_identifier", "actor_role", "action", "object_label", "ip", "country", "city", "response_status", "succeeded")
    list_filter = ("action", "country_code", "succeeded", "response_status", "device", "browser")
    search_fields = ("ip", "country", "city", "region", "path", "object_id", "object_label", "user__username", "actor_identifier", "detail")
    readonly_fields = (
        "user", "actor_user_id", "actor_identifier", "actor_role", "classe", "action", "detail", "object_type", "object_id",
        "object_label", "path", "response_status", "succeeded", "before_data", "after_data", "ip", "country",
        "country_code", "region", "city", "latitude", "longitude", "device", "browser", "operating_system",
        "user_agent", "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DossierDocument)
class DossierDocumentAdmin(admin.ModelAdmin):
    list_display = ("name", "classe", "activite", "parent", "is_public", "allow_public_downloads", "allow_public_uploads", "created_at")
    list_filter = ("is_public", "allow_public_downloads", "allow_public_uploads")
    search_fields = ("name", "classe__name", "activite__title")


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("display_name", "classe", "folder", "is_public", "allow_public_downloads", "byte_size", "uploaded_by", "uploaded_at")
    list_filter = ("is_public", "allow_public_downloads", "uploaded_at")
    search_fields = ("display_name", "classe__name", "folder__name")
    readonly_fields = ("classe", "folder", "file", "display_name", "byte_size", "uploaded_by", "uploaded_at")

    def has_add_permission(self, request):
        return False


@admin.register(RetourPlateforme)
class RetourPlateformeAdmin(admin.ModelAdmin):
    list_display = ("created_at", "kind_label", "subject", "submitter_name", "submitter_email", "status_label")
    list_filter = ("kind", "status", "created_at")
    search_fields = ("subject", "message", "submitter_name", "submitter_email")
    readonly_fields = ("kind", "subject", "message", "submitter_name", "submitter_email", "source_path", "submitted_by", "created_at", "updated_at")
    fields = readonly_fields + ("status", "admin_note", "handled_by")

    def has_add_permission(self, request):
        return False
