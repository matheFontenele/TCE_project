# Projeto 1 — TCE-CE SQL Explorer

> **Estado:** núcleo implementado e testado. Aba **🧮 SQL** funcionando no painel
> Streamlit, executando no Postgres + pg_duckdb. Falta apenas a implantação na
> rede local (Fase 4) e o controle de concorrência.
>
> Última atualização: 2026-10-11.

## 1. Visão geral

Criar uma interface web em **Streamlit** para que usuários autorizados na mesma
rede local do servidor possam consultar os arquivos **Parquet** extraídos pelo
pipeline do TCE-CE usando SQL, sem precisar instalar Python, DuckDB ou acessar
diretamente o servidor.

O projeto também deveria investigar e mitigar o problema em que operações do
DuckDB sobre grandes volumes de dados provocavam alto consumo de memória e
encerramento do processo no Linux. **Esse diagnóstico foi concluído** (seção 6) e
a solução está em produção (seção 4).

## 2. Problema atual

- O projeto roda em um servidor Linux local, com **pouca RAM**.
- O pipeline extrai dados e os salva em arquivos Parquet.
- Usuários da rede precisavam consultar esses arquivos sem acesso ao servidor.
- Operações do DuckDB sobre grandes volumes estouravam a memória e o processo
  era encerrado pelo OOM killer do Linux.

## 3. Objetivos

- Disponibilizar consultas SQL via navegador na rede local.
- Evitar cópias desnecessárias dos dados e materialização completa na RAM.
- Reduzir e controlar o consumo de memória.
- Mostrar resultados tabulares de maneira útil, com limite de linhas.
- Exibir erros SQL e informações básicas de execução.
- Restringir consultas e proteger os dados e o servidor.
- Reutilizar os dados do pipeline existente, sem duplicar a extração.

## 4. Arquitetura (implementada)

A decisão que mudou o desenho original: **o SQL do usuário não roda no processo
do Streamlit**. Com dados reais de milhões de linhas num host pequeno, ler
Parquet dentro da aplicação não escala — o processo cai. Quem executa é o
Postgres com a extensão **pg_duckdb**, que lê os Parquets e os publica como
tabelas comuns; o Postgres faz spool em disco e trabalha em lotes.

```text
Usuários da rede local
        |
        v
Interface Streamlit — aba 🧮 SQL
(Editor, resultados, catálogo e erros)
        |
        v
src/modules/execucao_sql.py  ← interface trocável
(usuário somente leitura, LIMIT, timeout)
        |
        v
Postgres + pg_duckdb  (produção)
tabelas materializadas em public
        |
        v
Armazenamento compartilhado de Parquet
(Arquivos produzidos pelo pipeline TCE-CE)
```

Há um segundo backend atrás da mesma interface, `duckdb`, que abre os Parquets
em memória — só para **desenvolvimento local**, sem subir o Postgres.

### Como as tabelas são construídas

O script `scripts/refresh_parquet_db.py` (chamado pela DAG do Airflow ao fim de
cada extração) reconstrói as tabelas em duas etapas, sem bloquear quem consulta:

1. Constrói a tabela nova no schema `parquet_staging`, em **lotes** de arquivos,
   preservando o schema global (`union_by_name`).
2. Move para `public` numa transação curta (DROP + SET SCHEMA).

Detalhes que resolvem o OOM:

- `duckdb.memory_limit` reduzido (padrão 1536 MB) faz o DuckDB transbordar para
  disco em vez de encher a RAM.
- Processamento em lotes evita materializar todas as linhas de uma vez.
- Uma conexão por tabela: cada backend tem sua própria instância DuckDB, e a
  memória só volta ao sistema quando ele encerra.

## 5. Funcionalidades

### Interface Streamlit — estado

- [x] Editor de SQL (`st.text_area`, com consultas de exemplo).
- [x] Botão para executar consulta.
- [x] Exibição de resultados em tabela (`st.dataframe`).
- [x] Limite de linhas configurável (com teto global).
- [x] Mensagens de erro compreensíveis.
- [x] Tempo de execução e aviso de truncamento.
- [x] Catálogo de tabelas e colunas.
- [x] Exportação do resultado em CSV.
- [ ] Paginação (hoje: limite de linhas + refino da consulta).

### Camada de execução — estado

