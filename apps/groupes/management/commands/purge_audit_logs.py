from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.groupes.models import AuditLog


class Command(BaseCommand):
    help = "Purge les journaux d’audit plus anciens que la durée de conservation."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=365, help="Durée de conservation en jours (défaut : 365).")
        parser.add_argument("--confirm", action="store_true", help="Confirme la purge irréversible des journaux expirés.")

    def handle(self, *args, **options):
        days = options["days"]
        if days < 1:
            raise CommandError("--days doit être supérieur ou égal à 1.")
        expired = AuditLog.objects.filter(created_at__lt=timezone.now() - timedelta(days=days))
        count = expired.count()
        if not options["confirm"]:
            self.stdout.write(f"{count} journal(aux) expiré(s). Aucun n’a été supprimé; ajoute --confirm pour purger.")
            return
        with transaction.atomic():
            deleted = expired._raw_delete(using=expired.db)
        self.stdout.write(self.style.SUCCESS(f"{deleted} journal(aux) purgé(s)."))
