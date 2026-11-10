from django.contrib import admin

from .models import Classe, Groupe, Membre


@admin.register(Classe)
class ClasseAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "is_open", "created_at")
    search_fields = ("name", "owner__username")


@admin.register(Groupe)
class GroupeAdmin(admin.ModelAdmin):
    list_display = ("classe", "number", "name", "capacity")


@admin.register(Membre)
class MembreAdmin(admin.ModelAdmin):
    list_display = ("full_name", "classe", "groupe", "verified", "source")
    search_fields = ("full_name",)
