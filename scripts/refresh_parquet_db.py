#!/usr/bin/env python3
"""Reconstrói as tabelas materializadas de consulta a partir dos parquets.

O Postgres (com a extensão pg_duckdb) lê os parquets e grava tabelas comuns.
Os usuários de leitura só recebem SELECT nessas tabelas — nunca acesso aos
arquivos — então este script roda sempre com o usuário administrador.

A atualização é feita em duas etapas para não bloquear quem está consultando:

1. Constrói a tabela nova no schema de staging (os leitores seguem na antiga).
2. Move a tabela nova para o schema public numa transação curta (DROP + SET
   SCHEMA). Os grants concedidos à tabela viajam com ela na troca de schema.

O staging é um schema separado (`parquet_staging`) em vez de um sufixo `_new`
no nome: o limite de 63 caracteres do Postgres truncaria `_new` em nomes
longos (ex.: `ajuste_reavaliacao_patrimonial_desincorporacao_bem_municipio`
tem 60 caracteres), quebrando a troca. Uma troca abortada deixa a tabela no
staging, que é substituída na execução seguinte — sem acúmulo de lixo.

A mesma lógica de agrupamento de `scripts/criar_views.py` é reaproveitada:
arquivos marcadores (0 colunas) são ignorados e o agrupamento usa o sufixo
`_AAAA[_MM]_NNN.parquet`.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq
import psycopg2
from psycopg2 import sql

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [PARQUET-DB] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("refresh_parquet_db")

# Sufixo de arquivo de partição: _2025_12_015.parquet ou _2025_015.parquet
SUFIXO_ARQUIVO = re.compile(r"_\d{4}(_\d{2})?_\d{3}\.parquet$")

# DATA_DIR é onde este script enxerga os parquets (para descobri-los).
# SOURCE_DIR é o caminho equivalente dentro do container do Postgres, que é
# quem realmente lê os arquivos. Quando o script roda no host/Airflow, os dois
# diferem (ex.: /app/data aqui, /data no container).
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
SOURCE_DIR = os.getenv("PARQUET_DB_SOURCE_DIR", str(DATA_DIR))
SCHEMA_PUBLICO = "public"
SCHEMA_STAGING = "parquet_staging"
# Tempo máximo esperando o lock de uma tabela durante a troca atômica. Se um
# leitor estiver numa consulta longa, a troca é abortada e tentada na próxima
# execução, em vez de esperar indefinidamente.
LOCK_TIMEOUT = os.getenv("PARQUET_DB_LOCK_TIMEOUT", "5s")
# Limita quantos arquivos são lidos em cada INSERT. A consulta de schema usa
# somente metadados; os dados são materializados em lotes no backend DuckDB.
TAMANHO_LOTE = int(os.getenv("PARQUET_DB_BATCH_SIZE", "128"))


def conexao():
    return psycopg2.connect(
        host=os.getenv("PARQUET_DB_HOST", "localhost"),
        port=int(os.getenv("PARQUET_DB_PORT", "5433")),
        dbname=os.getenv("PARQUET_DB_NAME", "tce"),
        user=os.getenv("PARQUET_DB_ADMIN_USER", "tce_admin"),
        password=os.getenv("PARQUET_DB_ADMIN_PASSWORD"),
    )


def agrupar_arquivos(data_dir: Path) -> dict[str, list[str]]:
    """Agrupa parquets por tabela, ignorando marcadores vazios (0 colunas)."""
    if not data_dir.is_dir():
        raise FileNotFoundError(f"Pasta de parquets não encontrada: {data_dir}")

    grupos: dict[str, list[str]] = defaultdict(list)
    vazios = 0
    for arquivo in sorted(data_dir.rglob("*.parquet")):
        try:
            if len(pq.read_schema(arquivo).names) == 0:
                vazios += 1
                continue  # marcador de mês sem dados
        except Exception as erro:  # arquivo corrompido ou em escrita
            log.warning("Ignorando %s: %s", arquivo, erro)
            continue
        tabela = SUFIXO_ARQUIVO.sub("", arquivo.name)
        # Traduz para o caminho visto pelo container do Postgres.
        relativo = arquivo.relative_to(data_dir)
        grupos[tabela].append(str(Path(SOURCE_DIR) / relativo))

    log.info(
        "%d tabelas, %d arquivos válidos, %d marcadores vazios ignorados.",
        len(grupos),
        sum(len(v) for v in grupos.values()),
        vazios,
    )
    return dict(grupos)


def construir(cur, tabela: str, arquivos: list[str]) -> int:
    """Cria no staging em lotes, preservando o schema global de union_by_name."""
    if TAMANHO_LOTE <= 0:
        raise ValueError("PARQUET_DB_BATCH_SIZE deve ser maior que zero")

    destino = sql.Identifier(SCHEMA_STAGING, tabela)
    cur.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(destino))

    # A inferência global de schema é necessária: unir schemas lote a lote
    # mudaria ordem e tipos quando colunas/tipos surgem em arquivos posteriores.
    # LIMIT 0 cria a tabela usando exatamente o schema global do union_by_name,
    # sem materializar todas as linhas em um único operador.
    lista_completa = sql.Literal(arquivos)
    cur.execute(
        sql.SQL(
            "CREATE TABLE {} AS SELECT * FROM read_parquet({}, "
            "union_by_name := true) LIMIT 0"
        ).format(destino, lista_completa)
    )
    cur.execute(sql.SQL("SELECT * FROM {} LIMIT 0").format(destino))
    colunas = [
        descricao.name if hasattr(descricao, "name") else descricao[0]
        for descricao in cur.description
    ]
    if not colunas:
        raise RuntimeError(f"Nenhuma coluna encontrada para {tabela}")

    colunas_sql = sql.SQL(", ").join(sql.Identifier(coluna) for coluna in colunas)
    for inicio in range(0, len(arquivos), TAMANHO_LOTE):
        lote = arquivos[inicio : inicio + TAMANHO_LOTE]
        lista_lote = sql.Literal(lote)
        fonte = sql.SQL("read_parquet({}, union_by_name := true)").format(lista_lote)

        # LIMIT 0 retorna apenas metadados da fonte e cursor.description fornece
        # os nomes sem depender do comando DuckDB DESCRIBE. Colunas globais
        # ausentes recebem NULL; as comuns são selecionadas pelo nome para que
        # drift de ordem não altere valores.
        cur.execute(sql.SQL("SELECT * FROM {} LIMIT 0").format(fonte))
        presentes = {
            descricao.name if hasattr(descricao, "name") else descricao[0]
            for descricao in cur.description
        }
        expressao = sql.SQL(", ").join(
            sql.Identifier(coluna) if coluna in presentes else sql.SQL("NULL")
            for coluna in colunas
        )
        cur.execute(
            sql.SQL("INSERT INTO {} ({}) SELECT {} FROM {}").format(
                destino, colunas_sql, expressao, fonte
            )
        )

    cur.execute(sql.SQL("SELECT count(*) FROM {}").format(destino))
    return cur.fetchone()[0]


def trocar(cur, tabela: str) -> bool:
    """Move a tabela do staging para o schema público numa transação curta."""
    origem = sql.Identifier(SCHEMA_STAGING, tabela)
    destino = sql.Identifier(SCHEMA_PUBLICO, tabela)
    try:
        cur.execute("BEGIN")
        cur.execute(sql.SQL("SET LOCAL lock_timeout = %s"), (LOCK_TIMEOUT,))
        cur.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(destino))
        cur.execute(
            sql.SQL("ALTER TABLE {} SET SCHEMA {}").format(
                origem, sql.Identifier(SCHEMA_PUBLICO)
            )
        )
        cur.execute("COMMIT")
        return True
    except Exception as erro:
        cur.execute("ROLLBACK")
        log.error("Troca de %s abortada (tentará de novo na próxima): %s", tabela, erro)
        return False


def atualizar_tabela(tabela: str, arquivos: list[str]) -> int:
    """Constrói e troca uma tabela usando uma conexão própria.

    Cada conexão cria a sua própria instância DuckDB, e a memória só volta ao
    sistema quando o backend do Postgres encerra. Por isso abrimos e fechamos
    uma conexão por tabela: sem isso, a memória consumida pelas tabelas
    grandes se acumula até derrubar o Postgres (OOM) numa máquina pequena.
    """
    con = conexao()
    try:
        con.autocommit = True
        cur = con.cursor()
        linhas = construir(cur, tabela, arquivos)
        trocada = trocar(cur, tabela)
        if not trocada:
            raise RuntimeError(f"troca de {tabela} abortada (lock)")
        return linhas
    finally:
        con.close()  # encerra o backend e libera a memória do DuckDB


def preparar_schemas() -> None:
    con = conexao()
    try:
        con.autocommit = True
        con.cursor().execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(SCHEMA_STAGING))
        )
    finally:
        con.close()


def main() -> int:
    grupos = agrupar_arquivos(DATA_DIR)
    if not grupos:
        log.error("Nenhum parquet válido em %s.", DATA_DIR)
        return 1

    preparar_schemas()

    inicio = time.monotonic()
    trocadas = 0

    for tabela in sorted(grupos):
        arquivos = grupos[tabela]
        try:
            t0 = time.monotonic()
            linhas = atualizar_tabela(tabela, arquivos)
            trocadas += 1
            log.info(
                "%-58s %7d linhas  %5.1fs",
                tabela,
                linhas,
                time.monotonic() - t0,
            )
        except Exception as erro:
            log.exception("Falha ao atualizar %s: %s", tabela, erro)

    log.info(
        "Concluído: %d/%d tabelas atualizadas em %.1fs.",
        trocadas,
        len(grupos),
        time.monotonic() - inicio,
    )
    return 0 if trocadas == len(grupos) else 1


if __name__ == "__main__":
    sys.exit(main())
