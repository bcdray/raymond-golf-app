#!/bin/sh
# Write credentials from env var to file if present
if [ -n "$GOOGLE_CREDENTIALS" ]; then
    echo "$GOOGLE_CREDENTIALS" > /app/credentials.json
fi
exec gunicorn --bind :$PORT --workers 1 --threads 2 app:app
