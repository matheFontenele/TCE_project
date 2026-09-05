"""
Testes automatizados da camada de Qualidade de Dados (src/quality/).

Cada teste usa um diretório temporário isolado (fixture `tmp_path` do pytest)
para o contexto do Great Expectations, garantindo que um teste não
contamine o estado de outro — importante porque o GE guarda suites,
checkpoints e Data Docs em disco, não só em memória.
"""
import pandas as pd
import pytest

from src.quality.dataframe_builder import registros_para_dataframe
from src.quality.runner import obter_contexto, rodar_validacao
from src.quality.suites import construir_suite_a_partir_do_schema
from src.validators.posicional import CampoSchema


@pytest.fixture
def schema_teste():
    """Schema simplificado, só para isolar o teste da camada de qualidade
    das regras reais do TCE (que já são testadas separadamente nos
    validadores posicional/delimitado)."""
    return [
        CampoSchema(nome="tipo_documento", posicao_ini=1, tamanho=3, tipo="char", valor_fixo="994"),
        CampoSchema(nome="codigo_renavam", posicao_ini=4, tamanho=11, tipo="char"),
        CampoSchema(
            nome="tipo_combustivel", posicao_ini=15, tamanho=1, tipo="char",
            valores_permitidos=["D", "G"],
        ),
    ]


@pytest.fixture
def df_valido():
    return pd.DataFrame(
        {
            "tipo_documento": ["994", "994"],
            "codigo_renavam": ["00592106985", "01304441170"],
            "tipo_combustivel": ["D", "G"],
        }
    )


@pytest.fixture
def df_com_campo_nulo():
    """Mesmo dataset, mas com um campo obrigatório vazio na segunda linha —
    deve ser detectado pela camada de qualidade."""
    return pd.DataFrame(
        {
            "tipo_documento": ["994", "994"],
            "codigo_renavam": ["00592106985", None],
            "tipo_combustivel": ["D", "G"],
        }
    )


@pytest.fixture
def df_com_valor_fora_da_lista():
    """Mesmo dataset, mas com tipo_combustivel fora da lista permitida (D/G)."""
    return pd.DataFrame(
        {
            "tipo_documento": ["994", "994"],
            "codigo_renavam": ["00592106985", "01304441170"],
            "tipo_combustivel": ["D", "X"],  # "X" não existe no mundo real
        }
    )


def test_construir_suite_gera_expectations_esperadas(schema_teste, tmp_path):
    """A suite deve conter: 1 checagem de contagem de linhas + 1 not-null
    por campo obrigatório + 1 value-set para o campo com valores_permitidos."""
    obter_contexto(caminho_projeto=str(tmp_path))  # ativa o contexto antes de montar a suite

    suite = construir_suite_a_partir_do_schema(
        nome_suite="suite_teste_estrutura", campos_schema=schema_teste, linhas_minimas=1
    )

    tipos = [exp.configuration.type for exp in suite.expectations]

    assert "expect_table_row_count_to_be_between" in tipos
    assert tipos.count("expect_column_values_to_not_be_null") == len(schema_teste)
    assert "expect_column_values_to_be_in_set" in tipos


def test_dataset_valido_passa_em_todas_expectativas(schema_teste, df_valido, tmp_path):
    obter_contexto(caminho_projeto=str(tmp_path))
    suite = construir_suite_a_partir_do_schema("suite_teste_valido", schema_teste, linhas_minimas=1)

    resultado = rodar_validacao(
        df_valido, suite, nome_dataset="teste_valido", caminho_projeto=str(tmp_path)
    )

    assert resultado["sucesso_geral"] is True
    assert all(d["sucesso"] for d in resultado["detalhes"])
    assert resultado["data_docs_path"] is not None


def test_campo_nulo_e_detectado(schema_teste, df_com_campo_nulo, tmp_path):
    obter_contexto(caminho_projeto=str(tmp_path))
    suite = construir_suite_a_partir_do_schema("suite_teste_nulo", schema_teste, linhas_minimas=1)

    resultado = rodar_validacao(
        df_com_campo_nulo, suite, nome_dataset="teste_nulo", caminho_projeto=str(tmp_path)
    )

    assert resultado["sucesso_geral"] is False

    falhas = [d for d in resultado["detalhes"] if not d["sucesso"]]
    assert any(
        d["expectativa"] == "expect_column_values_to_not_be_null" and d["coluna"] == "codigo_renavam"
        for d in falhas
    )


def test_valor_fora_da_lista_permitida_e_detectado(schema_teste, df_com_valor_fora_da_lista, tmp_path):
    obter_contexto(caminho_projeto=str(tmp_path))
    suite = construir_suite_a_partir_do_schema(
        "suite_teste_fora_lista", schema_teste, linhas_minimas=1
    )

    resultado = rodar_validacao(
        df_com_valor_fora_da_lista,
        suite,
        nome_dataset="teste_fora_lista",
        caminho_projeto=str(tmp_path),
    )

    assert resultado["sucesso_geral"] is False

    falhas = [d for d in resultado["detalhes"] if not d["sucesso"]]
    assert any(
        d["expectativa"] == "expect_column_values_to_be_in_set" and d["coluna"] == "tipo_combustivel"
        for d in falhas
    )


def test_arquivo_truncado_e_detectado_pela_contagem_minima(schema_teste, tmp_path):
    """Simula o cenário do Teste 2 do nosso plano manual: um arquivo com muito
    menos linhas do que o esperado deve falhar em expect_table_row_count_to_be_between,
    mesmo que todas as linhas presentes estejam individualmente válidas."""
    df_truncado = pd.DataFrame(
        {
            "tipo_documento": ["994"],
            "codigo_renavam": ["00592106985"],
            "tipo_combustivel": ["D"],
        }
    )

    obter_contexto(caminho_projeto=str(tmp_path))
    suite = construir_suite_a_partir_do_schema(
        "suite_teste_truncado", schema_teste, linhas_minimas=50  # esperamos pelo menos 50 linhas
    )

    resultado = rodar_validacao(
        df_truncado, suite, nome_dataset="teste_truncado", caminho_projeto=str(tmp_path)
    )

    assert resultado["sucesso_geral"] is False
    falhas = [d for d in resultado["detalhes"] if not d["sucesso"]]
    assert any(d["expectativa"] == "expect_table_row_count_to_be_between" for d in falhas)


def test_registros_para_dataframe_converte_string_vazia_em_nan():
    """Regressão do bug que encontramos: campo vazio ('') tem que virar NaN
    de verdade, senão expect_column_values_to_not_be_null não detecta nada."""

    class RegistroFake:
        def __init__(self, linha, campos, valido):
            self.linha = linha
            self.campos = campos
            self.valido = valido

    registros = [
        RegistroFake(1, {"campo_a": "valor", "campo_b": ""}, valido=False),
    ]

    df = registros_para_dataframe(registros)

    assert pd.isna(df.loc[0, "campo_b"])
    assert df.loc[0, "campo_a"] == "valor"