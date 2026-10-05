"""Validação estrutural e amostral do datalake Parquet do TCE."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq


# Contratos mínimos dos três endpoints financeiros prioritários.
# Campos opcionais e tipos específicos serão ampliados após observar relatórios reais.
CONTRATOS_MINIMOS = {
    "notas_empenho": ("codigo_municipio", "exercicio_orcamento", "numero_empenho"),
    "notas_fiscais": (
        "codigo_municipio", "exercicio_orcamento", "numero_empenho", "numero_nota_fiscal"
    ),
    "notas_pagamentos": (
        "codigo_municipio", "exercicio_orcamento", "numero_empenho", "numero_nota_pagamento"
    ),
}

COLUNAS_TEXTO = {
    "codigo_municipio",
    "numero_empenho",
    "numero_nota_fiscal",
    "numero_nota_pagamento",
}

PADRAO_ARQUIVO = re.compile(
    r"^(?P<endpoint>.+)_(?P<ano>\d{4})(?:_(?P<mes>\d{2}))?_(?P<municipio>\d{3})$"
)


def _registrar_issue(relatorio, max_issues, nivel, codigo, caminho, mensagem):
    relatorio["total_issues"] += 1
    if len(relatorio["issues"]) < max_issues:
        relatorio["issues"].append({
            "nivel": nivel,
            "codigo": codigo,
            "arquivo": str(caminho),
            "mensagem": mensagem,
        })
    if nivel == "ERRO":
        relatorio["erros_estruturais"] += 1
    else:
        relatorio["avisos"] += 1


def _contar_mascara(mascara):
    total = pc.sum(pc.cast(pc.fill_null(mascara, False), pa.int64())).as_py()
    return int(total or 0)


def validar_pasta_parquets(
    diretorio,
    validar_linhas_desde: datetime | None = None,
    max_issues: int = 200,
) -> dict:
    """Inspeciona todos os footers e valida valores-chave nos arquivos recentes.

    A função é report-only: retorna os problemas encontrados, mas não altera os
    Parquets nem interrompe a atualização das views.
    """
    pasta = Path(diretorio)
    if not pasta.is_dir():
        raise FileNotFoundError(f"Pasta de Parquets não encontrada: {pasta}")

    relatorio = {
        "diretorio": str(pasta.resolve()),
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(),
        "validar_linhas_desde": (
            validar_linhas_desde.isoformat() if validar_linhas_desde else None
        ),
        "arquivos_encontrados": 0,
        "arquivos_validos": 0,
        "marcadores_sem_dados": 0,
        "erros_estruturais": 0,
        "avisos": 0,
        "linhas_verificadas": 0,
        "total_issues": 0,
        "issues_exibidas": 0,
        "issues_truncadas": 0,
        "issues": [],
    }

    arquivos = sorted(pasta.rglob("*.parquet"))
    relatorio["arquivos_encontrados"] = len(arquivos)

    for caminho in arquivos:
        try:
            parquet = pq.ParquetFile(caminho)
            schema = parquet.schema_arrow
            metadados = parquet.metadata
        except Exception as exc:
            _registrar_issue(
                relatorio, max_issues, "ERRO", "PARQUET_ILEGIVEL", caminho,
                f"Não foi possível abrir o Parquet: {type(exc).__name__}: {exc}",
            )
            continue

        if metadados.num_rows == 0:
            # O extrator cria marcadores sem dados; schemas vazios são intencionais.
            relatorio["marcadores_sem_dados"] += 1
            relatorio["arquivos_validos"] += 1
            continue

        if len(schema.names) == 0:
            _registrar_issue(
                relatorio, max_issues, "ERRO", "SCHEMA_VAZIO_COM_LINHAS", caminho,
                "O arquivo declara linhas, mas não possui colunas no schema.",
            )
            continue

        match = PADRAO_ARQUIVO.match(caminho.stem)
        if not match:
            _registrar_issue(
                relatorio, max_issues, "AVISO", "NOME_FORA_DO_PADRAO", caminho,
                "O nome do arquivo não segue endpoint_AAAA_MM_MMM (ou endpoint_AAAA_MMM).",
            )

        campos_chave = None
        erros_antes = relatorio["erros_estruturais"]
        if match:
            endpoint = match.group("endpoint")
            campos_chave = CONTRATOS_MINIMOS.get(endpoint)
            if campos_chave:
                ausentes = sorted(set(campos_chave) - set(schema.names))
                if ausentes:
                    _registrar_issue(
                        relatorio, max_issues, "ERRO", "COLUNAS_OBRIGATORIAS_AUSENTES", caminho,
                        "Colunas obrigatórias ausentes: " + ", ".join(ausentes),
                    )
                for campo in campos_chave:
                    if campo not in schema.names:
                        continue
                    tipo = schema.field(campo).type
                    tipo_textual_compativel = (
                        pa.types.is_string(tipo) or pa.types.is_large_string(tipo)
                    )
                    tipo_exercicio_compativel = (
                        pa.types.is_integer(tipo)
                        or pa.types.is_string(tipo)
                        or pa.types.is_large_string(tipo)
                    )
                    compativel = (
                        tipo_textual_compativel
                        if campo in COLUNAS_TEXTO
                        else tipo_exercicio_compativel
                    )
                    if not compativel:
                        esperado = "texto" if campo in COLUNAS_TEXTO else "inteiro ou texto"
                        _registrar_issue(
                            relatorio, max_issues, "ERRO", "TIPO_INCOMPATIVEL", caminho,
                            f"Coluna '{campo}' tem tipo {tipo}; esperado {esperado}.",
                        )

        if relatorio["erros_estruturais"] == erros_antes:
            relatorio["arquivos_validos"] += 1
        if not campos_chave or not match:
            continue

        mtime = datetime.fromtimestamp(caminho.stat().st_mtime, tz=timezone.utc)
        if validar_linhas_desde is not None:
            limite = validar_linhas_desde
            if limite.tzinfo is None:
                limite = limite.replace(tzinfo=timezone.utc)
            if mtime < limite:
                continue

        campos_verificacao = [campo for campo in campos_chave if campo in schema.names]
        if not campos_verificacao:
            continue
        for lote in parquet.iter_batches(batch_size=65536, columns=campos_verificacao):
            quantidade = lote.num_rows
            relatorio["linhas_verificadas"] += quantidade
            contadores = {}
            colunas_normalizadas = {}
            indice_coluna = {nome: indice for indice, nome in enumerate(lote.schema.names)}
            for campo in campos_verificacao:
                valores = pc.cast(lote.column(indice_coluna[campo]), pa.string(), safe=False)
                valores = pc.utf8_trim_whitespace(valores)
                valores = pc.replace_substring_regex(
                    valores, pattern=r"\.0$", replacement=""
                )
                colunas_normalizadas[campo] = valores
                vazios = pc.or_kleene(
                    valores.is_null(),
                    pc.equal(valores, ""),
                )
                contadores[campo] = _contar_mascara(vazios)

            municipio_arquivo = match.group("municipio")
            ano_arquivo = match.group("ano")
            municipio_normalizado = pc.utf8_lpad(
                colunas_normalizadas["codigo_municipio"], 3, padding="0"
            )
            municipio_preenchido = pc.invert(
                pc.or_kleene(
                    municipio_normalizado.is_null(),
                    pc.equal(municipio_normalizado, ""),
                )
            )
            municipio_diferente = pc.not_equal(municipio_normalizado, municipio_arquivo)
            municipios_divergentes = _contar_mascara(
                pc.and_kleene(municipio_preenchido, municipio_diferente)
            )

            exercicio = colunas_normalizadas["exercicio_orcamento"]
            exercicio_preenchido = pc.invert(
                pc.or_kleene(exercicio.is_null(), pc.equal(exercicio, ""))
            )
            exercicio_corresponde = pc.match_substring_regex(
                exercicio, pattern=f"^{ano_arquivo}"
            )
            exercicios_divergentes = _contar_mascara(
                pc.and_kleene(exercicio_preenchido, pc.invert(exercicio_corresponde))
            )

            for campo, quantidade_vazia in contadores.items():
                if quantidade_vazia:
                    _registrar_issue(
                        relatorio, max_issues, "AVISO", "CHAVE_VAZIA", caminho,
                        f"{quantidade_vazia} de {quantidade} linhas têm '{campo}' vazio/nulo.",
                    )
            if municipios_divergentes:
                _registrar_issue(
                    relatorio, max_issues, "AVISO", "MUNICIPIO_DIVERGENTE", caminho,
                    f"{municipios_divergentes} linhas têm código de município diferente do sufixo {municipio_arquivo}.",
                )
            if exercicios_divergentes:
                _registrar_issue(
                    relatorio, max_issues, "AVISO", "EXERCICIO_DIVERGENTE", caminho,
                    f"{exercicios_divergentes} linhas têm exercício diferente do ano {ano_arquivo} no nome do arquivo.",
                )

    relatorio["issues_exibidas"] = len(relatorio["issues"])
    relatorio["issues_truncadas"] = max(0, relatorio["total_issues"] - len(relatorio["issues"]))
    relatorio["sucesso_estrutural"] = relatorio["erros_estruturais"] == 0
    return relatorio


def salvar_relatorio(relatorio: dict, destino) -> Path:
    caminho = Path(destino)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_text(json.dumps(relatorio, ensure_ascii=False, indent=2), encoding="utf-8")
    return caminho