- [x] Interface única e trocável (`executar_sql`).
- [x] `LIMIT` aplicado sempre, envolvendo a consulta do usuário.
- [x] Limite de tempo de execução.
- [x] Limites de memória e threads no backend DuckDB de desenvolvimento.
- [ ] Monitorar o consumo sob consultas simultâneas (Fase 4).

## 6. Diagnóstico inicial — conclusões

O diagnóstico obrigatório da versão anterior deste documento foi concluído:

1. **O que falhava:** materialização das tabelas grandes no DuckDB durante a
   criação das tabelas de consulta (o CTAS carregava os dados de uma vez).
2. **A causa:** o `duckdb.memory_limit` padrão de **4 GB por conexão** — quase
   toda a RAM de um host pequeno — somado ao acúmulo de memória entre tabelas
   na mesma conexão. O kernel matava o Postgres (OOM killer).
3. **A correção:** limite de memória menor + build em lotes + uma conexão por
   tabela (commits `4e6da66` e `abd785d`).
4. **Consequência para o Explorer:** como o build já é seguro, o Explorer
   reaproveita essas tabelas e nunca lê Parquet no processo do Streamlit.

Uma `VIEW` do DuckDB armazena só a definição, não os resultados — o problema
estava na materialização, como o documento já suspeitava.

## 7. Plano de implementação — status

### Fase 1 — Diagnóstico ✅

- [x] Localizar a operação que provocava o encerramento.
- [x] Confirmar recursos do servidor (host com RAM limitada).
- [x] Medir consumo e duração do build.
- [x] Revisar a estratégia de criação das tabelas.

### Fase 2 — Prova de conceito SQL ✅

- [x] Abrir os Parquets com DuckDB.
- [x] Executar consultas representativas.
- [x] Verificar consumo de memória e desempenho.
- [x] Protótipo Streamlit com editor SQL e tabela de resultados
      → `src/modules/sql_explorer.py`, aba **🧮 SQL**.

### Fase 3 — Segurança e controle de recursos ⏳

- [x] Restringir o SQL a operações de leitura permitidas.
- [x] Limitar linhas retornadas e tempo de execução.
- [x] Restringir o acesso a caminhos de arquivo.
- [ ] Controlar consultas simultâneas.
- [ ] Implementar autenticação, se necessária.

### Fase 4 — Implantação na rede local ⏳

- [ ] Configurar o serviço para acesso pela rede local.
- [ ] Validar acesso a partir de outra máquina.
- [ ] Testar consultas simultâneas.
- [ ] Documentar como iniciar, parar, atualizar e monitorar a aplicação.

### Fase 5 — Evolução ⏳

- [x] Criar catálogo de tabelas e colunas.
- [x] Adicionar consultas de exemplo.
- [ ] Registrar métricas e erros sem expor dados sensíveis.
- [ ] Preparar a camada de consulta para o futuro assistente de IA do TCE.

## 8. Implementação — arquivos e uso

### 8.1 Camada de execução — `src/modules/execucao_sql.py`

Sem dependência de Streamlit; é a interface reutilizável (inclusive pelo futuro
assistente de IA). Assinatura principal:

```python
executar_sql(sql, limite_linhas=None, backend=None) -> ResultadoSQL
```

`ResultadoSQL` carrega `colunas`, `linhas`, `truncado`, `tempo_s` e `erro` — sem
DataFrame obrigatório. Nunca levanta exceção por causa do SQL do usuário: erros
de sintaxe, permissão ou tempo esgotado voltam no campo `erro`.

- **`AdaptadorPostgres`** (padrão): usuário somente leitura, sessão marcada com
  `default_transaction_read_only = on`, `statement_timeout` e
  `idle_in_transaction_session_timeout`.
- **`AdaptadorDuckDB`** (dev): `:memory:` com `memory_limit`, reaproveitando o
  agrupamento de parquets do `refresh_parquet_db.py` (ignora marcadores de
  0 colunas).

A consulta é sempre envolvida em:

```sql
SELECT * FROM (<sql do usuário>) AS t LIMIT <n + 1>
```

Aceita `SELECT`/`WITH` sem parser, faz `DDL`/`DML` falharem e evita materializar
o resultado inteiro. O `n + 1` detecta truncamento.

### 8.2 Interface — `src/modules/sql_explorer.py`

`render_sql_explorer_page()`: exemplos, editor, limite de linhas, tabela,
tempo, aviso de truncamento, download CSV e catálogo via `information_schema`
com rótulos amigáveis vindos do `colunas_texto` do `endpoints.json`.

