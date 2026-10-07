"""Gera links auxiliares e registra tabelas Parquet no catálogo Hive do Trino."""

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
TRINO_DATA_DIR = Path(os.environ.get("TRINO_DATA_DIR", "/trino-data"))
TRINO_URL = os.environ.get("TRINO_URL", "http://localhost:8080/v1/statement")
TRINO_USER = os.environ.get("TRINO_USER", "bootstrap")
SCHEMA = "tce"


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def trino_type(data_type) -> str:
    import pyarrow as pa

    if pa.types.is_boolean(data_type):
        return "boolean"
    if pa.types.is_integer(data_type):
        return "bigint"
    if pa.types.is_floating(data_type):
        return "double"
    if pa.types.is_decimal(data_type):
        return f"decimal({data_type.precision}, {data_type.scale})"
    if pa.types.is_date(data_type):
        return "date"
    if pa.types.is_timestamp(data_type):
        return "timestamp"
    if pa.types.is_binary(data_type) or pa.types.is_large_binary(data_type):
        return "varbinary"
    # Para structs, listas, mapas e tipos não mapeados, preservar como texto
    # evita falhar no bootstrap e mantém o teste SQL acessível.
    return "varchar"


def execute(sql: str) -> None:
    request = urllib.request.Request(
        TRINO_URL,
        data=sql.encode("utf-8"),
        headers={"X-Trino-User": TRINO_USER, "Content-Type": "text/plain"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.load(response)

    while result.get("nextUri"):
        with urllib.request.urlopen(result["nextUri"], timeout=60) as response:
            result = json.load(response)
        if result.get("error"):
            raise RuntimeError(
                f"{result['error'].get('message', str(result['error']))}\nSQL: {sql}"
            )
    if result.get("error"):
        raise RuntimeError(f"{result['error'].get('message', str(result['error']))}\nSQL: {sql}")


def main() -> None:
    if not DATA_DIR.is_dir():
        raise FileNotFoundError(f"Pasta Parquet não encontrada: {DATA_DIR}")
    TRINO_DATA_DIR.mkdir(parents=True, exist_ok=True)

    groups = defaultdict(list)
    for parquet_file in sorted(DATA_DIR.rglob("*.parquet")):
        table_name = re.sub(r"_\d{4}(?:_\d{2})?_\d{3}$", "", parquet_file.stem)
        if table_name != parquet_file.stem and len(pq.read_schema(parquet_file).names) > 0:
            groups[table_name].append(parquet_file)
    if not groups:
        raise RuntimeError(f"Nenhum Parquet com sufixo esperado foi encontrado em {DATA_DIR}")

    # Limpa somente os links que este bootstrap mantém na área auxiliar.
    for item in TRINO_DATA_DIR.iterdir():
        if item.is_dir() and not item.is_symlink():
            for child in item.iterdir():
                if child.is_symlink():
                    child.unlink()
            try:
                item.rmdir()
            except OSError:
                pass

    execute(f"CREATE SCHEMA IF NOT EXISTS parquet.{quote_identifier(SCHEMA)}")
    for table_name, files in sorted(groups.items()):
        table_dir = TRINO_DATA_DIR / table_name
        table_dir.mkdir(parents=True, exist_ok=True)
        for parquet_file in files:
            link = table_dir / parquet_file.name
            if link.is_symlink() or link.exists():
                link.unlink()
            # O alvo precisa ser o mesmo caminho visto pelo Trino e pelo HMS.
            target_root = Path(os.environ.get("TRINO_SOURCE_DIR", "/data"))
            link.symlink_to(target_root / parquet_file.relative_to(DATA_DIR))

        schemas = [pq.read_schema(parquet_file) for parquet_file in files]
        schema = schemas[0] if len(schemas) == 1 else __import__("pyarrow").unify_schemas(
            schemas, promote_options="permissive"
        )
        columns = ", ".join(
            f"{quote_identifier(field.name)} {trino_type(field.type)}"
            for field in schema
        )
        location = "file:///trino-data/" + table_name
        execute(
            f"CREATE TABLE IF NOT EXISTS parquet.{quote_identifier(SCHEMA)}."
            f"{quote_identifier(table_name)} ({columns}) "
            f"WITH (format = 'PARQUET', external_location = '{location}')"
        )
        print(f"{table_name}: {len(files)} arquivo(s), {len(schema)} coluna(s)")

    print(f"Catálogo Trino atualizado: {len(groups)} tabela(s) no schema {SCHEMA}.")


if __name__ == "__main__":
    # Dá margem para o endpoint estar pronto após o healthcheck em máquinas lentas.
    time.sleep(1)
    main()
