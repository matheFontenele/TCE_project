<!--
Sync Impact Report
==================
Versão: 1.0.0 → 1.2.0
Tipo de bump: MINOR — princípio novo e orientação ampliada; nenhum princípio foi
  removido ou redefinido de forma incompatível.

Alterações nesta emenda (v1.2.0):
  + Princípio VI (Registro de Decisões nos Artefatos): toda decisão passa a
    MUST ser registrada no artefato `.md` do seu domínio, com o contexto da
    situação — não apenas o resultado.

Emenda anterior (v1.1.0):
  ~ Princípio II (Validação em Duas Camadas): o marcador vazio deixa de ser só
    "ignorado" e passa a ser explicitamente preservado como cache negativo.
  ~ Restrições Adicionais: novo item sobre a função dos Parquets vazios como
    cache negativo do extrator.

Histórico — v1.0.0 (adoção inicial): substituiu todos os placeholders do
  template. Princípios: I. Schema como Fonte Única da Verdade; II. Validação em
  Duas Camadas (NON-NEGOTIABLE); III. Publicação de Dados Somente-Leitura;
  IV. Reprodutibilidade por Contêiner; V. Testes Obrigatórios e CI Verde.

Seções removidas: nenhuma

TODOs / itens diferidos: nenhum. RATIFICATION_DATE fixada na data de adoção
  inicial (2026-10-11), pois não havia constituição anterior.

Nota: este relatório é material temporário de revisão e deve ser removido antes
  do commit da constituição emendada.
-->

# Constituição — Automação de Extração de Dados TCE-CE

## Core Principles

### I. Schema como Fonte Única da Verdade

Todo contrato de dado — nome, tipo, obrigatoriedade, valores permitidos, regras
de negócio — é declarado **uma única vez** em `src/schemas/` e consumido por
todos os pontos que precisam dele: validadores posicional/delimitado e as
Expectation Suites do Great Expectations (`src/quality/suites.py`).

- Regras de negócio **MUST NOT** ser duplicadas entre o motor de validação e as
  suítes de qualidade; ambas derivam do mesmo schema.
- Um campo novo ou alterado **MUST** ser refletido no schema antes de ser
  consumido por qualquer validador, exportador ou DAG.
- Regras declarativas (`src/validators/regras_declarativas.py`) são o formato
  preferido para novas checagens; código imperativo exige justificativa.

**Rationale:** derivar validação e qualidade do mesmo schema elimina a classe de
bug em que o validador e a checagem de dataset discordam sobre o mesmo campo.

### II. Validação em Duas Camadas (NON-NEGOTIABLE)

Nenhum dado é considerado válido por passar em apenas uma camada. Cada Parquet
**MUST** passar por:

1. **Validação linha-a-linha** — campos, tipos e regras de negócio
   (`src/validators/`).
2. **Qualidade de dataset** — expectativas de Great Expectations, ex.: % de
   nulos aceitável, contagem mínima de registros (`src/quality/`).

- Uma carga que falha em qualquer camada **MUST NOT** substituir a tabela
  publicada anteriormente; a versão boa anterior permanece servindo os leitores.
- Parquets marcadores vazios (0 colunas) **MUST** ser reconhecidos como
  intencionais e **preservados** — eles não são dado válido nem lixo: são o
  cache negativo que o extrator usa para não repetir requisições. Ignorá-los no
  consumo é correto; **apagá-los é proibido** (ver Restrições Adicionais).

**Rationale:** dados do TCE alimentam relatórios e PDFs oficiais; publicar uma
carga parcialmente válida propaga erro silencioso para o consumidor final. E os
marcadores vazios evitam requisições desnecessárias à API a cada atualização —
destruí-los degrada o extrator.

### III. Publicação de Dados Somente-Leitura

Consumidores acessam os Parquets **exclusivamente** por meio das tabelas
publicadas no Postgres + `pg_duckdb` (`parquet-db`), nunca por acesso direto aos
arquivos.

- Usuários de leitura **MUST** receber apenas `SELECT`; escrita e acesso a
  arquivo são negados.
- A atualização de tabelas **MUST** ser não-bloqueante: construir em schema de
  staging e trocar em transação curta, abortando a troca daquela tabela (sem
  derrubar o banco) se um leitor estiver segurando-a.
- O serviço **MUST** expor no máximo o necessário por rede e por IP de bind
  configurável (`PARQUET_DB_BIND_IP`).

**Rationale:** leitores concorrentes (DBeaver/DataGrip) não podem derrubar a
carga nem corromper dados; somente-leitura limita o raio de impacto.

### IV. Reprodutibilidade por Contêiner

Todo componente executa em Docker Compose, com o mesmo ambiente em
desenvolvimento e produção.

- Builds e execução **MUST** ocorrer via `docker compose`; não se assume Python
  do host.
- Configuração sensível **MUST** vir de `.env` (não versionado); segredos
  **MUST NOT** aparecer no código, no compose ou em logs.
- Variáveis obrigatórias **MUST** falhar cedo (`:?`) quando ausentes, com
  mensagem indicando onde defini-las.
- Orquestração de cargas recorrentes **MUST** passar pelo Airflow, não por
  execução manual não rastreada.

