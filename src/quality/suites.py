"""
Construção de Expectation Suites do Great Expectations a partir dos MESMOS
schemas usados pelo motor de validação (src/schemas/veiculos.py).

Isso evita duplicar regra de negócio em dois lugares: o schema descreve o
campo uma vez, e alimenta tanto a validação linha-a-linha (posicional.py /
delimitado.py) quanto a checagem de qualidade em nível de dataset (GE).

Great Expectations aqui responde perguntas diferentes do validador manual:
- Validador manual: "esta linha específica está correta?"
- Great Expectations: "o dataset como um todo se comporta como esperado?"
  (ex: % de nulos aceitável, quantidade mínima de registros, distribuição
  de valores dentro do esperado)
"""
import great_expectations as gx


def construir_suite_a_partir_do_schema(nome_suite: str, campos_schema, linhas_minimas: int = 1):
    """
    Constrói uma ExpectationSuite genérica a partir de uma lista de campos
    (CampoSchema ou CampoSchemaDelimitado), derivando automaticamente:
      - not-null para campos obrigatórios
      - conjunto de valores permitidos, quando definido no schema
      - quantidade mínima de linhas no dataset
    """
    suite = gx.ExpectationSuite(name=nome_suite)

    suite.add_expectation(
        gx.expectations.ExpectTableRowCountToBeBetween(min_value=linhas_minimas)
    )

    for campo in campos_schema:
        if getattr(campo, "obrigatorio", True):
            suite.add_expectation(
                gx.expectations.ExpectColumnValuesToNotBeNull(column=campo.nome)
            )
        valores_permitidos = getattr(campo, "valores_permitidos", None)
        if valores_permitidos:
            suite.add_expectation(
                gx.expectations.ExpectColumnValuesToBeInSet(
                    column=campo.nome, value_set=list(valores_permitidos)
                )
            )

    return suite


def adicionar_expectations_extras(suite, expectations: list):
    """Permite complementar a suite gerada automaticamente com regras específicas do tipo de documento."""
    for exp in expectations:
        suite.add_expectation(exp)
    return suite