#!/usr/bin/env bash
# Sauvegarde PostgreSQL (Neon) : ./scripts/backup.sh  (nécessite pg_dump et DATABASE_URL)
set -euo pipefail
: "${DATABASE_URL:?DATABASE_URL est requis}"
mkdir -p backups
file="backups/cohorte-$(date +%Y%m%d-%H%M).sql.gz"
pg_dump --no-owner --no-privileges "$DATABASE_URL" | gzip > "$file"
echo "Sauvegarde créée : $file"
# Conserve les 14 dernières sauvegardes
ls -1t backups/cohorte-*.sql.gz | tail -n +15 | xargs -r rm --
