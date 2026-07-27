-- ─────────────────────────────────────────────────────────────────────
-- Miranda API — criação do usuário e banco no PostgreSQL nativo do Ubuntu.
--
-- Rode este arquivo como o superusuário do PostgreSQL (usuário do SO `postgres`):
--
--     sudo -u postgres psql -v ON_ERROR_STOP=1 -f scripts/setup_db.sql
--
-- Depois, conecte-se ao banco recém-criado para ajustar o schema public:
--
--     sudo -u postgres psql -d miranda -f scripts/setup_db_schema.sql
--
-- (ou simplesmente use o script `scripts/create_db.sh`, que faz tudo de uma vez.)
--
-- IMPORTANTE: a senha abaixo (miranda_senha_local) deve ser IGUAL à que estiver
-- em miranda-api/.env (variável DATABASE_URL). Troque nos dois lugares se quiser
-- outra senha.
-- ─────────────────────────────────────────────────────────────────────

-- Cria o usuário/role da aplicação (idempotente).
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'miranda_user') THEN
    CREATE ROLE miranda_user LOGIN PASSWORD 'miranda_senha_local';
  END IF;
END
$$;

-- Cria o banco com o usuário da aplicação como dono (idempotente via \gexec).
SELECT 'CREATE DATABASE miranda OWNER miranda_user'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'miranda')\gexec

GRANT ALL PRIVILEGES ON DATABASE miranda TO miranda_user;
