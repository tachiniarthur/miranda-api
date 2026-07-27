#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Cria (de forma idempotente) o usuário e o banco do Miranda no PostgreSQL
# nativo do Ubuntu, e ajusta o schema public.
#
# Rode como o superusuário do PostgreSQL:
#     sudo -u postgres bash miranda-api/scripts/create_db.sh
#
# É possível sobrescrever nome/senha via variáveis de ambiente:
#     sudo -u postgres MIRANDA_DB_PASSWORD='outra' bash .../create_db.sh
# (mas lembre de manter a mesma senha em miranda-api/.env).
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

DB_NAME="${MIRANDA_DB_NAME:-miranda}"
DB_USER="${MIRANDA_DB_USER:-miranda_user}"
DB_PASS="${MIRANDA_DB_PASSWORD:-miranda_senha_local}"

echo "→ Criando role '${DB_USER}' e banco '${DB_NAME}' (se não existirem)…"

psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS}';
  END IF;
END
\$\$;

SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')\gexec

GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
SQL

echo "→ Ajustando schema public do banco '${DB_NAME}'…"
psql -v ON_ERROR_STOP=1 -d "${DB_NAME}" <<SQL
ALTER SCHEMA public OWNER TO ${DB_USER};
GRANT ALL ON SCHEMA public TO ${DB_USER};
SQL

echo "✓ Pronto: role '${DB_USER}' e banco '${DB_NAME}' configurados."
