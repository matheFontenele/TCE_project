"""
Motor de validação VETORIZADO (Polars) para arquivos POSICIONAIS do TCE-CE.

Lê o arquivo inteiro como uma coluna de strings e extrai campos via slicing
vetorizado — tudo em Rust (Polars), sem loops Python.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import polars as pl

from src.validators.posicional import CampoSchema


@dataclass
class ResultadoValidacaoPolars:
    total_registros: int
    linhas_invalidas: set
    df_erros: pl.DataFrame  # colunas: _linha, campo_check, erro

    @property
    def total_invalidos(self) -> int:
        return len(self.linhas_invalidas)


def _construir_expressoes_campo(campo: CampoSchema) -> list[pl.Expr]:
    """Extrai o campo via slicing e valida (obrigatoriedade, tipo, valor fixo, etc)."""
    # Extrai o campo da linha inteira via substring vetorizada
    # posicao_ini é 1-based no schema, então subtraímos 1
    col_bruto = pl.col("_linha").str.slice(
        campo.posicao_ini - 1, campo.tamanho
    ).str.strip_chars()
    
    vazio = col_bruto.is_null() | (col_bruto == "")
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
            pl.when((~vazio) & (col_bruto != str(campo.valor_fixo)))
            .then(pl.lit(f"Campo '{campo.nome}': esperado valor fixo '{campo.valor_fixo}'."))
            .otherwise(None)
            .alias(f"erro__{campo.nome}__valor_fixo")
        )

    if campo.tipo == "numerico":
        expressoes.append(
            pl.when((~vazio) & (~col_bruto.str.contains(r"^\d+$")))
            .then(pl.lit(f"Campo '{campo.nome}': deveria ser numérico."))
            .otherwise(None)
            .alias(f"erro__{campo.nome}__tipo")
        )
    elif campo.tipo == "decimal":
        casted = col_bruto.cast(pl.Float64, strict=False)
        expressoes.append(
            pl.when((~vazio) & casted.is_null())
            .then(pl.lit(f"Campo '{campo.nome}': deveria ser decimal."))
            .otherwise(None)
            .alias(f"erro__{campo.nome}__tipo")
        )
    elif campo.tipo == "data" and campo.formato_data:
        parsed = col_bruto.str.strptime(pl.Date, campo.formato_data, strict=False)
        expressoes.append(
            pl.when((~vazio) & parsed.is_null())
            .then(pl.lit(f"Campo '{campo.nome}': data inválida (formato esperado {campo.formato_data})."))
            .otherwise(None)
            .alias(f"erro__{campo.nome}__data")
        )

    if campo.valores_permitidos:
        valores_str = [str(v) for v in campo.valores_permitidos]
        expressoes.append(
            pl.when((~vazio) & (~col_bruto.is_in(valores_str)))
            .then(pl.lit(f"Campo '{campo.nome}': fora da lista permitida {campo.valores_permitidos}."))
            .otherwise(None)
            .alias(f"erro__{campo.nome}__valores_permitidos")
        )

    # Cria coluna com o valor extraído (para uso em regras de negócio)
    expressoes.append(col_bruto.alias(campo.nome))

    return expressoes


def ler_posicional_para_dataframe(conteudo: str) -> pl.DataFrame:
    """Lê arquivo posicional: cada linha vira uma string na coluna '_linha'."""
    df = pl.read_csv(
        io.StringIO(conteudo),
        has_header=False,
        new_columns=["_linha"],
        separator="\x00",  # separador que nunca aparece, força uma coluna só
        quote_char=None,
        infer_schema_length=0,
    )
    return df.with_row_index(name="_linha_num", offset=1)


def validar_dataframe(
    df: pl.DataFrame,
    schema: list[CampoSchema],
    expressoes_regras_negocio: list[pl.Expr] | None = None,
) -> ResultadoValidacaoPolars:
    """Valida campos e aplica regras de negócio."""
    expressoes = []
    for campo in schema:
        expressoes.extend(_construir_expressoes_campo(campo))
    
    df_com_campos = df.with_columns(expressoes)
    
    if expressoes_regras_negocio:
        df_com_campos = df_com_campos.with_columns(expressoes_regras_negocio)

    colunas_erro = [c for c in df_com_campos.columns if c.startswith("erro__")]

    df_erros_longo = (
        df_com_campos.select(["_linha_num"] + colunas_erro)
        .unpivot(index="_linha_num", on=colunas_erro, variable_name="campo_check", value_name="erro")
        .filter(pl.col("erro").is_not_null())
    )

    linhas_invalidas = set(df_erros_longo["_linha_num"].to_list())

    return ResultadoValidacaoPolars(
        total_registros=df.height,
        linhas_invalidas=linhas_invalidas,
        df_erros=df_erros_longo,
    )


def validar_arquivo(
    conteudo: str,
    schema: list[CampoSchema],
    expressoes_regras_negocio: list[pl.Expr] | None = None,
) -> ResultadoValidacaoPolars:
    df = ler_posicional_para_dataframe(conteudo)
    return validar_dataframe(df, schema, expressoes_regras_negocio)