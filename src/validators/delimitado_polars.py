from __future__ import annotations

import io
from dataclasses import dataclass

import polars as pl

from src.validators.delimitado import CampoSchemaDelimitado


@dataclass
class ResultadoValidacaoPolars:
    total_registros: int
    linhas_invalidas: set
    df_erros: pl.DataFrame  # colunas: _linha, campo_check, erro

    @property
    def total_invalidos(self) -> int:
        return len(self.linhas_invalidas)


def _construir_expressoes_campo(campo: CampoSchemaDelimitado) -> list[pl.Expr]:
    """Gera uma expressão Polars por checagem (obrigatoriedade, tipo, valor fixo,
    valores permitidos) para um campo — cada uma vira uma coluna auxiliar que
    é None quando o campo passa, ou a mensagem de erro quando falha."""
    col_bruto = pl.col(campo.nome)
    col = col_bruto.str.strip_chars()
    # campo pode vir como null "de verdade" (,,) OU como string vazia ("")
    # dependendo de como o CSV foi escrito — as duas contam como "vazio"
    vazio = col_bruto.is_null() | (col == "")
    expressoes = []

    if campo.obrigatorio:
        expressoes.append(
            pl.when(vazio)
            .then(pl.lit(f"Campo '{campo.nome}': valor obrigatório está vazio."))
            .otherwise(None)
            .alias(f"erro__{campo.nome}__obrigatorio")
        )

    if campo.valor_fixo is not None:
        expressoes.append(
            pl.when((~vazio) & (col != campo.valor_fixo))
            .then(pl.lit(f"Campo '{campo.nome}': esperado valor fixo '{campo.valor_fixo}'."))
            .otherwise(None)
            .alias(f"erro__{campo.nome}__valor_fixo")
        )

    if campo.tipo == "numerico":
        expressoes.append(
            pl.when((~vazio) & (~col.str.contains(r"^\d+$")))
            .then(pl.lit(f"Campo '{campo.nome}': deveria ser numérico."))
            .otherwise(None)
            .alias(f"erro__{campo.nome}__tipo")
        )
    elif campo.tipo == "decimal":
        casted = col.cast(pl.Float64, strict=False)
        expressoes.append(
            pl.when((~vazio) & casted.is_null())
            .then(pl.lit(f"Campo '{campo.nome}': deveria ser decimal."))
            .otherwise(None)
            .alias(f"erro__{campo.nome}__tipo")
        )
    elif campo.tipo == "data" and campo.formato_data:
        parsed = col.str.strptime(pl.Date, campo.formato_data, strict=False)
        expressoes.append(
            pl.when((~vazio) & parsed.is_null())
            .then(pl.lit(f"Campo '{campo.nome}': data inválida (formato esperado {campo.formato_data})."))
            .otherwise(None)
            .alias(f"erro__{campo.nome}__data")
        )

    if campo.valores_permitidos:
        expressoes.append(
            pl.when((~vazio) & (~col.is_in(list(campo.valores_permitidos))))
            .then(pl.lit(f"Campo '{campo.nome}': fora da lista permitida {campo.valores_permitidos}."))
            .otherwise(None)
            .alias(f"erro__{campo.nome}__valores_permitidos")
        )

    return expressoes


def ler_csv_para_dataframe(conteudo: str, schema: list[CampoSchemaDelimitado]) -> pl.DataFrame:
    """Lê o conteúdo CSV para um Polars DataFrame, com os nomes de coluna
    posicionados conforme o índice declarado no schema, e tudo como string
    (a validação decide os tipos, igual ao motor original)."""
    colunas_ordenadas = [None] * (max(c.indice for c in schema) + 1)
    for campo in schema:
        colunas_ordenadas[campo.indice] = campo.nome

    df = pl.read_csv(
        io.StringIO(conteudo),
        has_header=False,
        new_columns=colunas_ordenadas,
        quote_char='"',
        infer_schema_length=0,
    )
    return df.with_row_index(name="_linha", offset=1)


def validar_dataframe(
    df: pl.DataFrame,
    schema: list[CampoSchemaDelimitado],
    expressoes_regras_negocio: list[pl.Expr] | None = None,
) -> ResultadoValidacaoPolars:
    expressoes = []
    for campo in schema:
        expressoes.extend(_construir_expressoes_campo(campo))
    if expressoes_regras_negocio:
        expressoes.extend(expressoes_regras_negocio)

    df_com_erros = df.with_columns(expressoes)
    colunas_erro = [c for c in df_com_erros.columns if c.startswith("erro__")]

    df_erros_longo = (
        df_com_erros.select(["_linha"] + colunas_erro)
        .unpivot(index="_linha", on=colunas_erro, variable_name="campo_check", value_name="erro")
        .filter(pl.col("erro").is_not_null())
    )

    linhas_invalidas = set(df_erros_longo["_linha"].to_list())

    return ResultadoValidacaoPolars(
        total_registros=df.height,
        linhas_invalidas=linhas_invalidas,
        df_erros=df_erros_longo,
    )


def validar_arquivo(
    conteudo: str,
    schema: list[CampoSchemaDelimitado],
    expressoes_regras_negocio: list[pl.Expr] | None = None,
) -> ResultadoValidacaoPolars:
    df = ler_csv_para_dataframe(conteudo, schema)
    return validar_dataframe(df, schema, expressoes_regras_negocio)