**Rationale:** o deploy é self-hosted e automático na `main`; ambiente divergente
é a principal fonte de falha "funciona na minha máquina".

### V. Testes Obrigatórios e CI Verde

Toda mudança em validadores, schemas, qualidade ou no script de refresh **MUST**
vir acompanhada de testes `pytest` correspondentes.

- O CI (`.github/workflows/tests.yml`) **MUST** estar verde antes de integrar
  mudanças à `main`.
- Testes que tocam o contexto do Great Expectations **MUST** usar diretório
  temporário isolado, para não contaminar estado entre execuções.
- O deploy de produção dispara a partir da `main`; portanto, merge sem CI verde
  é proibido.

**Rationale:** o pipeline é a única barreira automática entre uma regressão e os
relatórios oficiais em produção.

### VI. Registro de Decisões nos Artefatos

Toda decisão tomada por um agente — humano ou automatizado — durante
especificação, planejamento, implementação ou revisão **MUST** ser registrada no
artefato Markdown correspondente ao domínio da decisão, **com o contexto da
situação**, não apenas o resultado.

- O arquivo-alvo é o artefato vigente daquele assunto: `spec.md` para decisões de
  escopo/requisito; `plan.md`, `research.md`, `data-model.md` e `contracts/` para
  decisões técnicas; `tasks.md` para decisões de execução; `docs/` e `README.md`
  para decisões de operação; esta constituição para decisões de governança.
- O registro **MUST** conter: o que foi decidido, por quê, as alternativas
  consideradas e o que foi descartado. Resultado sem contexto não satisfaz a
  regra.
- Decisão que altera contrato de dado, fluxo ou operação **MUST** atualizar o
  artefato **no mesmo PR** da mudança de código — não em um commit posterior.
- Se o artefato correspondente não existir, o agente **MUST** criá-lo ou
  registrar a decisão no artefato mais próximo, apontando a lacuna.

**Rationale:** decisões não registradas se perdem entre sessões e entre agentes;
o próximo leitor refaz a análise já feita ou reverte uma escolha sem conhecer o
motivo. O contexto gravado no artefato é o que torna a decisão auditável e
reproduzível.

## Restrições Adicionais

- **Stack:** Python 3.12; extração com `requests`/`pandas`/`polars`; persistência
  em Parquet (`pyarrow`); qualidade com Great Expectations; interface Streamlit;
  orquestração Airflow; consulta via Postgres + `pg_duckdb`.
- **Parquets são a fonte de verdade** dos dados extraídos, sob `data/` (não
  versionada). O arquivo `tce.duckdb` contém apenas views e não é versionado.
- **Parquets vazios (0 colunas) são cache negativo intencional.** O extrator
  grava um marcador vazio quando a API não retorna registros para um mês/
  município e, nas execuções seguintes, **pula a requisição** ao encontrar esse
  arquivo (`src/modules/extraction.py`). Regras decorrentes:
  - Scripts de limpeza, deploy ou migração **MUST NOT** apagar arquivos de
    `data/` com 0 colunas; eles são estado, não sujeira.
  - Rotinas de build/refresh **MUST** ignorá-los no agrupamento de tabelas
    (sem erro), exatamente como o refresh do `parquet-db` já faz.
  - Ao remover um marcador de propósito, o efeito é re-requisitar aquele dado na
    próxima extração — só faça isso se quiser forçar a reconsulta.
- **Recursos:** o `duckdb.memory_limit` do `parquet-db` **MUST** permanecer
  limitado (padrão 1536 MB) para que o DuckDB transborde para disco em vez de
  causar OOM no Postgres; builds **MUST** processar Parquets em lotes.
- **`.env` e `data_quality/gx/uncommitted/`** não são versionados.

## Fluxo de Desenvolvimento e Qualidade

- **Commits** seguem o padrão convencional já usado no repositório
  (`feat:`, `fix:`, `chore:` …), com escopo quando aplicável.
- **Antes do push:** rodar `pytest` localmente; a suíte inclui testes de
  validadores, motor Polars, validador de Parquet e refresh do `parquet-db`.
- **Pull Request:** revisão **MUST** verificar conformidade com os princípios
  acima; complexidade extra **MUST** ser justificada no PR.
- **Deploy:** automático na `main` (self-hosted), via `docker compose up -d
  --build`. Mudanças de schema ou de contrato de dados **MUST** indicar o plano
  de refresh das tabelas publicadas.

## Governance

- Esta constituição **supersedes** outras práticas em conflito.
- **Emendas** exigem: descrição da mudança, aprovação do responsável pelo
  projeto e plano de migração quando afetar dados já publicados.
- **Versionamento semântico:** MAJOR para remoção/redefinição incompatível de
  princípio; MINOR para princípio novo ou orientação materialmente ampliada;
  PATCH para esclarecimentos e correções de redação.
- **Revisão de conformidade:** todo PR/revisão **MUST** verificar aderência a
  esta constituição; violações exigem justificativa explícita e registro.

**Version**: 1.2.0 | **Ratified**: 2026-10-11 | **Last Amended**: 2026-10-11
