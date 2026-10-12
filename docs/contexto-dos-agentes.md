# Contexto dos agentes — estado consolidado

> Documento de handoff. Reúne, num só lugar, o que foi trabalhado em paralelo por
> várias sessões de agente (cada uma num worktree/branch) e que antes só existia
> disperso em transcripts. Escrito em 2026-10-11, ao final do dia de trabalho.
> Serve para retomar o projeto em outra máquina sem reler as conversas.

## 1. O que foi feito hoje, por frente

| Frente | Branch / worktree | Estado | Entregável principal |
|---|---|---|---|
| Guarda de disco no Airflow | `matheus/airflow` | **commitado** (`ab37d88`, em `main`) | `src/quality/armazenamento.py` + task `verificar_armazenamento` no DAG |
| SQL Explorer (Streamlit) | `matheus/QuerySQL_Streamlit` | **commitado** (`508586a`) | aba 🧮 SQL + `src/modules/execucao_sql.py` |
| TCE AI Assistant (planejamento) | `matheus/IA_agent_TCE` | **commitado** (`f67787f`) | `docs/tce_ai_assistant_planejamento.md` (só plano, sem código) |
| Spec Kit + constituição + schema | `adotar-spec-kit` | **commitado** (`d56e043`) | constituição v1.2.0 + `src/schemas/schema_campo.py` |
| Infra / `.env` / README | checkout principal | **commitado** (`d56e043`) | `.env` gerado (não versionado), README reescrito |

Todas as frentes foram integradas em `main` (merges `762541b`, `1e794c3`,
`b048ecc`). As branches `matheus/*` continuam existindo como histórico.

## 2. Diagnóstico medido do OOM (SQL Explorer) — o achado técnico mais importante

Este diagnóstico foi **medido**, não inferido, rodando no container `tce_app`
(DuckDB 1.0.0) sobre os dados reais do checkout principal — tabela
`notas_empenho`: **462 MB, 5.728 arquivos, ~2,6 milhões de linhas**.

| Cenário | Tempo | Pico RSS | Resultado |
|---|---|---|---|
| Amostra de 500 arquivos — `count` / `group by` | 0,5–0,9 s | ~228 MB | OK |
| Tabela completa (5.704 arqs) — `count` + `group by` | 2,9–3,4 s | 737–760 MB | OK **desde que** o limite de fd seja alto |
| Tabela completa com `ulimit -n` padrão (1024) | — | — | ❌ **`Too many open files`** |
| `glob` `notas_empenho_*.parquet` | — | 39 MB | ❌ quebra nos marcadores vazios |

**Conclusões (corrigem o que o plano supunha):**

1. **RAM não é o gargalo nas leituras.** O pico fica em ~250 MB.
2. O consumo de ~4 GB que matava o processo vinha do **`duckdb.memory_limit`
   padrão de 4 GB por conexão** ao *materializar tabelas* (o build do Postgres),
   não de "view materializada" nem de Pandas.
3. O limite real numa leitura completa é de **file descriptors** (`ulimit -n`),
   não de memória.
4. **`glob` é inviável** porque os arquivos marcadores vazios têm 0 colunas e
   fazem a leitura explodir — daí a lista explícita de arquivos.

**Consequência arquitetural:** a execução **não roda no processo do Streamlit**.
Vai para o Postgres + `pg_duckdb`, que faz spool em disco e trabalha em lotes.
O DuckDB direto fica apenas como backend de desenvolvimento.

## 3. Regra de governança dos marcadores vazios

Os Parquets com **0 colunas** (`*_001.parquet`, código de município 001) não são
lixo — são o **cache negativo intencional** do extrator: gravados de propósito
quando a API não retorna registros para um mês/município, e nas execuções
seguintes **evitam a requisição**. Regras registradas na constituição
(Restrições Adicionais) e em `docs/acesso-parquet-db.md`:

- Scripts de limpeza/deploy/migração **MUST NOT** apagar arquivos de `data/` com
  0 colunas.
- Rotinas de build/refresh **MUST** ignorá-los no agrupamento de tabelas, sem
  erro (o refresh do `parquet-db` já faz isso).
- Remover um marcador de propósito **força a reconsulta** na próxima extração.

> ⚠️ **Evento do dia:** durante a medição, os 42.337 Parquets (~1,31 GB) do
> checkout principal foram apagados por outra sessão (autorizada pelo usuário,
> para limpar `data/`). Não há Parquets no disco agora. Qualquer teste que
> dependa de dados reais precisa reextrair ou rodar no servidor.

## 4. Decisões do TCE AI Assistant (planejamento)

- **Acesso a dados:** Postgres + `pg_duckdb` **somente leitura**, com **instância
  própria** do container (para uma consulta pesada do agente não derrubar o SQL
  Explorer). É a mesma camada de acesso, isolada por recursos.
- **LLM:** **inferência local** (Ollama), sem enviar dados a APIs externas.
- **Primeiro entregável:** chat **Text-to-SQL somente leitura** ponta a ponta.
- **Ordem:** validar o LLM **antes** de construir a aplicação.

**Bloqueios (não resolvidos):**

- A máquina de desenvolvimento tem **~6,7 GiB de RAM (~1,7 GiB livres), sem GPU
  NVIDIA** → não comporta um modelo 7B+.
- O servidor **`172.16.0.200` não é alcançável daqui** (rota sai pelo roteador
  doméstico; portas 22/5433/11434 recusam; sem VPN). A validação do Ollama tem
  que rodar **do lado do servidor**.
