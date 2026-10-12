"""Guarda de espaço em disco para o pipeline de extração do TCE.

Antes de baixar/refazer Parquets, o Airflow consulta o volume onde eles são
gravados. Se o espaço livre estiver abaixo do mínimo configurado, o pipeline
não inicia: enche o disco no meio da extração deixaria arquivos parciais e, em
hosts pequenos, poderia derrubar serviços vizinhos (ex.: o Postgres do
parquet-db).

O mínimo vem de ``AIRFLOW_MIN_FREE_SPACE_GB`` (padrão 20 GB). Use ``0`` para
desativar o guarda.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

VARIAVEL_AMBIENTE = "AIRFLOW_MIN_FREE_SPACE_GB"
MINIMO_PADRAO_GB = 20.0
BYTES_POR_GB = 1024 ** 3


def _ancestral_existente(caminho: Path) -> Path:
    """Sobe na árvore até achar um diretório existente.

    No primeiro run a pasta ``data/`` pode ainda não existir; medimos o espaço
    do volume do ancestral mais próximo (o bind mount) para não quebrar.
    """
    atual = Path(caminho)
    for candidato in (atual, *atual.parents):
        if candidato.exists():
            return candidato
    return Path("/")


def espaco_livre_bytes(caminho) -> int:
    """Espaço livre (bytes) no volume que contém ``caminho``."""
    return shutil.disk_usage(_ancestral_existente(Path(caminho))).free


def _minimo_gb(minimo_gb) -> float:
    if minimo_gb is None:
        minimo_gb = os.getenv(VARIAVEL_AMBIENTE, str(MINIMO_PADRAO_GB))
    return float(minimo_gb)


def verificar_espaco_livre(caminho, minimo_gb=None) -> dict:
    """Verifica se há espaço livre suficiente em ``caminho``.

    Retorna um resumo para log quando há espaço. Levanta ``RuntimeError`` (que
    falha a task e aciona o alerta de Slack) quando o volume está crítico.
    ``minimo_gb <= 0`` desativa a verificação.
    """
    caminho = Path(caminho)
    minimo = _minimo_gb(minimo_gb)
    uso = shutil.disk_usage(_ancestral_existente(caminho))
    livre_gb = uso.free / BYTES_POR_GB
    total_gb = uso.total / BYTES_POR_GB

    if minimo > 0 and livre_gb < minimo:
        raise RuntimeError(
            f"Armazenamento crítico em {caminho}: {livre_gb:.1f} GB livres, "
            f"mínimo exigido {minimo:.1f} GB. Extração não iniciada."
        )

    return {
        "livre_gb": livre_gb,
        "total_gb": total_gb,
        "minimo_gb": minimo,
        "caminho": str(caminho),
    }
