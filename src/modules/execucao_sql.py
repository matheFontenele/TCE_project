"""Camada de execução de SQL do Explorer — a interface trocável, sem Streamlit.

O SQL digitado pelo usuário nunca roda no processo do Streamlit: quem executa é
o Postgres + pg_duckdb que já publica os parquets como tabelas comuns. Ler
Parquet direto no processo da aplicação não escala num host pequeno (o OOM
killer já derrubou o processo ao materializar tabelas grandes), então a
execução fica no banco, que faz spool em disco e trabalha em lotes.

Dois backends atrás da mesma interface:

- ``postgres`` (padrão): conecta com um usuário *somente leitura* e reforça a
  barreira no próprio banco (``default_transaction_read_only``), em vez de
  procurar palavras proibidas no texto do SQL.
- ``duckdb`` (desenvolvimento local): abre os parquets em memória com limite de
  RAM, sem depender de um Postgres no ar.

Nenhuma biblioteca pesada (psycopg2, duckdb, pandas, pyarrow) é importada no
topo deste módulo — só dentro das funções — para que os testes rodem sem banco
nem essas dependências instaladas.
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# Mesmo sufixo de partição usado por scripts/refresh_parquet_db.py:
# _2025_12_015.parquet ou _2025_015.parquet.
SUFIXO_ARQUIVO = re.compile(r"_\d{4}(_\d{2})?_\d{3}\.parquet$")

# Backend padrão: o Postgres com pg_duckdb. O DuckDB-direto é só para dev.
BACKEND_PADRAO = "postgres"
# Teto de linhas devolvidas por consulta, mesmo que a interface peça mais.
MAX_LINHAS_PADRAO = 2000
# Tempo máximo de uma consulta no Postgres, em milissegundos.
TIMEOUT_MS_PADRAO = 15000


@dataclass
class ResultadoSQL:
    """Resultado de uma consulta, independente de DataFrame ou de banco.

    ``linhas`` guarda tuplas na ordem de ``colunas``. ``truncado`` indica que a
    consulta produziu mais linhas do que o limite pedido (o excedente foi
    descartado). ``erro`` preenchido significa que nada foi executado com
    sucesso e ``linhas`` está vazio.
    """

    colunas: list[str] = field(default_factory=list)
    linhas: list[tuple] = field(default_factory=list)
    truncado: bool = False
    tempo_s: float = 0.0
    erro: Optional[str] = None

    @property
    def linhas_exibidas(self) -> int:
        return len(self.linhas)

    @property
    def vazio(self) -> bool:
        return not self.linhas

    def como_dataframe(self):
        """Converte para pandas.DataFrame (import tardio, só na interface)."""
        import pandas as pd

        return pd.DataFrame(self.linhas, columns=self.colunas)


# ---------------------------------------------------------------------------
# Configuração (variáveis de ambiente, com padrões seguros)
# ---------------------------------------------------------------------------
def backend_atual() -> str:
    return os.getenv("TCE_SQL_BACKEND", BACKEND_PADRAO).strip().lower()


def _limite_efetivo(limite_linhas: Optional[int]) -> int:
    """Aplica o teto TCE_SQL_MAX_ROWS ao limite pedido pela interface."""
    teto = _inteiro_env("TCE_SQL_MAX_ROWS", MAX_LINHAS_PADRAO, minimo=1)
    if limite_linhas is None:
        return teto
    return max(1, min(int(limite_linhas), teto))


def _inteiro_env(nome: str, padrao: int, minimo: int = 0) -> int:
    try:
        return max(minimo, int(os.getenv(nome, str(padrao))))
    except (TypeError, ValueError):
        return padrao


def teto_linhas() -> int:
    """Teto de linhas por consulta (TCE_SQL_MAX_ROWS), para a interface exibir."""
    return _limite_efetivo(None)


def _envolver_consulta(sql: str, limite: int) -> str:
    """Envolve o SQL num subselect com LIMIT para nunca materializar tudo.

    Aceita SELECT/WITH sem precisar de parser. O ``limite + 1`` é o truque para
    detectar truncamento: se voltarem ``limite + 1`` linhas, havia mais.
    Um ponto-e-vírgula no meio (tentativa de empilhar comandos) vira erro de
    sintaxe dentro do subselect, em vez de executar.
    """
    sql_limpo = sql.strip().rstrip(";").strip()
    if not sql_limpo:
        raise ValueError("Consulta vazia.")
    return f"SELECT * FROM (\n{sql_limpo}\n) AS t LIMIT {limite + 1}"


def _montar_resultado(cursor, limite: int, inicio: float) -> ResultadoSQL:
    """Lê até ``limite + 1`` linhas do cursor e marca truncamento."""
    colunas = [descricao[0] for descricao in (cursor.description or [])]
    linhas = cursor.fetchall()
    truncado = len(linhas) > limite
    if truncado:
        linhas = linhas[:limite]
    return ResultadoSQL(
        colunas=colunas,
        linhas=[tuple(linha) for linha in linhas],
        truncado=truncado,
        tempo_s=time.monotonic() - inicio,
    )


# ---------------------------------------------------------------------------
# Backend Postgres + pg_duckdb (produção)
# ---------------------------------------------------------------------------
def _conexao_postgres():
    import psycopg2

    usuario = os.getenv("PARQUET_DB_READ_USER")
    senha = os.getenv("PARQUET_DB_READ_PASSWORD")
    if not usuario or not senha:
        raise RuntimeError(
            "Defina PARQUET_DB_READ_USER e PARQUET_DB_READ_PASSWORD (usuário "
            "somente leitura criado por ./scripts/criar_usuario_leitura.sh)."
        )
    return psycopg2.connect(
        host=os.getenv("PARQUET_DB_HOST", "localhost"),
        port=int(os.getenv("PARQUET_DB_PORT", "5433")),
        dbname=os.getenv("PARQUET_DB_NAME", "tce"),
        user=usuario,
        password=senha,
        connect_timeout=_inteiro_env("TCE_SQL_CONNECT_TIMEOUT", 10, minimo=1),
    )


def _executar_postgres(sql: str, limite: int, inicio: float) -> ResultadoSQL:
    con = _conexao_postgres()
    try:
        # autocommit: cada comando é sua própria transação, e o
        # default_transaction_read_only passa a valer para todas elas.
        con.autocommit = True
        cur = con.cursor()
        timeout = _inteiro_env("TCE_SQL_TIMEOUT_MS", TIMEOUT_MS_PADRAO, minimo=1)
        # A barreira real é o banco, não uma blacklist de palavras: o usuário só
        # tem SELECT, e a sessão inteira é somente leitura.
        cur.execute("SET statement_timeout = %s", (timeout,))
        cur.execute("SET default_transaction_read_only = on")
        cur.execute("SET idle_in_transaction_session_timeout = %s", (timeout,))
        # Sem o segundo argumento, psycopg2 não interpola '%' — assim um
        # LIKE '%texto%' do usuário chega intacto ao servidor.
        cur.execute(_envolver_consulta(sql, limite))
        return _montar_resultado(cur, limite, inicio)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Backend DuckDB direto (desenvolvimento)
# ---------------------------------------------------------------------------
def agrupar_parquets(data_dir: Path) -> dict[str, list[str]]:
    """Agrupa parquets por tabela, ignorando marcadores vazios (0 colunas).

    Mesma lógica de scripts/refresh_parquet_db.py, mas devolvendo os caminhos
    locais (é o DuckDB deste processo que vai ler os arquivos).
    """
    import pyarrow.parquet as pq

    if not data_dir.is_dir():
        raise FileNotFoundError(f"Pasta de parquets não encontrada: {data_dir}")

    grupos: dict[str, list[str]] = {}
    for arquivo in sorted(data_dir.rglob("*.parquet")):
        try:
            if len(pq.read_schema(arquivo).names) == 0:
                continue  # marcador de mês sem dados
        except Exception:  # arquivo corrompido ou em escrita
            continue
        tabela = SUFIXO_ARQUIVO.sub("", arquivo.name)
        grupos.setdefault(tabela, []).append(str(arquivo))
    return grupos


def _literal_lista(arquivos: list[str]) -> str:
    """Monta um literal de lista SQL com aspas escapadas (caminhos locais)."""
    itens = ", ".join("'" + caminho.replace("'", "''") + "'" for caminho in arquivos)
    return f"[{itens}]"


def _registrar_views_duckdb(con) -> None:
    data_dir = Path(os.getenv("DATA_DIR", "data"))
    for tabela, arquivos in agrupar_parquets(data_dir).items():
        nome = '"' + tabela.replace('"', '""') + '"'
        con.execute(
            f"CREATE OR REPLACE VIEW {nome} AS SELECT * FROM "
            f"read_parquet({_literal_lista(arquivos)}, union_by_name := true)"
        )


def _executar_duckdb(sql: str, limite: int, inicio: float) -> ResultadoSQL:
    import duckdb

    con = duckdb.connect(database=":memory:")
    try:
        con.execute(f"SET memory_limit = '{_inteiro_env('TCE_SQL_MEMORY_MB', 1024, minimo=64)}MB'")
        con.execute(f"SET threads = {_inteiro_env('TCE_SQL_THREADS', 2, minimo=1)}")
        _registrar_views_duckdb(con)
        cur = con.execute(_envolver_consulta(sql, limite))
        return _montar_resultado(cur, limite, inicio)
    finally:
        con.close()


# ---------------------------------------------------------------------------
# Interface pública
# ---------------------------------------------------------------------------
def executar_sql(
    sql: str,
    limite_linhas: Optional[int] = None,
    backend: Optional[str] = None,
) -> ResultadoSQL:
    """Executa ``sql`` e devolve no máximo ``limite_linhas`` linhas.

    Nunca levanta exceção por causa do SQL do usuário: erros de sintaxe,
    permissão ou tempo esgotado voltam no campo ``erro`` do resultado.
    """
    inicio = time.monotonic()
    try:
        limite = _limite_efetivo(limite_linhas)
    except Exception as erro:
        return ResultadoSQL(erro=str(erro), tempo_s=time.monotonic() - inicio)

    backend = (backend or backend_atual()).strip().lower()
    try:
        if backend == "duckdb":
            return _executar_duckdb(sql, limite, inicio)
        if backend != "postgres":
            raise ValueError(f"Backend desconhecido: {backend!r} (use 'postgres' ou 'duckdb').")
        return _executar_postgres(sql, limite, inicio)
    except Exception as erro:
        # Erros do banco (psycopg2.Error) e falhas de conexão/import caem aqui.
        return ResultadoSQL(erro=str(erro).strip(), tempo_s=time.monotonic() - inicio)


def listar_catalogo(backend: Optional[str] = None) -> ResultadoSQL:
    """Lista tabela/coluna/tipo do schema public, para o catálogo da interface."""
    consulta = (
        "SELECT table_name, column_name, data_type "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' "
        "ORDER BY table_name, ordinal_position"
    )
    # O catálogo é pequeno e não depende do limite de linhas da tela, mas
    # respeita o teto global (TCE_SQL_MAX_ROWS) — por isso pede o máximo.
    return executar_sql(consulta, limite_linhas=None, backend=backend)
