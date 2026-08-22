#!/bin/sh
# Substitute build-time placeholders in static HTML so browsers pick up new JS/CSS
# on each new image version without needing hard-reloads.
REV="${APP_REVISION:-dev}"
SHORT="${REV:0:7}"
sed -i "s/__APP_VERSION__/${SHORT}/g" /app/static/index.html
exec uvicorn app.main:app --host 0.0.0.0 --port 8080