Integrada em `src/app.py` como quinta aba (**🧮 SQL**).

### 8.3 Configuração

Credencial de leitura da aplicação (criada uma vez):

```bash
./scripts/criar_usuario_leitura.sh tce_explorer
```

No `.env` (não versionado):

```dotenv
PARQUET_DB_READ_USER=tce_explorer
PARQUET_DB_READ_PASSWORD=<a senha exibida pelo script>
```

Variáveis do Explorer (todas com padrão no `docker-compose.yml`):

| Variável | Padrão | Efeito |
|---|---|---|
| `TCE_SQL_MAX_ROWS` | `2000` | Teto de linhas devolvidas por consulta |
| `TCE_SQL_TIMEOUT_MS` | `15000` | Tempo máximo de uma consulta, em ms |
| `TCE_SQL_BACKEND` | `postgres` | `postgres` (produção) ou `duckdb` (dev) |
| `TCE_SQL_MEMORY_MB` | `1024` | Limite de RAM do backend DuckDB (dev) |

### 8.4 Como rodar

Produção (Postgres + pg_duckdb):

```bash
docker compose up -d parquet-db app
```

Desenvolvimento, sem subir o Postgres:

```bash
TCE_SQL_BACKEND=duckdb DATA_DIR=data streamlit run src/app.py
```

### 8.5 Testes

`tests/test_execucao_sql.py` — unittest + mock, **sem banco e sem dependências
pesadas** (imports tardios). Cobre: montagem do `LIMIT n+1`, teto de linhas,
detecção de truncamento, reforço de somente-leitura, envio do SQL sem
interpolação de `%`, fechamento de conexão e erros amigáveis.

```bash
PYTHONPATH=. python -m pytest tests/test_execucao_sql.py -v
```

## 9. Segurança — modelo adotado

Como a aplicação aceita SQL digitado pelo usuário, **a barreira é o banco, não
uma blacklist de palavras** (como o documento original e a constituição do
projeto exigem):

- O usuário de leitura só tem `SELECT` nas tabelas de `public`; não enxerga os
  Parquets nem o `duckdb.postgres_role`.
- A sessão inteira é somente leitura (`default_transaction_read_only = on`), o
  que faz qualquer `DDL`/`DML` falhar mesmo que passe pela montagem do `LIMIT`.
- `statement_timeout` e `idle_in_transaction_session_timeout` limitam recursos.
- O `LIMIT n+1` impede materializar resultados gigantes.
- Nenhuma conexão de escrita ou credencial de admin é usada pela aplicação.

## 10. Critérios de sucesso — status

- [x] Usuários consultam os Parquets pelo navegador (falta validar na rede).
- [x] Consultas comuns não carregam todos os dados na RAM.
- [x] Consultas pesadas são limitadas de forma previsível.
- [x] Resultados têm limites claros e exportação.
- [x] Acesso a arquivos e operações não autorizadas é bloqueado.
- [x] O processo de consulta não interfere no pipeline de extração.

## 11. Relação com o projeto de IA

O SQL Explorer é a camada de consulta que o futuro **TCE AI Assistant**
reutilizará. O assistente não deve ter acesso irrestrito ao sistema de arquivos
ou ao banco: deve chamar `executar_sql(...)`, que já concentra validação,
permissões e limites — e é independente de Streamlit justamente para isso.

## 12. Questões em aberto

- Quantos usuários poderão consultar simultaneamente? (define o controle de
  concorrência da Fase 3)
- É necessário autenticar os usuários, ou todos os dispositivos da rede são
  confiáveis?
- Qual o nome/porta definitivos do serviço publicado na rede local? (Fase 4)

Resolvidas: causa do encerramento (seção 6); quantidade de RAM (host limitado,
tratado com limites de memória); versão do DuckDB (embutida na imagem
`pgduckdb:16-v1.1.1`); particionamento dos Parquets (por período no sufixo
`_AAAA[_MM]_NNN.parquet`); a aplicação roda em Docker.

## 13. Stack

- Python 3.10+, Streamlit
- Postgres 16 + pg_duckdb (execução)
- DuckDB (dev local)
- Parquet, PyArrow
- psycopg2, pandas
- Docker Compose, Airflow
- Linux, Git

**Princípio de implementação:** diagnosticar primeiro, executar sob demanda no
banco (nunca no processo da aplicação) e impor limites de segurança e recursos
desde a primeira versão.
