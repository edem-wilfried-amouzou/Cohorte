# Cohorte

Plateforme Django pour gérer les classes, activités (exposés/projets), groupes et participations.

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
2. **Exposés & projets** → crée une activité, définis ses groupes et répartis les membres.
3. Partage son lien d’inscription ou active un lien de consultation publique en lecture seule.
4. Suis les réponses et affecte plusieurs membres en une action depuis l’annuaire.
5. Exporte chaque activité en PDF, Excel, Word ou CSV.

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

### Neon + Render

Le projet garde SQLite pour le développement local. En production, il utilise PostgreSQL dès qu’une variable `DATABASE_URL` est définie. Pour Neon, crée un projet et une base, puis copie l’URL de connexion **pooled** depuis le tableau de bord Neon. Ne mets jamais cette URL dans Git.

1. Dans Neon, copie la chaîne PostgreSQL pooled; vérifie qu’elle inclut TLS (`sslmode=require`).
2. Crée un Web Service Render à partir du dépôt et utilise le blueprint `render.yaml`.
3. Dans les variables du service Render, renseigne `DATABASE_URL` avec l’URL Neon pooled. Render génère la clé Django; le blueprint active `DEBUG=0`, le proxy HTTPS et applique les migrations au démarrage avant de lancer Gunicorn.
4. Ajoute les domaines personnalisés dans `DJANGO_ALLOWED_HOSTS` et `DJANGO_CSRF_TRUSTED_ORIGINS` si tu en utilises. Render détecte automatiquement son hostname public.

Commandes équivalentes pour un autre hébergeur WSGI :

```bash
pip install -r requirements.txt
python manage.py check --deploy
python manage.py migrate
python manage.py collectstatic --noinput
gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 2 --threads 4 --timeout 120
```

`DATABASE_URL` est analysée avec `dj-database-url`; PostgreSQL utilise `psycopg`, TLS est requis en production et les curseurs serveur sont désactivés pour la compatibilité avec le pooler Neon. Ajuste `DB_CONN_MAX_AGE` à l’hébergeur. `DJANGO_BEHIND_PROXY=1` doit être activé derrière un proxy HTTPS.

Les données de `db.sqlite3` ne sont pas copiées automatiquement vers Neon. Pour garder les données locales, effectue un export/import de données Django ou une migration dédiée avant la mise en service; vérifie également les utilisateurs et fichiers nécessaires. La sauvegarde SQLite reste à conserver tant que la vérification des données PostgreSQL n’est pas terminée.

Le service web Render gratuit peut s’endormir après inactivité; pour un usage régulier, choisis une instance adaptée. Configure également un cache partagé (Redis) avant d’utiliser plusieurs instances, car le limiteur de requêtes actuel repose sur le cache local.

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
