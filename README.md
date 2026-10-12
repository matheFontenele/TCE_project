# Automação de Extração de Dados — TCE-CE

Sistema de **extração, validação, qualificação e publicação** automatizada dos
dados abertos do TCE-CE (Tribunal de Contas do Estado do Ceará). Os dados são
baixados da API pública, validados em duas camadas, persistidos em Parquet e
publicados para consulta SQL via Postgres + `pg_duckdb`.

O projeto cobre **21 tipos de documento** — de notas de empenho e liquidações a
veículos municipais e bens patrimoniais.

---

## Sumário

- [Visão geral](#visão-geral)
- [Arquitetura do pipeline](#arquitetura-do-pipeline)
- [Stack técnica](#stack-técnica)
- [Como utilizar](#como-utilizar)
  - [Usuário final (painel)](#usuário-final-painel)
  - [Desenvolvimento (Docker Compose)](#desenvolvimento-docker-compose)
- [Estrutura do repositório](#estrutura-do-repositório)
- [Dados e tabelas](#dados-e-tabelas)
- [Consultas](#consultas)
- [Qualidade e testes](#qualidade-e-testes)
- [Orquestração (Airflow)](#orquestração-airflow)
- [Governança e Spec Kit](#governança-e-spec-kit)
- [IA e suportes utilizados](#ia-e-suportes-utilizados)
- [Suporte](#suporte)

---

## Visão geral

O que o sistema faz, ponta a ponta:

1. **Extrai** os endpoints da API de dados abertos do TCE-CE, por ano, mês e
   município (`src/modules/extraction.py`).
2. **Valida linha a linha** contra um schema declarado — posicional (layout de
   largura fixa) ou delimitado (`src/validators/`).
3. **Qualifica o dataset** com Great Expectations: nulos aceitáveis, contagem
   mínima de registros, etc. (`src/quality/`).
4. **Persiste** cada lote em Parquet sob `data/` — a fonte de verdade.
5. **Publica** tabelas somente-leitura num Postgres com `pg_duckdb`, acessíveis
   por DBeaver/DataGrip na rede interna.

O ciclo mensal é orquestrado pelo Airflow; o painel Streamlit serve para
extração manual, visualização e geração de relatórios.

---

## Arquitetura do pipeline

```text
        API TCE-CE (dados abertos)
                │
                ▼
     ┌──────────────────────┐
     │  EXTRAÇÃO            │  src/modules/extraction.py
     │  requests + retry    │  • por ano / mês / município
     │  rate-limit + backoff│  • retoma de onde parou
     └──────────┬───────────┘
                │
                ▼
     ┌──────────────────────┐
     │  VALIDAÇÃO (camada 1)│  src/validators/
     │  linha a linha       │  • posicional e delimitado
     │  via schema único    │  • regras declarativas (Polars)
     └──────────┬───────────┘
                │
                ▼
     ┌──────────────────────┐
     │  QUALIDADE (camada 2)│  src/quality/
     │  Great Expectations  │  • suites derivadas do schema
     └──────────┬───────────┘
                │
                ▼
     ┌──────────────────────┐
     │  PARQUET             │  data/  (não versionado)
     │  fonte de verdade    │  • gravação atômica
     └──────────┬───────────┘
                │
                ▼
     ┌──────────────────────┐
     │  PUBLICAÇÃO          │  scripts/refresh_parquet_db.py
     │  Postgres + pg_duckdb│  • staging → troca em transação curta
     │  somente leitura     │  • nunca bloqueia leitores
     └──────────────────────┘
```

---

## Stack técnica

| Camada | Tecnologia |
|---|---|
| Linguagem | Python 3.12 |
| Extração HTTP | `requests` (com retry e backoff em HTTP 400) |
| Dataframes | `pandas`, `polars` |
| Persistência | Parquet via `pyarrow` |
| Qualidade | Great Expectations |
| Interface | Streamlit |
| Orquestração | Apache Airflow (LocalExecutor) |
| Consulta SQL | Postgres 16 + extensão `pg_duckdb` |
| Relatórios | `reportlab` (PDF), `openpyxl` (Excel) |
| Testes | `pytest` |
| Empacotamento | Docker + Docker Compose |
| CI/CD | GitHub Actions (testes + deploy self-hosted) |

---

## Como utilizar

### Usuário final (painel)

1. Abra a pasta do projeto.
2. Dê um **duplo clique** no arquivo `run.bat`.
3. Aguarde o terminal carregar as dependências automaticamente.
4. O navegador abrirá o **Painel de Controle**.

> `run.bat` é gerado pelo instalador e **não é versionado** (consta no
> `.gitignore`). Em um clone do repositório, use o caminho de desenvolvimento
> abaixo.

No painel:

- **Extração** — selecione o **Ano Base** e clique em **Executar Extração**.
- **Visualização** — navegue pelas abas de *Notas de Empenho*, *Notas Fiscais*,
  etc.
- **Dados** — os Parquet resultantes ficam em `data/`.

### Desenvolvimento (Docker Compose)

Requisitos: Docker e Docker Compose. **Não** se assume Python do host.

```bash
# 1. Crie o .env a partir do modelo e defina os segredos
cp .env.example .env   # se disponível; caso não, crie manualmente

# 2. Suba a aplicação (Streamlit em http://localhost:8501)
docker compose up -d --build app

# 3. Extração manual / backfill (ano inicial e final)
docker compose run --rm extrator

# 4. Wizard interativo de terminal
docker compose --profile tools run --rm cli
```

Serviços principais do `docker-compose.yml`:

| Serviço | Descrição | Porta |
|---|---|---|
| `app` | Painel Streamlit | `8501` |
| `extrator` | Extração manual / backfill | — |
| `cli` | Wizard interativo (profile `tools`) | — |
| `airflow-webserver` | UI do Airflow | `8085` → 8080 |
| `airflow-scheduler` | Scheduler das DAGs | — |
| `parquet-db` | Postgres + `pg_duckdb` (somente leitura) | `5433` |
| `parquet-db-init` | Build/refresh das tabelas (profile `parquet-init`) | — |

> Variáveis obrigatórias usam `:?` e **falham cedo** quando ausentes —
> ex.: `PARQUET_DB_ADMIN_PASSWORD`. Veja [Consultas](#consultas).

---

## Estrutura do repositório

```text
.
├── src/
│   ├── app.py                     # Entrada do painel Streamlit
│   ├── main.py                    # Wizard interativo de terminal (CLI)
│   ├── worker_extrator.py         # Worker de extração
│   ├── components/                # Componentes de UI (cards, modais)
│   ├── modules/
│   │   ├── extraction.py          # Núcleo da extração (HTTP, retry, Parquet)
│   │   ├── validacao.py           # Orquestração da validação
│   │   ├── qualidade.py           # Integração com Great Expectations
│   │   ├── exportadores.py        # Exportação (Excel/relatórios)
│   │   ├── gerador_pdf.py         # Geração de PDF (reportlab)
│   │   ├── motor_relacional.py    # Motor de relatórios relacionais
│   │   ├── consultation.py        # Consultas sobre os dados extraídos
│   │   ├── compare.py             # Comparação entre períodos
│   │   ├── data_utils.py          # Utilidades de dados
│   │   ├── tradutor_veiculos.py   # Tradução de códigos de veículos
│   │   └── rules/                 # Regras de negócio específicas
│   ├── schemas/                   # FONTE ÚNICA DA VERDADE dos contratos
│   │   ├── schema_campo.py        # Campo (posicional ou delimitado)
│   │   └── veiculos.py            # Catálogo de schemas de veículos
│   ├── validators/                # Validação linha a linha
│   │   ├── posicional.py          # Layout de largura fixa
│   │   ├── posicional_polars.py   # Versão vetorizada (Polars)
│   │   ├── delimitado.py          # Layout delimitado
│   │   ├── delimitado_polars.py   # Versão vetorizada (Polars)
│   │   ├── motor_polars.py        # Motor de validação em Polars
│   │   └── regras_declarativas.py # Regras declarativas (formato preferido)
│   ├── quality/                   # Qualidade de dataset (Great Expectations)
│   │   ├── suites.py              # Suites derivadas do schema
│   │   ├── dataframe_builder.py
│   │   ├── parquet_validator.py
│   │   └── runner.py
│   ├── orchestration/airflow/     # DAGs e Dockerfile do Airflow
│   │   └── dags/tce_extracao_mensal.py
│   └── tests/                     # Testes internos do pacote
├── tests/                         # Suíte pytest (CI)
├── scripts/
│   ├── refresh_parquet_db.py      # Build/refresh das tabelas publicadas
│   ├── criar_views.py             # Views locais para DuckDB/DataGrip
│   └── criar_usuario_leitura.sh   # Cria usuário SELECT-only no Postgres
├── docs/
│   └── acesso-parquet-db.md       # Guia de consulta por Postgres + pg_duckdb
├── .specify/                      # Spec Kit: constituição, templates, scripts
│   └── memory/constitution.md     # Constituição do projeto
├── .claude/skills/                # Skills Spec Kit para Claude Code
├── .opencode/commands/            # Comandos Spec Kit para opencode
├── endpoints.json                 # Catálogo dos 21 endpoints da API
├── municipios.json                # Lista oficial de municípios do TCE-CE
├── data/                          # Parquets extraídos (NÃO versionado)
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

---

## Dados e tabelas

Os **21 tipos de documento** em `endpoints.json`:

| # | Tipo | # | Tipo |
|---|---|---|---|
| 1 | Notas de Empenho | 12 | Controle de bens e notas de empenho |
| 2 | Notas Fiscais | 13 | Contas Redutoras e Bens Incorporados |
| 3 | Notas de Pagamento | 14 | Itens de Notas Fiscais |
| 4 | Cheques de Notas de Pagamento | 15 | Veículos Municipais |
| 5 | Notas de Pagamento da Folha | 16 | Veículos Locados |
| 6 | Despesas Extra Orçamentárias | 17 | Veículos Cedidos a Terceiros |
| 7 | Pagamento e Liquidações | 18 | Destinação de Veículos |
| 8 | Liquidações | 19 | Baixa de Destinação de Veículos |
| 9 | Bens Incorporados ao Patrimônio | 20 | Controle de Abastecimento de Veículos |
| 10 | Controle de Alocação (Bens em Órgãos) | 21 | Controle de Manutenção de Veículos |
| 11 | Ajuste, Reavaliação e Obras em Bens | | |

**Onde os dados ficam:**

- `data/` — Parquets extraídos. **Fonte de verdade**, não versionada.
- `tce.duckdb` — apenas *views* apontando para os Parquets; gerado localmente,
  não versionado.

**Parquets vazios (0 colunas) são cache negativo intencional.** O extrator grava
um marcador vazio quando a API não retorna registros para um mês/município e, nas
execuções seguintes, **pula a requisição** ao encontrar esse arquivo. Eles são
estado, não sujeira: scripts de limpeza, deploy ou migração **não devem**
apagá-los (ver [Governança](#governança-e-spec-kit)).

---

## Consultas

Há dois caminhos, conforme o consumidor.

### Local — DataGrip / DuckDB

1. Instale as dependências do projeto, incluindo DuckDB e PyArrow.
2. Feche/desconecte no DataGrip a conexão com `tce.duckdb` antes de recriar as
   views.
3. Na raiz do projeto, execute `python scripts/criar_views.py`.
4. No DataGrip, conecte-se ao arquivo `tce.duckdb` e atualize o schema `main`.

O script encontra `data/` relativa à sua localização, ignora Parquets vazios
(marcadores) e cria uma view por tabela, combinando os arquivos com
`union_by_name`. Para atualizar, repita os passos 2–4.

### Remoto — Postgres + `pg_duckdb`

Para que outras pessoas consultem por DBeaver/DataGrip **sem acesso ao
servidor**, o projeto sobe um container Postgres com `pg_duckdb` que publica as
tabelas por rede, somente leitura (porta `5433`). Usuários conectam num Postgres
normal — sem driver Trino, sem certificado.

Guia completo: **[`docs/acesso-parquet-db.md`](docs/acesso-parquet-db.md)**.

Exemplo:

```sql
SELECT municipio_referencia, count(*) AS n, sum(valor_liquidado) AS total
FROM liquidacoes
GROUP BY 1
ORDER BY n DESC;
```

> **Memória:** o `duckdb.memory_limit` é limitado de propósito (padrão 1536 MB,
> `PARQUET_DB_MEMORY_LIMIT`) para que o DuckDB transborde para disco em vez de
> causar OOM no Postgres. Builds processam Parquets em lotes.

---

## Qualidade e testes

**Validação em duas camadas (não negociável).** Nenhum dado é considerado válido
por passar em apenas uma:

1. **Linha a linha** — campos, tipos e regras de negócio (`src/validators/`).
2. **Qualidade de dataset** — expectativas de Great Expectations
   (`src/quality/`).

Ambas derivam do **mesmo schema** em `src/schemas/`, eliminando a classe de bug
em que o validador e a checagem de dataset discordam sobre o mesmo campo. Uma
carga que falha em qualquer camada **não** substitui a tabela publicada
anteriormente.

Rodar a suíte localmente:

```bash
pytest tests/ -v
```

A suíte inclui testes de validadores, motor Polars, validador de Parquet e
refresh do `parquet-db`.

---

## Orquestração (Airflow)

A DAG `tce_extracao_mensal` (`src/orchestration/airflow/dags/`) faz o backfill
incremental diário e, ao final de cada extração, chama
`scripts/refresh_parquet_db.py` para reconstruir as tabelas publicadas.

- **Retry:** 3 tentativas com backoff exponencial (até 6 h).
- **Alertas:** falha notifica via `SLACK_WEBHOOK_URL` (quando definido).
- **Timezone:** `America/Fortaleza`.
- **Refresh não-bloqueante:** cada tabela é construída no schema
  `parquet_staging` e trocada em transação curta; se um leitor segurar a tabela,
  a troca daquela tabela é abortada após 5 s e repetida na próxima execução —
  as demais seguem, e o banco nunca fica bloqueado.

UI do Airflow em `http://localhost:8085`.

---

## Governança e Spec Kit

O projeto adota **Spec-Driven Development** via [Spec Kit](https://github.com/github/spec-kit)
(versão instalada: **1.1.3**). O ciclo é
`specify → plan → tasks → implement`, com gates de revisão.

- **Constituição:** [`.specify/memory/constitution.md`](.specify/memory/constitution.md)
  — versão **1.2.0**. Seis princípios:

  | # | Princípio |
  |---|---|
  | I | Schema como Fonte Única da Verdade |
  | II | Validação em Duas Camadas **(NON-NEGOTIABLE)** |
  | III | Publicação de Dados Somente-Leitura |
  | IV | Reprodutibilidade por Contêiner |
  | V | Testes Obrigatórios e CI Verde |
  | VI | Registro de Decisões nos Artefatos |

- **Princípio VI** — toda decisão tomada durante especificação, planejamento,
  implementação ou revisão **deve** ser registrada no artefato Markdown do seu
  domínio (`spec.md`, `plan.md`, `research.md`, `tasks.md`, `docs/`, README ou a
  própria constituição), **com o contexto**: o que foi decidido, por quê, as
  alternativas consideradas e o que foi descartado.

- **Comandos disponíveis:** `/speckit-constitution`, `/speckit-specify`,
  `/speckit-plan`, `/speckit-tasks`, `/speckit-implement`, `/speckit-analyze`,
  `/speckit-checklist`, `/speckit-clarify`, `/speckit-converge`,
  `/speckit-taskstoissues`.

**CI/CD:**

| Workflow | Gatilho | Ação |
|---|---|---|
| `.github/workflows/tests.yml` | push/PR em `main`, `master` | `pytest tests/ -v` |
| `.github/workflows/deploy-production.yml` | push em `main` | deploy self-hosted via `docker compose up -d --build` |

O deploy de produção dispara da `main` — portanto, **merge sem CI verde é
proibido**.

---

## IA e suportes utilizados

Ferramentas de assistência por IA empregadas no desenvolvimento deste projeto.
Os presets de modelo ficam em configuração local (não versionada) — a tabela
registra o que está configurado no ambiente de desenvolvimento.

### Ferramentas

| Ferramenta | Papel no projeto | Onde |
|---|---|---|
| **Claude Code** | Assistente principal via terminal; executa o ciclo Spec Kit | `.claude/skills/` |
| **opencode** | CLI alternativa de agente; mesmo ciclo Spec Kit | `.opencode/commands/` |
| **Spec Kit 1.1.3** | Framework de Spec-Driven Development | `.specify/` |
| **oh-my-opencode-slim** | Plugin de orquestração de agentes do opencode | config local |

Ambas as integrações (Claude Code e opencode) foram instaladas e versionadas no
repositório — ver commits `chore: adotar Spec Kit (opencode)` e
`chore: adicionar integração Spec Kit para Claude`.

### Modelos por agente (preset `openai`, ativo no opencode)

| Agente | Modelo | Variante |
|---|---|---|
| orchestrator | `openai/gpt-6-sol` | high |
| oracle | `openai/gpt-6-astra` | high |
| librarian | `openai/gpt-6-luna` | low |
| explorer | `openai/gpt-6-luna` | low |
| designer | `openai/gpt-6-luna` | medium |
| fixer | `openai/gpt-6-luna` | high |

Preset alternativo `opencode-go` (disponível na config): `minimax-m3`
(orchestrator), `qwen3.7-max` (oracle), `deepseek-v4-flash`
(explorer/librarian/fixer), `kimi-k2.7-code` (designer), `mimo-v2.5`
(observer).

> O modelo do **Claude Code** é definido por sessão, não fixado no repositório.

### Ferramentas de suporte

| Ferramenta | Uso |
|---|---|
| Docker / Docker Compose | Empacotamento e execução de todos os serviços |
| Apache Airflow | Orquestração das cargas recorrentes |
| Great Expectations | Qualidade de dataset |
| pytest | Testes automatizados |
| GitHub Actions | CI (testes) e deploy self-hosted |
| pg_duckdb (`pgduckdb:16-v1.1.1`) | Consulta SQL sobre os Parquets |
| DBeaver / DataGrip | Clientes de consulta dos usuários |
| Streamlit | Painel de controle |

---

## Suporte

Em caso de erro ou necessidade de atualização, contate o responsável pelo
desenvolvimento do sistema.
