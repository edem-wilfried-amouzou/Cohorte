import logging

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

from .models import Document

logger = logging.getLogger(__name__)


@receiver(post_delete, sender=Document)
def remove_document_file(sender, instance, **kwargs):
    """Supprime le fichier physique aussi quand Django supprime un dossier en cascade."""
    if instance.file and instance.file.name:
        try:
            instance.file.storage.delete(instance.file.name)
        except OSError:
            logger.exception("Impossible de supprimer le fichier %s", instance.file.name)


@receiver(pre_save)
def enforce_single_superuser(sender, instance, raw=False, **kwargs):
    """Un seul compte peut conserver le privilège superuser dans la base."""
    if raw or sender is not get_user_model() or not getattr(instance, "is_superuser", False):
        return
    existing = sender.objects.filter(is_superuser=True)
    if instance.pk:
        existing = existing.exclude(pk=instance.pk)
    if existing.exists():
        raise ValidationError("Un seul super administrateur est autorisé sur Cohorte.")
