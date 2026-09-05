"""
Converte os registros já parseados pelo motor de validação (posicional.py /
delimitado.py) em um pandas DataFrame — a "matéria-prima" que o Great
Expectations usa para rodar checagens de qualidade em nível de DATASET
(percentual de nulos, distribuição de valores, contagem de linhas), que são
complementares às checagens em nível de LINHA que o validador já faz.
"""
import pandas as pd


def registros_para_dataframe(registros) -> pd.DataFrame:
    """
    Recebe uma lista de RegistroValidado ou RegistroValidadoDelimitado
    (retornados por validar_arquivo) e devolve um DataFrame com uma coluna
    por campo do schema, mais uma coluna auxiliar '_linha' e '_valido'.

    Strings vazias ("") são convertidas para NaN: o parser guarda campo
    ausente como string vazia, mas o Great Expectations só reconhece nulo
    de verdade (None/NaN) em expect_column_values_to_not_be_null — sem essa
    conversão, um campo obrigatório vazio passaria pela checagem de qualidade
    sem ser detectado.
    """
    linhas = []
    for registro in registros:
        linha = dict(registro.campos)
        linha["_linha"] = registro.linha
        linha["_valido"] = registro.valido
        linhas.append(linha)

    df = pd.DataFrame(linhas)
    colunas_campos = [c for c in df.columns if c not in ("_linha", "_valido")]
    df[colunas_campos] = df[colunas_campos].replace("", pd.NA)
    return df