from django.contrib import admin

from .models import AuditLog, Classe, Commentaire, Groupe, Membre


@admin.register(Classe)
class ClasseAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "is_open", "created_at")
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
    list_display = ("created_at", "action", "user", "classe", "ip")
    list_filter = ("action",)
    readonly_fields = ("user", "classe", "action", "detail", "ip", "created_at")

    def has_add_permission(self, request):
        return False
