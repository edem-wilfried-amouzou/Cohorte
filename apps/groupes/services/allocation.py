import random

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from ..models import Membre


@transaction.atomic
def auto_assign(classe) -> dict:
    """Répartit les personnes sans groupe de façon équilibrée, en respectant les capacités."""
    groupes = list(classe.groupes.annotate(size=Count("membres")))
    pending = list(classe.membres.sans_groupe())
    if not groupes or not pending:
        return {"assigned": 0, "left": len(pending)}

    load = {g.pk: g.size for g in groupes}
    capacity = {g.pk: g.capacity for g in groupes}
    random.shuffle(pending)
    now = timezone.now()
    done = []
    for member in pending:
        open_groups = [pk for pk in load if capacity[pk] is None or load[pk] < capacity[pk]]
        if not open_groups:
            break
        pk = min(open_groups, key=lambda p: (load[p], p))
        member.groupe_id = pk
        member.responded_at = member.responded_at or now
        load[pk] += 1
        done.append(member)
    Membre.objects.bulk_update(done, ["groupe", "responded_at"])
    return {"assigned": len(done), "left": len(pending) - len(done)}
