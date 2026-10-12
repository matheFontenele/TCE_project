# Consultas SQL nos parquets (Postgres + pg_duckdb)

Os parquets da pasta `data/` ficam disponíveis para consulta por DBeaver,
DataGrip ou qualquer cliente PostgreSQL. Um container Postgres com a extensão
`pg_duckdb` lê os parquets e os publica como tabelas comuns. Os usuários
conectam em um Postgres normal — **sem driver Trino, sem certificado**.

- **Somente leitura:** cada usuário recebe apenas `SELECT` nas tabelas. Não tem
  acesso aos arquivos nem permissão de escrita.
- **Vários usuários ao mesmo tempo:** é o Postgres normal, cada cliente é uma
  conexão independente.
- **Dados atualizados após cada carga:** o Airflow reconstrói as tabelas ao fim
  de cada extração; quem estiver consultando continua lendo a versão anterior
  até a troca (~1 s).

## Preparar o servidor

No clone do projeto no servidor (`172.16.0.200`), adicione ao `.env` (não
versionado):

```dotenv
PARQUET_DB_ADMIN_PASSWORD=<uma senha forte, só do administrador>
PARQUET_DB_BIND_IP=172.16.0.200
PARQUET_DB_PORT=5433
```

Opcionalmente `PARQUET_DB_ADMIN_USER` (padrão `tce_admin`), `PARQUET_DB_NAME`
(padrão `tce`) e os limites de memória do DuckDB (ver abaixo). Suba o serviço e
faça a carga inicial:

```bash
docker compose up -d parquet-db
docker compose --profile parquet-init run --rm parquet-db-init
```

O build inicial leva cerca de 30 segundos e cria uma tabela por prefixo de
parquet (ex.: `liquidacoes`, `notas_empenho`, `veiculos_municipais`).

Arquivos marcadores vazios (0 colunas) são **ignorados no build** — não viram
tabela, e isso é correto. Mas atenção: eles **não são lixo**. São o *cache
negativo* do extrator, gravado de propósito quando a API não retorna registros
para um mês/município, e nas execuções seguintes evitam a requisição. Por isso
o build os ignora **sem apagá-los**; removê-los força a reconsulta (ver
Constituição, Restrições Adicionais).

## Criar um usuário de leitura

```bash
./scripts/criar_usuario_leitura.sh NOME_DO_USUARIO
```

O script gera uma senha aleatória e a exibe uma única vez (ou aceite uma senha
como segundo argumento). Repita com outro nome para cada usuário. O acesso
`SELECT` é automático para as tabelas atuais e para as que forem criadas nos
próximos refreshes.

## Conexão no DBeaver / DataGrip

Crie uma conexão **PostgreSQL** (driver nativo, sem download extra):

| Campo | Valor |
|---|---|
| Host | `172.16.0.200` |
| Porta | `5433` |
| Banco | `tce` |
| Usuário / Senha | as credenciais criadas acima |

URL JDBC equivalente:

```text
jdbc:postgresql://172.16.0.200:5433/tce
```

Exemplo de consulta:

```sql
SELECT municipio_referencia, count(*) AS n, sum(valor_liquidado) AS total
FROM liquidacoes
GROUP BY 1
ORDER BY n DESC;
```

As tabelas têm o mesmo nome do prefixo dos parquets. Para listar todas:

```sql
SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';
```

## Atualização dos dados

A DAG `tce_extracao_mensal` do Airflow chama
`scripts/refresh_parquet_db.py` depois de cada extração. O script:

1. Constrói cada tabela nova no schema `parquet_staging` (os leitores seguem na
   tabela antiga).
2. Move a tabela nova para `public` numa transação curta, trocando a antiga.

Se um leitor estiver numa consulta longa e segurar a tabela, a troca daquela
tabela é abortada após 5 segundos e repetida na próxima execução — as demais
seguem normalmente, e o banco nunca fica bloqueado.

Para atualizar manualmente a qualquer momento:

```bash
docker compose --profile parquet-init run --rm parquet-db-init
```

## Memória (importante em servidores pequenos)

O `duckdb.memory_limit` padrão é **4 GB por conexão** — num host com pouca RAM
isso faz o kernel matar o Postgres (OOM) ao materializar as tabelas grandes.
Por isso o serviço sobe com limites menores, ajustáveis no `.env`:

```dotenv
PARQUET_DB_MEMORY_LIMIT=1536     # MB de RAM para o DuckDB (padrão 1536)
PARQUET_DB_TEMP_LIMIT=30GB       # teto do spool em disco (padrão 30GB)
```

Com o limite baixo, o DuckDB transborda para o disco em vez de encher a RAM. O
script também abre e fecha uma conexão por tabela: cada conexão tem sua própria
instância DuckDB, então a memória é liberada entre uma tabela e outra em vez de
se acumular.

Se o build ainda falhar com OOM, baixe `PARQUET_DB_MEMORY_LIMIT` (ex.: `1024`)
e confirme que há espaço em disco para o spool. O container é reiniciado
automaticamente após uma queda; basta rodar o refresh de novo — ele é
idempotente e retoma do zero.

## Firewall (opcional)

A porta publicada pelo Docker pode contornar regras simples do UFW. Se for
necessário restringir o acesso à sub-rede interna, aplique uma regra na chain
`DOCKER-USER` liberando TCP `5433` apenas de `172.16.0.0/24`. Sem essa regra, a
porta fica acessível conforme a política padrão do host.

## Solução de problemas

- **"DuckDB execution is not allowed":** a consulta tentou ler arquivos
  diretamente. Usuários de leitura só podem consultar as tabelas de `public`.
- **Tabela não aparece:** confirme que existe parquet válido (com colunas) para
  aquele prefixo em `data/` e rode o refresh manual.
- **Container não sobe:** verifique se `PARQUET_DB_ADMIN_PASSWORD` está no
  `.env` e se a porta `5433` não está em uso.
