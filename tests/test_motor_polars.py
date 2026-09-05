"""
Testes do motor Polars unificado.

Valida que o motor Polars produz resultados idênticos aos motores antigos
para garantir que a migração não introduziu bugs.
"""
import random

import pytest

from src.schemas.veiculos import CATALOGO_SCHEMAS, construir_regras_994_polars
from src.validators.delimitado import validar_arquivo as validar_arquivo_antigo
from src.validators.motor_polars import validar_arquivo as validar_arquivo_polars


def _gerar_linha_994(forcar_erro: str | None = None) -> str:
    orgaos = ["16", "07", "13", "09", "11", "14"]
    renavam = f"{random.randint(0, 99999999999):011d}"
    data_abastecimento = f"2026{random.randint(1, 3):02d}{random.randint(1, 28):02d}"
    odometro = f"{random.randint(1000, 900000):06d}"
    qtd = round(random.uniform(5, 200), 2)
    preco = round(random.uniform(5.5, 7.5), 2)
    total = round(qtd * preco, 2)
    tipo_combustivel = random.choice(["D", "G"])

    if forcar_erro == "combustivel_invalido":
        tipo_combustivel = "X"
    elif forcar_erro == "renavam_vazio":
        renavam = ""
    elif forcar_erro == "data_vazia":
        data_abastecimento = ""
    elif forcar_erro == "total_nao_bate":
        total = round(total + 50, 2)

    return (
        f'"994","049",202600,"{random.choice(orgaos)}","01",20240101,"{renavam}",'
        f"{data_abastecimento},{odometro},\"{tipo_combustivel}\",1,{qtd:.2f},{preco:.2f},{total:.2f},"
        f'1,"1","52710736000157","POSTO EXEMPLO","87677881300","RESPONSAVEL EXEMPLO",202603'
    )


@pytest.mark.parametrize(
    "tipo_erro",
    [None, "combustivel_invalido", "renavam_vazio", "data_vazia", "total_nao_bate"],
)
def test_motor_polars_concorda_com_validador_original(tipo_erro):
    """Motor Polars deve produzir mesmo resultado que validador antigo."""
    linha = _gerar_linha_994(forcar_erro=tipo_erro)

    # Motor antigo
    schema = CATALOGO_SCHEMAS["994"]["schema"]
    regras = CATALOGO_SCHEMAS["994"]["regras"]
    registros = validar_arquivo_antigo(linha, schema, regras)
    valido_antigo = registros[0].valido

    # Motor Polars unificado
    resultado = validar_arquivo_polars(linha, "994")
    valido_novo = resultado["total_invalidos"] == 0

    assert valido_antigo == valido_novo, (
        f"Divergência no caso '{tipo_erro}': "
        f"antigo={'válido' if valido_antigo else 'inválido'}, "
        f"polars={'válido' if valido_novo else 'inválido'}"
    )


def test_motor_polars_em_lote_misto():
    """Conjunto de linhas inválidas deve ser idêntico entre os dois motores."""
    random.seed(123)
    
    tipos_erro = [None, None, None, "combustivel_invalido", "renavam_vazio", "data_vazia", "total_nao_bate"]
    linhas = [_gerar_linha_994(forcar_erro=random.choice(tipos_erro)) for _ in range(500)]
    conteudo = "\n".join(linhas)

    # Motor antigo
    schema = CATALOGO_SCHEMAS["994"]["schema"]
    regras = CATALOGO_SCHEMAS["994"]["regras"]
    registros = validar_arquivo_antigo(conteudo, schema, regras)
    invalidas_antigo = {r.linha for r in registros if not r.valido}

    # Motor Polars
    resultado = validar_arquivo_polars(conteudo, "994")
    invalidas_novo = resultado["linhas_invalidas"]

    assert invalidas_antigo == invalidas_novo


def test_motor_polars_e_mais_rapido():
    """Motor Polars deve ser mais rápido que o motor antigo."""
    import time

    random.seed(99)
    linhas = [_gerar_linha_994(forcar_erro=None) for _ in range(5_000)]
    conteudo = "\n".join(linhas)

    # Motor antigo
    schema = CATALOGO_SCHEMAS["994"]["schema"]
    regras = CATALOGO_SCHEMAS["994"]["regras"]
    inicio = time.perf_counter()
    validar_arquivo_antigo(conteudo, schema, regras)
    tempo_antigo = time.perf_counter() - inicio

    # Motor Polars
    inicio = time.perf_counter()
    validar_arquivo_polars(conteudo, "994")
    tempo_novo = time.perf_counter() - inicio

    assert tempo_novo < tempo_antigo, (
        f"Motor Polars ({tempo_novo:.2f}s) não foi mais rápido que "
        f"motor antigo ({tempo_antigo:.2f}s)"
    )