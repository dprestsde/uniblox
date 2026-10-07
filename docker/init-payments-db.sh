#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v payments_db="$PAYMENTS_DB_NAME" <<'SQL'
SELECT format('CREATE DATABASE %I', :'payments_db')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'payments_db')
\gexec
SQL
