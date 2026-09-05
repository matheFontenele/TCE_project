"""
Catálogo de schemas dos endpoints de VEÍCULOS do TCE-CE.

Cada novo tipo de documento deve ser adicionado aqui seguindo o mesmo padrão:
- schema: lista de CampoSchema (posicional) ou CampoSchemaDelimitado (delimitado)
- regras_declarativas: lista de dicionários de regras (interpretados em Polars)

Para adicionar uma regra nova, basta adicionar um dicionário em regras_declarativas.
"""
from src.validators.posicional import CampoSchema
from src.validators.delimitado import CampoSchemaDelimitado

# ---------------------------------------------------------------------------
# Tipo 993 — Baixa na Destinação de Veículos (arquivo BVAAAAMM.VCL)
# ---------------------------------------------------------------------------

SCHEMA_993 = [
    CampoSchema(nome="tipo_documento", posicao_ini=1, tamanho=3, tipo="char", valor_fixo="993"),
    CampoSchema(nome="codigo_municipio", posicao_ini=4, tamanho=3, tipo="char"),
    CampoSchema(nome="exercicio_orcamento", posicao_ini=7, tamanho=6, tipo="numerico"),
    CampoSchema(nome="codigo_orgao", posicao_ini=13, tamanho=2, tipo="char"),
    CampoSchema(nome="codigo_unidade_orcamentaria", posicao_ini=15, tamanho=4, tipo="char"),
    CampoSchema(nome="data_inclusao", posicao_ini=19, tamanho=8, tipo="data", formato_data="%Y%m%d"),
    CampoSchema(nome="codigo_renavam", posicao_ini=27, tamanho=11, tipo="char"),
    CampoSchema(nome="data_baixa", posicao_ini=38, tamanho=8, tipo="data", formato_data="%Y%m%d"),
    CampoSchema(nome="motivo_baixa", posicao_ini=46, tamanho=1, tipo="numerico", valores_permitidos=[1, 2, 3, 4, 5, 6]),
    CampoSchema(nome="odometro", posicao_ini=47, tamanho=6, tipo="numerico"),
    CampoSchema(nome="data_referencia", posicao_ini=53, tamanho=6, tipo="numerico"),
]

REGRAS_DECLARATIVAS_993 = [
    {
        "nome": "data_baixa_apos_inclusao",
        "tipo": "ordem_datas",
        "antes": "data_inclusao",
        "depois": "data_baixa",
        "formato": "%Y%m%d",
    },
    {
        "nome": "renavam_valido",
        "tipo": "renavam",
        "campo": "codigo_renavam",
    },
]


# ---------------------------------------------------------------------------
# Tipo 994 — Consumo de Combustível de Veículos (arquivo CV AAAAMM.VCL)
# ---------------------------------------------------------------------------

SCHEMA_994 = [
    CampoSchemaDelimitado(nome="tipo_documento", indice=0, tipo="char", valor_fixo="994"),
    CampoSchemaDelimitado(nome="codigo_municipio", indice=1, tipo="char"),
    CampoSchemaDelimitado(nome="exercicio_orcamento", indice=2, tipo="numerico"),
    CampoSchemaDelimitado(nome="codigo_orgao", indice=3, tipo="char"),
    CampoSchemaDelimitado(nome="codigo_unidade_orcamentaria", indice=4, tipo="char"),
    CampoSchemaDelimitado(nome="data_inclusao_veiculo", indice=5, tipo="data", formato_data="%Y%m%d"),
    CampoSchemaDelimitado(nome="codigo_renavam", indice=6, tipo="char"),
    CampoSchemaDelimitado(nome="data_abastecimento", indice=7, tipo="data", formato_data="%Y%m%d"),
    CampoSchemaDelimitado(nome="odometro", indice=8, tipo="numerico"),
    CampoSchemaDelimitado(nome="tipo_combustivel", indice=9, tipo="char", valores_permitidos=["D", "G"]),
    CampoSchemaDelimitado(nome="quantidade_registro", indice=10, tipo="numerico"),
    CampoSchemaDelimitado(nome="quantidade_litros", indice=11, tipo="decimal"),
    CampoSchemaDelimitado(nome="preco_unitario", indice=12, tipo="decimal"),
    CampoSchemaDelimitado(nome="valor_total", indice=13, tipo="decimal"),
    CampoSchemaDelimitado(nome="flag_1", indice=14, tipo="numerico"),
    CampoSchemaDelimitado(nome="flag_2", indice=15, tipo="char"),
    CampoSchemaDelimitado(nome="cnpj_fornecedor", indice=16, tipo="char"),
    CampoSchemaDelimitado(nome="nome_fornecedor", indice=17, tipo="char"),
    CampoSchemaDelimitado(nome="documento_responsavel", indice=18, tipo="char"),
    CampoSchemaDelimitado(nome="nome_responsavel", indice=19, tipo="char"),
    CampoSchemaDelimitado(nome="data_referencia", indice=20, tipo="numerico"),
]