- **O schema completo não está no repo:** o `endpoints.json` descreve só as
  colunas de texto/UI; as numéricas e de data (usadas nas agregações) só existem
  nos Parquets/Postgres. Precisa ser extraído do banco vivo.

**Ativo subestimado:** o `endpoints.json` já traz `colunas_texto` (descrições em
português = RAG pronto) e `relacionamentos` com **chaves de join explícitas**
(ex.: `liquidacoes ↔ notas_empenho` por `codigo_municipio, numero_empenho`) —
isso resolve de graça boa parte do *schema linking* do Text-to-SQL.

## 5. Guarda de armazenamento do Airflow

Task `verificar_armazenamento`, agora **primeira** da cadeia:

```
verificar_armazenamento → calcular_janela → executar_extracao → validar_parquets → atualizar_views
```

- `src/quality/armazenamento.py`: helper puro (sem Airflow). Usa
  `shutil.disk_usage`; mínimo padrão de `AIRFLOW_MIN_FREE_SPACE_GB` (**20 GB**);
  levanta `RuntimeError` quando crítico; `minimo_gb=0` desativa. Resolve a pasta
  `data/` (que pode não existir no 1º run) para o ancestral existente.
- Se o disco estiver crítico, a task falha **antes de qualquer download**, e o
  `on_failure_callback` existente dispara o alerta de Slack.
- Configurado no `docker-compose.yml` (anchor `airflow_env`, herdado por
  webserver e scheduler). Documentado no README. 5 testes.

## 6. Constituição (Spec Kit) — v1.2.0

Adotado o Spec Kit (`.specify/`, skills `.claude/skills/speckit-*`, também
`.opencode/`). A constituição foi emendada:

- **Princípio VI — Registro de Decisões nos Artefatos:** toda decisão (humana ou
  automatizada) **MUST** ser registrada no `.md` do seu domínio, **com o
  contexto da situação**, não só o resultado. → *Este documento é uma aplicação
  direta desse princípio.*
- Princípios anteriores: I. Schema como Fonte Única da Verdade; II. Validação em
  Duas Camadas (NON-NEGOTIABLE); III. Publicação de Dados Somente-Leitura;
  IV. Reprodutibilidade por Contêiner; V. Testes Obrigatórios e CI Verde.
- **Schema como fonte única:** novo `src/schemas/schema_campo.py` (dataclass
  `Campo`, exige exatamente um modo — posicional OU delimitado). Refactor em
  andamento: unificar validadores e suítes de qualidade sobre o mesmo schema.

## 7. Como os agentes se relacionaram (por que este documento existe)

Cada agente trabalhou **numa ilha**, com um prompt curto e investigação própria.
Não houve comunicação entre sessões. Consequências observadas:

- Fatos foram redescobertos várias vezes (o `endpoints.json` foi "descoberto"
  em pelo menos duas sessões; o `pg_duckdb` foi analisado em três).
- O diagnóstico medido (fd limit, marcadores vazios) ficou só no transcript do
  SQL Explorer; a regra de governança dos marcadores ficou só no transcript do
  checkout principal. Nenhum dos dois sabia do outro.
- Os dados de `data/` foram apagados por uma sessão enquanto outra os media.

Foi esse sintoma que motivou o Princípio VI. **Regra prática daqui pra frente:**
decisão relevante → commit no `.md` do domínio, não só na conversa.

## 8. Questões em aberto / próximos passos

- [ ] **Extrair o schema vivo** (numéricas e datas) do Postgres e versioná-lo —
      desbloqueia o Text-to-SQL.
- [ ] **Acessar `172.16.0.200`** para instalar/validar o Ollama e medir tokens/s
      e acerto em perguntas reais.
- [ ] **Formalizar o conjunto de perguntas de teste** com o SQL esperado.
- [ ] **Commitar o SQL Explorer foi feito**, mas falta: controlar consultas
      simultâneas, autenticação, e validar a implantação na rede local.
- [ ] **Concluir o refactor de schema único** (validadores + suítes de qualidade
      sobre `src/schemas/`).
- [ ] Reextrair dados em `data/` (foram apagados) ou trabalhar direto no
      servidor.
- [ ] Terminar a migração das abas: `qualidade` está comentada em `src/app.py`.

## 9. Mapa de arquivos por frente

**SQL Explorer**
- `src/modules/execucao_sql.py` — camada de execução trocável (postgres/duckdb).
- `src/modules/sql_explorer.py` — UI Streamlit (editor, catálogo, exemplos).
- `tests/test_execucao_sql.py` — 14 testes (rodam sem banco).
- `docs/tce_sql_explorer_planejamento.md` — plano.
- `docker-compose.yml` / `requirements.txt` — `PARQUET_DB_*`, `TCE_SQL_*`,
  `psycopg2-binary`.

**TCE AI Assistant**
- `docs/tce_ai_assistant_planejamento.md` — plano completo.

**Airflow**
- `src/quality/armazenamento.py`, `tests/test_armazenamento.py`,
  `src/orchestration/airflow/dags/tce_extracao_mensal.py`, `docker-compose.yml`,
  `README.md`.

**Spec Kit / governança**
- `.specify/memory/constitution.md`, `src/schemas/schema_campo.py`,
  `src/validators/delimitado.py`, `README.md`, `docs/acesso-parquet-db.md`.
