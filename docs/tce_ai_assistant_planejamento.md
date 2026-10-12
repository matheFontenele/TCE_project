# Projeto 2 — TCE AI Assistant

## 1. Visão geral

Construir um assistente de IA interativo especializado nos dados públicos extraídos pelo pipeline do TCE-CE. O usuário poderá fazer perguntas em linguagem natural e receber respostas baseadas em consultas reais aos arquivos Parquet, com tabelas, gráficos, explicações e o SQL utilizado.

A proposta também serve como laboratório prático para estudar **IA Engineering**, modelos de linguagem (LLMs), inferência local, agentes, uso de ferramentas, Text-to-SQL, RAG e avaliação de respostas.

O OpenJarvis será avaliado como uma possível parte da solução e ambiente de estudo, sem assumir de antemão que todos os componentes precisam depender dele.

## 2. Estado atual e decisões registradas

### 2.1 O que o repositório já oferece

O pipeline do TCE-CE já entrega boa parte da base necessária, o que encurta bastante as primeiras fases do plano:

- **Catálogo de dados — `endpoints.json`:** para cada uma das 21 categorias, traz o endpoint da API, o prefixo/tabela correspondente nos Parquets, a frequência de atualização, parâmetros obrigatórios, campos de UI, **descrições em português das colunas** (`colunas_texto`) e o **grafo de relacionamentos** (`relacionamentos`) com as chaves de join entre tabelas (ex.: `liquidacoes` ↔ `notas_empenho` por `codigo_municipio, numero_empenho`).
- **Camada DuckDB sobre Parquet:** `scripts/criar_views.py` cria views locais; o serviço **Postgres + `pg_duckdb`** (ver `docs/acesso-parquet-db.md`) publica as tabelas pela rede, **somente leitura**, com um usuário `SELECT`-only por pessoa (`scripts/criar_usuario_leitura.sh`).
- **Interface Streamlit existente** (`src/app.py`, `src/modules/consultation.py`), com a UI dirigida pelo `endpoints.json`.
- **Cadastro de municípios** (`municipios.json`): código TCM ↔ IBGE ↔ geonames.

O `endpoints.json` cobre de graça parte do que o plano chamava de RAG (descrição de campos) e a etapa mais difícil do Text-to-SQL (descoberta de como juntar as tabelas).

### 2.2 Decisões tomadas

Registradas na discussão de 2026-10-11:

- **Acesso a dados:** o assistente consulta o **mesmo modelo Postgres + `pg_duckdb` somente leitura** que o SQL Explorer usa, em vez de ler os Parquets direto do sistema de arquivos. Assim a política de permissões passa a ser um ponto único (ver §8 e §12). O agente terá uma **instância própria** do `pg_duckdb` (container separado), para que uma consulta pesada do agente não degrade o serviço do SQL Explorer — os dois compartilhariam o mesmo host e o mesmo limite de memória do DuckDB.
- **Modelo de linguagem:** **inferência local** (Ollama), sem enviar dados a APIs externas. O modelo rodará no servidor `172.16.0.200`; o agente (Streamlit) apenas faz chamadas HTTP. A camada de LLM permanece substituível (ver Fase 4).
- **Primeiro entregável:** um **chat Text-to-SQL somente leitura** de ponta a ponta — pergunta em português → seleção do schema relevante → SQL proposto → validação → execução no Postgres read-only → exibição do SQL e da tabela de resultados.
- **Ordem de trabalho:** **validar o LLM antes de construir a aplicação** — confirmar o modelo, medir tokens/s e acerto em perguntas reais, e só então escrever o protótipo em cima disso.

### 2.3 Restrições conhecidas

- **Hardware de desenvolvimento:** a máquina onde o código está sendo escrito tem ~6,7 GiB de RAM (≈1,7 GiB livres) e **nenhuma GPU NVIDIA**. Não comporta modelos de 7B+ com folga; por isso o modelo local foi direcionado ao servidor.
- **Acesso ao servidor:** o `172.16.0.200` está na rede interna do TCE e **não é alcançável** da máquina de desenvolvimento (sem VPN configurada, portas testadas recusando conexão). A instalação do Ollama e os testes no servidor dependem de acesso local a ele — no momento indisponível.
- **Schema completo não está no repositório:** o `endpoints.json` descreve apenas as colunas de texto/UI. As colunas numéricas e de data (ex.: `valor_bruto_nota_liquidacao`, `data_liquidacao`, `municipio_referencia`), que são as usadas nas agregações, só existem nos Parquets no servidor. O schema vivo precisa ser extraído do Postgres.

## 3. Motivação

