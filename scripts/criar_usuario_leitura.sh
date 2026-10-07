#!/usr/bin/env bash
# Cria (ou atualiza a senha de) um usuário de leitura do banco de parquets.
#
# O usuário recebe apenas SELECT nas tabelas do schema public. Não tem acesso
# aos arquivos parquet nem ao duckdb.postgres_role, então não consegue ler nada
# fora do banco.
#
# Uso:
#   ./scripts/criar_usuario_leitura.sh NOME_USUARIO [SENHA]
#
# Se a SENHA for omitida, uma senha aleatória é gerada e exibida uma única vez.
# Execute novamente com outro nome para adicionar cada usuário.
#
# Configuração (variáveis de ambiente ou .env na raiz do projeto):
#   PARQUET_DB_CONTAINER  (padrão: tce_parquet_db)
#   PARQUET_DB_NAME       (padrão: tce)
#   PARQUET_DB_ADMIN_USER (padrão: tce_admin)
#   PARQUET_DB_ADMIN_PASSWORD  (obrigatória)
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Carrega o .env da raiz, se existir (sem sobrescrever o ambiente).
if [[ -f "$RAIZ/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$RAIZ/.env"
  set +a
fi

CONTAINER="${PARQUET_DB_CONTAINER:-tce_parquet_db}"
BANCO="${PARQUET_DB_NAME:-tce}"
ADMIN="${PARQUET_DB_ADMIN_USER:-tce_admin}"

if [[ $# -lt 1 ]]; then
  echo "Uso: $0 NOME_USUARIO [SENHA]" >&2
  exit 2
fi

USUARIO="$1"
if [[ ! "$USUARIO" =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]]; then
  echo "Nome de usuário inválido: use apenas letras, números e _ (começando por letra)." >&2
  exit 2
fi
if [[ "$USUARIO" == "$ADMIN" ]]; then
  echo "Escolha um usuário de leitura diferente do administrador ($ADMIN)." >&2
  exit 2
fi

if [[ -n "${2:-}" ]]; then
  SENHA="$2"
else
  SENHA="$(openssl rand -base64 18)"
fi

if [[ -z "${PARQUET_DB_ADMIN_PASSWORD:-}" ]]; then
  echo "Defina PARQUET_DB_ADMIN_PASSWORD no ambiente ou no .env." >&2
  exit 1
fi

if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "Container '$CONTAINER' não encontrado. Suba o serviço parquet-db primeiro." >&2
  exit 1
fi

# Aplica as permissões. Os ALTER DEFAULT PRIVILEGES garantem que toda tabela
# nova criada pelo admin (no staging e no public) já nasça com SELECT para este
# usuário — é o que mantém o acesso válido após cada refresh.
docker exec -i -e PGPASSWORD="$PARQUET_DB_ADMIN_PASSWORD" "$CONTAINER" \
  psql -v ON_ERROR_STOP=1 -q -U "$ADMIN" -d "$BANCO" \
  -v role="$USUARIO" -v pw="$SENHA" <<'SQL'
CREATE SCHEMA IF NOT EXISTS parquet_staging;
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'role', :'pw')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'role', :'pw') \gexec
SELECT format('GRANT USAGE ON SCHEMA public TO %I', :'role') \gexec
SELECT format('GRANT SELECT ON ALL TABLES IN SCHEMA public TO %I', :'role') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO %I', :'role') \gexec
SELECT format('ALTER DEFAULT PRIVILEGES IN SCHEMA parquet_staging GRANT SELECT ON TABLES TO %I', :'role') \gexec
SQL

echo "Usuário de leitura pronto: $USUARIO"
if [[ -z "${2:-}" ]]; then
  echo "Senha gerada: $SENHA"
  echo "(guarde agora; ela não será exibida novamente)"
fi
