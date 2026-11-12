from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    if dictionary is None:
        return ""
    return dictionary.get(key, "")


@register.simple_tag
def member_columns(classe):
    """Colonnes facultatives à afficher : seulement celles que la liste utilise vraiment."""
    cached = getattr(classe, "_member_columns", None)
    if cached is None:
        members = classe.membres
        cached = {
            "number": members.filter(number__isnull=False).exists(),
            "sex": members.exclude(sex="").exists(),
            "reference": members.exclude(reference="").exists(),
        }
        classe._member_columns = cached
    return cached


@register.simple_tag
def custom_columns(classe):
    """Colonnes personnalisées de la classe (aussi disponibles quand une seule ligne est rechargée)."""
    cached = getattr(classe, "_custom_columns", None)
    if cached is None:
        cached = list(classe.champs_membres.all())
        classe._custom_columns = cached
    return cached