O projeto TCE já possui uma base de Engenharia de Dados: extração de APIs, transformação e armazenamento em Parquet, além de consultas com DuckDB. A próxima etapa é tornar esses dados mais acessíveis a usuários que não dominam SQL.

O assistente deve consultar os dados existentes no momento da pergunta. Não deve inventar valores nem depender apenas do conhecimento prévio do modelo.

## 4. Exemplo de uso

Pergunta ilustrativa:

> Quais municípios tiveram os maiores gastos nessa categoria? Compare os cinco primeiros e mostre a diferença percentual entre eles.

Fluxo esperado:

1. Identificar a categoria, o período e os filtros necessários.
2. Verificar se os dados necessários estão disponíveis.
3. Gerar uma consulta SQL candidata.
4. Validar a consulta e suas permissões.
5. Executar a consulta no Postgres + `pg_duckdb` somente leitura (que por sua vez lê os Parquets autorizados).
6. Calcular a comparação a partir dos resultados reais.
7. Exibir a resposta, a tabela, um gráfico e o SQL executado.

Qualquer resultado apresentado em exemplos ou protótipos deve ser identificado como ilustrativo até ser calculado sobre os dados reais.

## 5. Arquitetura proposta

```text
Usuário
  |
  v
Interface de chat (Streamlit)
  |
  v
Orquestração da aplicação
  |
  +--> LLM: interpreta a pergunta e propõe uma ação
  |
  +--> Catálogo de dados: tabelas, colunas, descrições e regras
  |
  +--> Documentação/RAG: manuais, dicionários e documentação das APIs
  |
  v
Validação e política de segurança
  |
  v
Ferramentas Python controladas
  |
  v
Postgres + pg_duckdb (somente leitura, instância própria)
  |
  v
DuckDB --> arquivos Parquet autorizados
  |
  v
Resultados reais
  |
  v
Resposta explicada + SQL + tabela/gráfico
```

Na decisão registrada em §2.2, o agente **não** lê os Parquets diretamente: ele envia o SQL validado ao Postgres + `pg_duckdb` somente leitura, que é a camada de acesso compartilhada com o SQL Explorer.

## 6. Papel do OpenJarvis

O OpenJarvis será investigado como uma possível plataforma para experimentar modelos, agentes, ferramentas, memória e execução de IA. Antes de integrá-lo, verificar a documentação e a versão atuais para confirmar quais interfaces e recursos são adequados ao projeto.

Com a decisão de usar **inferência local** (§2.2), o OpenJarvis deixa de ser pré-requisito: ele permanece como uma implementação opcional a avaliar na Fase 4, sem entrar no caminho crítico das fases anteriores.

A arquitetura deve manter as responsabilidades separadas:

- **Streamlit:** interface do usuário.
- **Camada de aplicação:** gerencia a conversa, contexto e fluxo de execução.
- **LLM:** interpreta a pergunta e propõe consultas ou chamadas de ferramentas.
- **Camada de ferramentas:** executa somente funções explicitamente autorizadas.
- **Postgres + `pg_duckdb` (read-only):** camada de acesso aos dados compartilhada com o SQL Explorer; é o limite real de permissões.
- **DuckDB:** realiza os cálculos e consultas analíticas.
- **Parquet:** fonte de dados extraídos pelo pipeline.
- **RAG (opcional):** recupera documentação e definições relevantes.
- **OpenJarvis (opcional):** infraestrutura a avaliar para modelos/agentes, sem acoplamento prematuro.

O projeto começa com **inferência local** (§2.2) e só avalia uma API externa se a política institucional permitir explicitamente. Antes de enviar qualquer conteúdo a um serviço externo, a regra de uso precisa estar definida.

## 7. Funcionalidades planejadas

### Chat em linguagem natural

- Perguntas sobre despesas, municípios, contratos e outras entidades disponíveis.
- Continuação de perguntas usando contexto da conversa.
- Solicitação de esclarecimento quando faltarem período, categoria ou filtros.
- Respostas em português claro.
- Indicação de limitações ou ausência de dados quando aplicável.

### Text-to-SQL

- Gerar consultas com base no catálogo de tabelas e colunas autorizado.
- Validar tabelas, colunas, filtros e tipos de operação antes da execução.
- Permitir inicialmente apenas consultas de leitura.
- Aplicar limites de duração, memória, concorrência e linhas retornadas.
- Exibir o SQL executado para facilitar auditoria e aprendizado.
- Permitir revisar ou corrigir consultas inválidas sem executar operações perigosas.

### Resultados e visualização

- Tabelas com resultados reais.
- Gráficos adequados ao tipo de pergunta.
- Totais, classificações e variações calculados pelo SQL ou por código determinístico.
- Filtros de período e município, quando houver os campos necessários.
- Indicação das tabelas/arquivos consultados, filtros utilizados e momento da consulta, quando possível.

