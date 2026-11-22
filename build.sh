#!/usr/bin/env bash
set -euo pipefail

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
npm ci --include=dev
npm run build:css
python manage.py collectstatic --noinput --ignore=src/tailwind.css
python manage.py migrate --noinput
python manage.py createcachetable
