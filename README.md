# Cohorte

Plateforme Django pour recenser qui est dans quel groupe, et qui n'en a pas.

## Démarrage

```bash
python -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                  # Windows : copy .env.example .env
python manage.py migrate
python manage.py runserver
```

Ouvre http://127.0.0.1:8000, crée ton compte, puis :

1. **Nouvelle classe** → importe la liste de la salle (xlsx, csv, txt ou texte collé).
2. **Groupes** → crée-les un par un ou de 1 à N, avec capacité facultative.
3. **Vue d'ensemble** → copie le lien (ou le QR code) et envoie-le à la classe.
4. Suis les réponses, glisse les « sans groupe » dans un groupe, ou clique sur *Répartir automatiquement*.
5. **Exporte** en PDF, Excel, Word ou CSV.

## Ce que fait le lien public

- Recherche du nom dans la liste (suggestions pendant la frappe).
- Choix du groupe parmi des cases numérotées, ou « Je n'ai pas de groupe ».
- Un nom ne peut répondre qu'une fois : le délégué corrige ensuite depuis son tableau de bord.
- Groupes complets bloqués. Noms hors liste : refusés, ou acceptés « à vérifier » (réglage de la classe).
- Date limite et fermeture manuelle du lien. Tu peux régénérer le lien à tout moment.

## Format d'import

Une colonne de noms suffit. Si la première ligne contient des titres, sont reconnus : `Nom` (ou Nom complet, Étudiant), `Groupe` (crée les groupes manquants), `Matricule`.

## Sécurité

- Chaque classe n'est visible que par son propriétaire (404 sinon).
- CSRF, CSP, X-Frame-Options, cookies HttpOnly/SameSite, HTTPS + HSTS hors mode debug.
- Limiteur de requêtes et champ piège sur le lien public.
- Import validé (extension, taille, signature xlsx), exports protégés contre l'injection de formules.
- Mots de passe : 10 caractères minimum, validateurs Django activés.

## Production

```bash
# .env : DJANGO_DEBUG=0, DJANGO_SECRET_KEY=<longue valeur aléatoire>,
#        DJANGO_ALLOWED_HOSTS=ton-domaine.com, DJANGO_CSRF_TRUSTED_ORIGINS=https://ton-domaine.com
python manage.py collectstatic --noinput
gunicorn config.wsgi
```

Derrière un proxy HTTPS, mets `DJANGO_BEHIND_PROXY=1`. Pour plusieurs serveurs, remplace le cache local (limiteur) par Redis.

## Tests

```bash
DJANGO_DEBUG=1 python manage.py test
```

## Structure

```
apps/accounts      inscription, connexion
apps/groupes       modèles, vues, formulaires
apps/groupes/services   importing.py · allocation.py · exporting.py · qr.py
templates/ static/      interface (CSS terracotta, JS sans dépendance sauf HTMX)
```