### RAG e documentação

- Indexar manuais, dicionários de dados, descrições de campos e documentação dos endpoints.
- Recuperar trechos relevantes para explicar o significado das colunas e indicadores.
- Usar a documentação para orientar a geração de SQL.
- Distinguir informações documentais de resultados calculados a partir dos dados.

RAG é especialmente útil para responder “o que significa este campo?”; para “qual município teve o maior valor?”, a resposta quantitativa deve vir de uma consulta verificável.

## 8. Segurança e confiabilidade

- Não permitir que o modelo execute SQL sem validação.
- Não permitir acesso irrestrito ao sistema de arquivos, shell ou comandos do servidor.
- Usar funções Python com parâmetros e permissões bem definidos.
- Restringir consultas a tabelas/views e diretórios autorizados.
- Aplicar limites de recursos e de resultados.
- Tratar o conteúdo dos dados e documentos como entrada não confiável: texto armazenado não pode alterar as políticas do agente.
- Não incluir credenciais ou segredos no prompt.
- Registrar informações de auditoria sem armazenar desnecessariamente dados sensíveis.
- Exigir confirmação humana para operações com efeitos colaterais; a primeira versão deve ser somente leitura.
- Verificar as regras institucionais antes de utilizar APIs externas ou modelos que enviem dados para fora do servidor.

Com a decisão de acesso via `pg_duckdb` read-only (§2.2), a fronteira de segurança deixa de ser reimplementada do zero:

- **O Postgres read-only é o limite de acesso aos dados.** O usuário do agente recebe apenas `SELECT` nas tabelas de `public`; ele não consegue ler arquivos diretamente (o `pg_duckdb` rejeita com *"DuckDB execution is not allowed"*). Isso atende ao requisito de "sem acesso irrestrito ao sistema de arquivos".
- **A validação do SQL continua sendo necessária**, mas como segunda camada e para melhorar a experiência: garantir **um único statement**, **somente `SELECT`**, **tabelas dentro de uma allowlist** e **sem funções de arquivo/rede**. Uma biblioteca de parse de SQL (ex.: `sqlglot`) é o ponto natural para isso.
- **Limite obrigatório de linhas (`LIMIT`) e tempo de execução.** O `pg_duckdb` roda com `duckdb.memory_limit` baixo (padrão 1536 MB); uma consulta pesada do agente derrubaria o Postgres para *todos* os usuários, inclusive o SQL Explorer — motivo pelo qual o agente terá instância própria.

## 9. Plano de estudo e implementação

### Fase 1 — Fundamentos e escopo

- [ ] Escolher um conjunto de dados pequeno e bem conhecido.
- [~] Documentar tabelas, colunas, tipos e significados. *(parcial: o `endpoints.json` descreve as colunas de texto e os relacionamentos; o schema completo — numéricas e datas — ainda precisa ser extraído do Postgres.)*
- [ ] Definir perguntas que o sistema deverá responder.
- [ ] Estudar os fundamentos de LLMs, tokens, contexto, inferência e chamadas de ferramentas.

### Fase 2 — Protótipo Text-to-SQL

- [ ] Criar uma interface simples de chat.
- [ ] Fornecer ao modelo somente o esquema necessário.
- [ ] Gerar SQL para um conjunto limitado de perguntas.
- [ ] Validar e executar as consultas via Postgres + `pg_duckdb` somente leitura.
- [ ] Exibir o SQL e os resultados reais.
- [ ] Testar erros de sintaxe, colunas inexistentes e perguntas ambíguas.

### Fase 3 — Segurança e avaliação

- [ ] Implementar validação de consultas de leitura.
- [ ] Restringir recursos e resultados.
- [ ] Criar um conjunto de perguntas com respostas SQL esperadas.
- [ ] Medir validade do SQL, correção dos resultados, latência e consumo de recursos.
- [ ] Testar tentativas de acesso a arquivos ou operações não autorizadas.
- [ ] Distinguir “não há dados” de “não foi possível responder”.

### Fase 4 — Avaliar OpenJarvis e modelos locais

- [ ] Ler a documentação oficial atual do OpenJarvis.
- [ ] Identificar as interfaces de integração disponíveis.
- [ ] Executar um modelo compatível com o hardware disponível.
- [ ] Comparar um modelo local com uma opção via API, se permitido.
- [ ] Medir qualidade, latência, memória e custo.
- [ ] Manter a camada de IA substituível para evitar dependência desnecessária.

### Fase 5 — RAG e contexto

