-- Ajusta o schema `public` do banco `miranda` para que o usuário da aplicação
-- (miranda_user) possa criar tabelas (necessário a partir do PostgreSQL 15).
-- Rode conectado ao banco `miranda`:
--     sudo -u postgres psql -d miranda -f scripts/setup_db_schema.sql

ALTER SCHEMA public OWNER TO miranda_user;
GRANT ALL ON SCHEMA public TO miranda_user;
