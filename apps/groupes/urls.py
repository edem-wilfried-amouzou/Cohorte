from django.urls import path

from . import views

app_name = "groupes"

urlpatterns = [
    path("classes/", views.dashboard, name="dashboard"),
    path("classes/nouvelle/", views.classe_create, name="create"),
    path("classes/<int:pk>/", views.classe_detail, name="detail"),
    path("classes/<int:pk>/reglages/", views.classe_settings, name="settings"),
    path("classes/<int:pk>/nouveau-lien/", views.classe_regenerate, name="regenerate"),
    path("classes/<int:pk>/supprimer/", views.classe_delete, name="delete"),
    path("classes/<int:pk>/qr.svg", views.classe_qr, name="qr"),
    path("classes/<int:pk>/membres/", views.members, name="members"),
    path("classes/<int:pk>/membres/<int:mpk>/modifier/", views.member_edit, name="member_edit"),
    path("classes/<int:pk>/membres/<int:mpk>/supprimer/", views.member_delete, name="member_delete"),
    path("classes/<int:pk>/membres/<int:mpk>/valider/", views.member_verify, name="member_verify"),
    path("classes/<int:pk>/membres/<int:mpk>/affecter/", views.member_assign, name="member_assign"),
    path("classes/<int:pk>/groupes/", views.groups, name="groups"),
    path("classes/<int:pk>/groupes/nouveau/", views.group_create, name="group_create"),
    path("classes/<int:pk>/groupes/serie/", views.groups_bulk, name="groups_bulk"),
    path("classes/<int:pk>/groupes/repartir/", views.auto_assign_view, name="auto_assign"),
    path("classes/<int:pk>/groupes/<int:gpk>/modifier/", views.group_edit, name="group_edit"),
    path("classes/<int:pk>/groupes/<int:gpk>/supprimer/", views.group_delete, name="group_delete"),
    path("classes/<int:pk>/import/", views.import_view, name="import"),
    path("classes/<int:pk>/export/<str:fmt>/", views.export_view, name="export"),
    path("j/<str:token>/", views.join, name="join"),
    path("j/<str:token>/chercher/", views.join_search, name="join_search"),
    path("j/<str:token>/merci/", views.join_done, name="join_done"),
]
