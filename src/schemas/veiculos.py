"""
Catálogo de schemas dos endpoints de VEÍCULOS do TCE-CE.

Cada novo tipo de documento (ex: inclusão de veículo, atualização de odômetro)
deve ser adicionado aqui seguindo o mesmo padrão: uma lista de CampoSchema
descrevendo o layout posicional, uma lista de regras de negócio que dependem
de mais de um campo, e uma entrada no CATALOGO_SCHEMAS.
"""
from datetime import datetime

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
    CampoSchema(
        nome="motivo_baixa",
        posicao_ini=46,
        tamanho=1,
        tipo="numerico",
        valores_permitidos=[1, 2, 3, 4, 5, 6],
    ),
    CampoSchema(nome="odometro", posicao_ini=47, tamanho=6, tipo="numerico"),
    CampoSchema(nome="data_referencia", posicao_ini=53, tamanho=6, tipo="numerico"),
]


def regra_data_baixa_apos_inclusao(campos: dict) -> list[str]:
    """A data de baixa nunca pode ser anterior à data de inclusão do veículo."""
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
        pass  # formato de data inválido já é reportado na validação do campo individual
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


REGRAS_993 = [regra_data_baixa_apos_inclusao, regra_renavam_valido]


# ---------------------------------------------------------------------------
# Tipo 994 — Consumo de Combustível de Veículos (arquivo CV AAAAMM.VCL)
#
# Diferente do 993, este arquivo vem em formato DELIMITADO (CSV com aspas),
# não posicional. Mapeamento inferido a partir de um arquivo real (CV202603.VCL):
#
#   0  tipo_documento              "994"
#   1  codigo_municipio            "049"
#   2  exercicio_orcamento         202600
#   3  codigo_orgao                "16"
#   4  codigo_unidade_orcamentaria "01"
#   5  data_inclusao_veiculo       20240101   (AAAAMMDD)
#   6  codigo_renavam              "00592106985"
#   7  data_abastecimento          20260302   (AAAAMMDD)
#   8  odometro                    206219
#   9  tipo_combustivel            "D" (Diesel) / "G" (Gasolina)
#   10 quantidade_registro         1
#   11 quantidade_litros           179.00
#   12 preco_unitario              06.33
#   13 valor_total                 1133.07    (= quantidade_litros * preco_unitario)
#   14 flag_1                      1
#   15 flag_2                      "1"
#   16 cnpj_fornecedor             "52710736000157"
#   17 nome_fornecedor             "POSTO VIA OESTE PRIME 7 ..."
#   18 documento_responsavel       "87677881300"
#   19 nome_responsavel            "RIVALDO FERREIRA DE LIMA JUNIOR"
#   20 data_referencia             202603     (AAAAMM)
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
    CampoSchemaDelimitado(
        nome="tipo_combustivel", indice=9, tipo="char", valores_permitidos=["D", "G"]
    ),
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


def regra_quantidade_x_preco_igual_total(campos: dict) -> list[str]:
    """quantidade_litros x preco_unitario deve bater com valor_total (tolerância de arredondamento)."""
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
        pass  # erro de tipo já reportado na validação do campo individual
    return erros


def regra_abastecimento_apos_inclusao(campos: dict) -> list[str]:
    """A data de abastecimento não deveria ser anterior à data de inclusão do veículo."""
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


REGRAS_994 = [regra_quantidade_x_preco_igual_total, regra_abastecimento_apos_inclusao]


# ---------------------------------------------------------------------------
# Catálogo geral — adicione novos tipos de documento de veículos aqui
# ---------------------------------------------------------------------------

CATALOGO_SCHEMAS = {
    "993": {
        "nome": "Baixa na Destinação de Veículos",
        "arquivo_pattern": "BV{AAAAMM}.VCL",
        "formato": "posicional",
        "schema": SCHEMA_993,
        "regras": REGRAS_993,
    },
    "994": {
        "nome": "Consumo de Combustível de Veículos",
        "arquivo_pattern": "CV{AAAAMM}.VCL",
        "formato": "delimitado",
        "schema": SCHEMA_994,
        "regras": REGRAS_994,
    },
    # Outros arquivos identificados na sua estrutura de veículos (aguardando
    # especificação para mapear): BN, BO, BP, RB, RP (.PAT) e DV, VM (.VCL)
}