- [ ] Reunir dicionários de dados, manuais e documentação de APIs.
- [ ] Implementar recuperação de trechos relevantes.
- [ ] Incluir referências às fontes documentais usadas nas explicações.
- [ ] Testar se o RAG melhora a interpretação do esquema e reduz erros.

### Fase 6 — Evolução

- [ ] Adicionar gráficos e análises comparativas.
- [ ] Melhorar o histórico e o contexto da conversa.
- [ ] Criar mecanismos de feedback dos usuários.
- [ ] Implantar na rede local com autenticação e monitoramento.
- [ ] Avaliar ferramentas adicionais somente quando houver uma necessidade concreta.

### Próximo passo imediato

Antes de escrever a aplicação, **validar o LLM local** (decisão de §2.2), o que depende de acesso ao servidor `172.16.0.200`. Enquanto esse acesso não estiver disponível, o trabalho que não depende do servidor é: formalizar o conjunto de perguntas de teste e preparar a extração do schema vivo a partir do `endpoints.json`.

## 10. Métricas de sucesso

- Percentual de consultas SQL válidas.
- Percentual de perguntas cuja resposta coincide com a consulta de referência.
- Precisão de totais, rankings e percentuais.
- Taxa de perguntas que exigem esclarecimento.
- Latência média e percentis de duração.
- Consumo de RAM/CPU e, se aplicável, VRAM.
- Custo por pergunta quando houver APIs pagas.
- Taxa de bloqueio correto de operações não autorizadas.
- Capacidade de informar honestamente quando não há dados suficientes.

## 11. Stack inicial sugerida

- Python
- Streamlit
- **Postgres + `pg_duckdb`** (somente leitura, instância própria do agente) como camada de acesso aos dados
- DuckDB
- Parquet
- **Ollama** com um LLM local no servidor `172.16.0.200` (API externa apenas se a política institucional permitir; ver §2.2)
- `sqlglot` (ou equivalente) para validação/parse de SQL — camada única de statement, allowlist de tabelas e bloqueio de funções de arquivo/rede
- Bibliotecas de visualização compatíveis com Streamlit
- RAG opcional para documentação
- Git e Docker, conforme o ambiente de implantação
- OpenJarvis como opção a avaliar na Fase 4, fora do caminho crítico

## 12. Relação com o SQL Explorer

Os projetos devem compartilhar uma camada de acesso aos dados, mas não necessariamente a mesma interface ou política de uso.

O SQL Explorer atende usuários que sabem escrever consultas SQL. O TCE AI Assistant ajuda usuários a formular perguntas em linguagem natural. Ambos devem usar a mesma política central de permissões e limites, evitando implementar controles diferentes em cada interface.

Essa camada compartilhada **já existe** na prática: o serviço Postgres + `pg_duckdb` (`docs/acesso-parquet-db.md`) é somente leitura, tem usuário por pessoa e é reconstruído pelo Airflow a cada extração. O assistente passa a ser um segundo consumidor dessa mesma camada, com **instância própria** do `pg_duckdb` para isolar recursos (§2.2).

```text
                 +----------------------+
                 | Parquets do TCE-CE   |
                 +----------+-----------+
                            |
                 +----------v-----------+
                 | Camada segura de     |
                 | consulta DuckDB      |
                 +-----+-----------+----+
                       |           |
             +---------v--+   +----v-------------+
             | SQL        |   | TCE AI Assistant |
             | Explorer   |   | Text-to-SQL/RAG  |
             +------------+   +------------------+
```

## 13. Questões em aberto

- Qual é a RAM e o hardware disponível no servidor? *(em aberto — a máquina de desenvolvimento não comporta o modelo; ver §2.3.)*
- O projeto poderá utilizar APIs externas ou precisa operar completamente local? *(decidido: local, §2.2.)*
- Quais conjuntos de dados serão suportados primeiro?
- Quais perguntas os usuários mais precisam responder?
- Os arquivos contêm informações que exigem controles adicionais de acesso?
- Como serão atualizados os metadados quando o pipeline gerar novos Parquets?
- Quais usuários poderão acessar a aplicação?
- Qual versão e quais componentes do OpenJarvis são mais adequados à integração?
- Qual modelo local (tamanho/quantização) cabe no servidor e ainda acerta o Text-to-SQL?
- Como o servidor `172.16.0.200` será acessado para instalar o Ollama e rodar os testes?

## 14. Recursos para começar

- Repositório: https://github.com/open-jarvis/OpenJarvis
- Documentação: https://open-jarvis.github.io/OpenJarvis/

**Princípio de implementação:** a IA interpreta e explica; o DuckDB calcula; os Parquets fornecem os dados; a camada de segurança controla o que pode ser consultado. O resultado precisa ser verificável, limitado e rastreável.
