"""
Executa uma ExpectationSuite do Great Expectations contra um DataFrame e
gera/atualiza o Data Docs (site HTML navegável com o histórico de validações).

Escrito para ser seguro de rodar múltiplas vezes (idempotente), já que o
Streamlit reexecuta o script inteiro a cada interação do usuário.
"""
import os

import great_expectations as gx
import pandas as pd

# Contexto do GE fica na raiz do projeto, em uma pasta própria (não versionar
# o conteúdo de gx/uncommitted/ no git — adicione ao .gitignore)
CAMINHO_CONTEXTO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data_quality",
)


def obter_contexto(caminho_projeto: str | None = None):
    """
    caminho_projeto permite sobrescrever onde o contexto do GE é criado —
    por padrão usa a pasta fixa do projeto (CAMINHO_CONTEXTO), mas testes
    automatizados devem passar um diretório temporário isolado, para que
    um teste não contamine o estado de outro.
    """
    caminho = caminho_projeto or CAMINHO_CONTEXTO
    return gx.get_context(mode="file", project_root_dir=caminho)


def _obter_ou_criar_data_source(context, nome: str):
    try:
        return context.data_sources.get(nome)
    except Exception:
        return context.data_sources.add_pandas(nome)


def _obter_ou_criar_asset(data_source, nome: str):
    try:
        return data_source.get_asset(nome)
    except Exception:
        return data_source.add_dataframe_asset(name=nome)


def _obter_ou_criar_batch_definition(data_asset, nome: str):
    try:
        return data_asset.get_batch_definition(nome)
    except Exception:
        return data_asset.add_batch_definition_whole_dataframe(nome)


def rodar_validacao(df: pd.DataFrame, suite, nome_dataset: str, caminho_projeto: str | None = None) -> dict:
    """
    Roda a suite contra o DataFrame e retorna um resumo pronto para exibição,
    além de atualizar o Data Docs em disco.

    caminho_projeto: usado por testes automatizados para isolar o contexto
    do GE em um diretório temporário, em vez da pasta fixa do projeto.
    """
    context = obter_contexto(caminho_projeto)

    data_source = _obter_ou_criar_data_source(context, f"{nome_dataset}_ds")
    data_asset = _obter_ou_criar_asset(data_source, nome_dataset)
    batch_definition = _obter_ou_criar_batch_definition(data_asset, f"batch_{nome_dataset}")

    suite = context.suites.add_or_update(suite)

    validation_definition = gx.ValidationDefinition(
        data=batch_definition, suite=suite, name=f"validation_{nome_dataset}"
    )
    validation_definition = context.validation_definitions.add_or_update(validation_definition)

    checkpoint = gx.Checkpoint(
        name=f"checkpoint_{nome_dataset}", validation_definitions=[validation_definition]
    )
    checkpoint = context.checkpoints.add_or_update(checkpoint)

    resultado = checkpoint.run(batch_parameters={"dataframe": df})
    context.build_data_docs()

    detalhes = []
    for r in resultado.run_results.values():
        for exp_result in r["results"]:
            detalhes.append(
                {
                    "expectativa": exp_result["expectation_config"]["type"],
                    "coluna": exp_result["expectation_config"]["kwargs"].get("column", "-"),
                    "sucesso": exp_result["success"],
                }
            )

    return {
        "sucesso_geral": resultado.success,
        "detalhes": detalhes,
        "data_docs_path": localizar_index_data_docs(caminho_projeto),
    }


def localizar_index_data_docs(caminho_projeto: str | None = None) -> str | None:
    caminho_base = caminho_projeto or CAMINHO_CONTEXTO
    caminho = os.path.join(
        caminho_base, "gx", "uncommitted", "data_docs", "local_site", "index.html"
    )
    return caminho if os.path.exists(caminho) else None