"""
Sistema declarativo de regras de negócio para validação TCE-CE.

Em vez de escrever funções Python linha-a-linha, você define regras como
dicionários de configuração que são interpretados em expressões Polars.

Exemplo de uso:

    REGRAS_994 = [
        {
            "nome": "quantidade_x_preco_igual_total",
            "tipo": "aritmetica",
            "formula": "quantidade_litros * preco_unitario",
            "target": "valor_total",
            "tolerancia": 0.05,
        },
        {
            "nome": "abastecimento_apos_inclusao",
            "tipo": "ordem_datas",
            "antes": "data_inclusao_veiculo",
            "depois": "data_abastecimento",
            "formato": "%Y%m%d",
        },
    ]

Para adicionar uma regra nova, basta adicionar um dicionário ao catálogo.
"""
from __future__ import annotations

import polars as pl


def construir_regra_aritmetica(regra: dict) -> pl.Expr:
    """
    Regra: formula (expressão matemática) deve bater com target (tolerância).
    
    Parâmetros:
        formula: string com expressão Polars (ex: "qtd * preco")
        target: nome da coluna que deve conter o resultado
        tolerancia: diferença máxima permitida (default 0.01)
    """
    formula = regra["formula"]
    target = regra["target"]
    tolerancia = regra.get("tolerancia", 0.01)
    nome = regra["nome"]
    
    # Avalia a fórmula (string -> expressão Polars)
    # Ex: "quantidade_litros * preco_unitario" -> pl.col("quantidade_litros") * pl.col("preco_unitario")
    expr_formula = eval(f"pl.{formula}")
    expr_target = pl.col(target)
    
    # Converte para float se necessário
    if isinstance(expr_formula, pl.Expr):
        calculado = expr_formula.cast(pl.Float64, strict=False)
    else:
        calculado = pl.lit(expr_formula).cast(pl.Float64)
    
    esperado = calculado.round(2)
    
    return (
        pl.when(
            calculado.is_not_null()
            & expr_target.is_not_null()
            & ((esperado - expr_target.cast(pl.Float64, strict=False)).abs() > tolerancia)
        )
        .then(pl.lit(f"Regra de negócio '{nome}': {formula} = {esperado}, mas {target} informado é diferente."))
        .otherwise(None)
        .alias(f"erro__regra__{nome}")
    )


def construir_regra_ordem_datas(regra: dict) -> pl.Expr:
    """
    Regra: campo 'antes' deve ser <= campo 'depois' (datas).
    
    Parâmetros:
        antes: nome do campo que deve vir primeiro
        depois: nome do campo que deve vir depois
        formato: formato das datas (ex: "%Y%m%d")
    """
    campo_antes = regra["antes"]
    campo_depois = regra["depois"]
    formato = regra.get("formato", "%Y%m%d")
    nome = regra["nome"]
    
    data_antes = pl.col(campo_antes).str.strip_chars().str.strptime(pl.Date, formato, strict=False)
    data_depois = pl.col(campo_depois).str.strip_chars().str.strptime(pl.Date, formato, strict=False)
    
    return (
        pl.when(
            data_antes.is_not_null()
            & data_depois.is_not_null()
            & (data_depois < data_antes)
        )
        .then(pl.lit(f"Regra de negócio '{nome}': '{campo_depois}' é anterior a '{campo_antes}'."))
        .otherwise(None)
        .alias(f"erro__regra__{nome}")
    )


def construir_regra_dominio(regra: dict) -> pl.Expr:
    """
    Regra: campo deve estar em uma lista de valores permitidos.
    
    Parâmetros:
        campo: nome do campo
        valores: lista de valores permitidos
    """
    campo = regra["campo"]
    valores = regra["valores"]
    nome = regra["nome"]
    
    col = pl.col(campo).str.strip_chars()
    vazio = col.is_null() | (col == "")
    
    valores_str = [str(v) for v in valores]
    
    return (
        pl.when((~vazio) & (~col.is_in(valores_str)))
        .then(pl.lit(f"Regra de negócio '{nome}': campo '{campo}' fora do domínio {valores}."))
        .otherwise(None)
        .alias(f"erro__regra__{nome}")
    )


def construir_regra_range(regra: dict) -> pl.Expr:
    """
    Regra: campo numérico deve estar em um intervalo [min, max].
    
    Parâmetros:
        campo: nome do campo
        min: valor mínimo (opcional)
        max: valor máximo (opcional)
    """
    campo = regra["campo"]
    min_val = regra.get("min")
    max_val = regra.get("max")
    nome = regra["nome"]
    
    col = pl.col(campo).str.strip_chars().cast(pl.Float64, strict=False)
    vazio = col.is_null()
    
    condicoes = []
    if min_val is not None:
        condicoes.append(col < min_val)
    if max_val is not None:
        condicoes.append(col > max_val)
    
    if not condicoes:
        raise ValueError(f"Regra '{nome}': pelo menos 'min' ou 'max' deve ser especificado.")
    
    condicao_final = condicoes[0]
    for cond in condicoes[1:]:
        condicao_final = condicao_final | cond
    
    return (
        pl.when((~vazio) & condicao_final)
        .then(pl.lit(f"Regra de negócio '{nome}': campo '{campo}' fora do intervalo [{min_val}, {max_val}]."))
        .otherwise(None)
        .alias(f"erro__regra__{nome}")
    )


def construir_regra_renavam(regra: dict) -> pl.Expr:
    """
    Regra específica: RENAVAM deve ter exatamente 11 dígitos numéricos.
    
    Parâmetros:
        campo: nome do campo (default "codigo_renavam")
    """
    campo = regra.get("campo", "codigo_renavam")
    nome = regra["nome"]
    
    col = pl.col(campo).str.strip_chars()
    vazio = col.is_null() | (col == "")
    
    return (
        pl.when((~vazio) & ((col.str.len_chars() != 11) | (~col.str.contains(r"^\d{11}$"))))
        .then(pl.lit(f"Regra de negócio '{nome}': RENAVAM deve ter exatamente 11 dígitos numéricos."))
        .otherwise(None)
        .alias(f"erro__regra__{nome}")
    )


# Mapeamento de tipos de regra para construtores
CONSTRUTORES_REGRAS = {
    "aritmetica": construir_regra_aritmetica,
    "ordem_datas": construir_regra_ordem_datas,
    "dominio": construir_regra_dominio,
    "range": construir_regra_range,
    "renavam": construir_regra_renavam,
}


def construir_expressoes_regras(regras: list[dict]) -> list[pl.Expr]:
    """
    Converte lista de regras declarativas em expressões Polars.
    
    Cada regra é um dicionário com:
        - nome: identificador da regra
        - tipo: tipo da regra (aritmetica, ordem_datas, dominio, range, renavam)
        - ...: parâmetros específicos do tipo
    """
    expressoes = []
    for regra in regras:
        tipo = regra["tipo"]
        if tipo not in CONSTRUTORES_REGRAS:
            raise ValueError(f"Tipo de regra desconhecido: '{tipo}'. Tipos disponíveis: {list(CONSTRUTORES_REGRAS.keys())}")
        
        construtor = CONSTRUTORES_REGRAS[tipo]
        expressoes.append(construtor(regra))
    
    return expressoes