REGRAS_DECLARATIVAS_994 = [
    {
        "nome": "quantidade_x_preco_igual_total",
        "tipo": "aritmetica",
        "formula": "col('quantidade_litros').cast(pl.Float64, strict=False) * pl.col('preco_unitario').cast(pl.Float64, strict=False)",
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


# ---------------------------------------------------------------------------
# Catálogo geral
# ---------------------------------------------------------------------------

CATALOGO_SCHEMAS = {
    "993": {
        "nome": "Baixa na Destinação de Veículos",
        "arquivo_pattern": "BV{AAAAMM}.VCL",
        "formato": "posicional",
        "schema": SCHEMA_993,
        "regras_declarativas": REGRAS_DECLARATIVAS_993,
    },
    "994": {
        "nome": "Consumo de Combustível de Veículos",
        "arquivo_pattern": "CV{AAAAMM}.VCL",
        "formato": "delimitado",
        "schema": SCHEMA_994,
        "regras_declarativas": REGRAS_DECLARATIVAS_994,
    },
}


# ---------------------------------------------------------------------------
# Compatibilidade com código antigo (funções Python linha-a-linha)
# Mantidas para testes de regressão e fallback
# ---------------------------------------------------------------------------

def regra_data_baixa_apos_inclusao(campos: dict) -> list[str]:
    """A data de baixa nunca pode ser anterior à data de inclusão do veículo."""
    from datetime import datetime
    erros: list[str] = []
    try:
        d_inc = datetime.strptime(campos["data_inclusao"], "%Y%m%d")
        d_baixa = datetime.strptime(campos["data_baixa"], "%Y%m%d")
        if d_baixa < d_inc:
            erros.append(
                "Regra de negócio: 'data_baixa' é anterior a 'data_inclusao' "
                "(veículo não pode ser baixado antes de ser incluído)."
            )
    except (ValueError, KeyError):
        pass
    return erros


def regra_renavam_valido(campos: dict) -> list[str]:
    """RENAVAM deve ter exatamente 11 dígitos numéricos."""
    erros: list[str] = []
    renavam = campos.get("codigo_renavam", "")
    if renavam and not renavam.isdigit():
        erros.append(
            f"Regra de negócio: RENAVAM '{renavam}' contém caracteres não numéricos."
        )
    return erros


def regra_quantidade_x_preco_igual_total(campos: dict) -> list[str]:
    """quantidade_litros x preco_unitario deve bater com valor_total."""
    erros: list[str] = []
    try:
        qtd = float(campos["quantidade_litros"])
        preco = float(campos["preco_unitario"])
        total = float(campos["valor_total"])
        esperado = round(qtd * preco, 2)
        if abs(esperado - total) > 0.05:
            erros.append(
                f"Regra de negócio: quantidade_litros x preco_unitario = {esperado:.2f}, "
                f"mas valor_total informado é {total:.2f}."
            )
    except (ValueError, KeyError, TypeError):
        pass
    return erros


def regra_abastecimento_apos_inclusao(campos: dict) -> list[str]:
    """A data de abastecimento não deveria ser anterior à data de inclusão."""
    from datetime import datetime
    erros: list[str] = []
    try:
        d_inc = datetime.strptime(campos["data_inclusao_veiculo"], "%Y%m%d")
        d_abast = datetime.strptime(campos["data_abastecimento"], "%Y%m%d")
        if d_abast < d_inc:
            erros.append(
                "Regra de negócio: 'data_abastecimento' é anterior a "
                "'data_inclusao_veiculo' (abastecimento antes do veículo ser incluído)."
            )
    except (ValueError, KeyError):
        pass
    return erros


REGRAS_993 = [regra_data_baixa_apos_inclusao, regra_renavam_valido]
REGRAS_994 = [regra_quantidade_x_preco_igual_total, regra_abastecimento_apos_inclusao]

# Atualiza catálogo com regras antigas (para compatibilidade)
CATALOGO_SCHEMAS["993"]["regras"] = REGRAS_993
CATALOGO_SCHEMAS["994"]["regras"] = REGRAS_994


# ---------------------------------------------------------------------------
# Função auxiliar para construir regras Polars do 994 (compatibilidade)
# ---------------------------------------------------------------------------

def construir_regras_994_polars():
    """Versão vetorizada das regras 994 (mantida para testes)."""
    import polars as pl

    qtd = pl.col("quantidade_litros").str.strip_chars().cast(pl.Float64, strict=False)
    preco = pl.col("preco_unitario").str.strip_chars().cast(pl.Float64, strict=False)
    total = pl.col("valor_total").str.strip_chars().cast(pl.Float64, strict=False)
    esperado = (qtd * preco).round(2)

    expr_quantidade_preco = (
        pl.when(
            qtd.is_not_null()
            & preco.is_not_null()
            & total.is_not_null()
            & ((esperado - total).abs() > 0.05)
        )
        .then(pl.lit("Regra de negócio: quantidade_litros x preco_unitario não confere com valor_total."))
        .otherwise(None)
        .alias("erro__regra__quantidade_preco")
    )

    d_inc = pl.col("data_inclusao_veiculo").str.strip_chars().str.strptime(pl.Date, "%Y%m%d", strict=False)
    d_abast = pl.col("data_abastecimento").str.strip_chars().str.strptime(pl.Date, "%Y%m%d", strict=False)

    expr_datas = (
        pl.when(d_inc.is_not_null() & d_abast.is_not_null() & (d_abast < d_inc))
        .then(pl.lit("Regra de negócio: 'data_abastecimento' é anterior a 'data_inclusao_veiculo'."))
        .otherwise(None)
        .alias("erro__regra__datas")
    )

    return [expr_quantidade_preco, expr_datas]