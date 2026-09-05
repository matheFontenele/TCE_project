"""
Motor de validação VETORIZADO (Polars) UNIFICADO para arquivos TCE-CE.

API única que funciona tanto para arquivos POSICIONAIS quanto DELIMITADOS.
Detecta o formato automaticamente e aplica validações vetorizadas.

Uso:
    from src.validators.motor_polars import validar_arquivo
    
    resultado = validar_arquivo(conteudo, tipo_documento="994")
"""
from __future__ import annotations

import io

import polars as pl

from src.schemas.veiculos import CATALOGO_SCHEMAS
from src.validators.posicional_polars import (
    ler_posicional_para_dataframe,
    validar_dataframe as validar_posicional,
)
from src.validators.delimitado_polars import (
    ler_csv_para_dataframe,
    validar_dataframe as validar_delimitado,
)
from src.validators.regras_declarativas import construir_expressoes_regras


def validar_arquivo(
    conteudo: str,
    tipo_documento: str,
) -> dict:
    """
    Valida arquivo TCE-CE usando motor Polars unificado.
    
    Args:
        conteudo: conteúdo do arquivo (string)
        tipo_documento: código do documento (ex: "993", "994")
    
    Returns:
        Dicionário com:
            - total_registros: int
            - total_invalidos: int
            - linhas_invalidas: set[int]
            - df_erros: pl.DataFrame (colunas: _linha, campo_check, erro)
    """
    if tipo_documento not in CATALOGO_SCHEMAS:
        raise ValueError(f"Tipo de documento desconhecido: '{tipo_documento}'")
    
    config = CATALOGO_SCHEMAS[tipo_documento]
    formato = config["formato"]
    schema = config["schema"]
    regras_declarativas = config.get("regras_declarativas", [])
    
    # Constrói expressões Polars para regras de negócio
    expressoes_regras = construir_expressoes_regras(regras_declarativas) if regras_declarativas else None
    
    if formato == "posicional":
        df = ler_posicional_para_dataframe(conteudo)
        resultado = validar_posicional(df, schema, expressoes_regras)
    elif formato == "delimitado":
        df = ler_csv_para_dataframe(conteudo, schema)
        resultado = validar_delimitado(df, schema, expressoes_regras)
    else:
        raise ValueError(f"Formato desconhecido: '{formato}'")
    
    return {
        "total_registros": resultado.total_registros,
        "total_invalidos": resultado.total_invalidos,
        "linhas_invalidas": resultado.linhas_invalidas,
        "df_erros": resultado.df_erros,
    }