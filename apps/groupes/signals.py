import logging

from django.db.models.signals import post_delete
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
