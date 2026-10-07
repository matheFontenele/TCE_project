# Automação de Extração de Dados - TCE-CE

Este sistema realiza a extração automatizada de notas de empenho e notas fiscais da API de dados abertos do TCE-CE, consolidando os dados para análise.

## Como utilizar

Para iniciar o sistema pela primeira vez ou realizar uma nova extração:

1. Abra a pasta deste projeto.
2. Dê um **duplo clique** no arquivo `run.bat`.
3. Aguarde a tela preta (terminal) carregar as dependências automaticamente.
4. O navegador abrirá automaticamente em uma nova janela com o Painel de Controle.

## No Painel de Controle:
- **Extração:** Selecione o ano desejado no campo "Ano Base" e clique no botão **Executar Extração**.
- **Visualização:** Utilize as abas superiores para navegar entre os dados de "Notas de Empenho" e "Notas Fiscais".
- **Dados:** Os arquivos resultantes ficam salvos automaticamente na pasta `data/`.

## Consultas dos Parquets no DataGrip (DuckDB)

Os dados Parquet permanecem na pasta `data/`; o arquivo `tce.duckdb` contém apenas views que apontam para esses arquivos e é gerado localmente, não devendo ser enviado ao GitHub.

1. Instale as dependências do projeto, incluindo DuckDB e PyArrow.
2. Feche/desconecte no DataGrip a conexão com `tce.duckdb` antes de recriar as views.
3. Na raiz do projeto, execute `python scripts/criar_views.py`.
4. No DataGrip, conecte-se ao arquivo `tce.duckdb` e atualize o schema `main`.

O script encontra a pasta `data/` relativa à sua localização, ignora Parquets vazios usados como marcadores e cria uma view por tabela, combinando os arquivos com `union_by_name`. Para atualizar as views depois de baixar novos Parquets, basta repetir os passos 2–4.

## Consultas dos Parquets pela rede (Postgres + pg_duckdb)

Para que outras pessoas consultem os parquets por DBeaver/DataGrip sem acesso ao
servidor, o projeto sobe um container Postgres com a extensão `pg_duckdb` que
publica as tabelas via rede, somente leitura. Veja
[`docs/acesso-parquet-db.md`](docs/acesso-parquet-db.md).

## Suporte
Em caso de erro ou necessidade de atualização, contate o responsável pelo desenvolvimento do sistema.
