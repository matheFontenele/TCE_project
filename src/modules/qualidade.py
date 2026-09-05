"""
Aba de Qualidade de Dados — roda checagens estatísticas em nível de DATASET
com Great Expectations, complementando a validação linha-a-linha da aba
"Validação". Mostra o resumo direto no Streamlit e permite abrir o Data Docs
(o site HTML gerado automaticamente pelo GE) para uma visão mais completa.
"""
import streamlit as st
import streamlit.components.v1 as components

from src.schemas.veiculos import CATALOGO_SCHEMAS
from src.validators.posicional import validar_arquivo as validar_arquivo_posicional
from src.validators.delimitado import validar_arquivo as validar_arquivo_delimitado
from src.quality.dataframe_builder import registros_para_dataframe
from src.quality.suites import construir_suite_a_partir_do_schema
from src.quality.runner import rodar_validacao, obter_contexto


def render_qualidade_page():
    st.header("📈 Qualidade de Dados (Great Expectations)")
    st.caption(
        "Enquanto a aba Validação checa cada linha individualmente, aqui rodamos "
        "checagens estatísticas sobre o dataset inteiro: percentual de nulos, "
        "quantidade mínima de registros, conformidade de valores — o tipo de "
        "checagem que aponta problemas sistêmicos, não só erros pontuais."
    )

    tipo_selecionado = st.selectbox(
        "Tipo de documento",
        options=list(CATALOGO_SCHEMAS.keys()),
        format_func=lambda k: f"{k} - {CATALOGO_SCHEMAS[k]['nome']}",
        key="qualidade_tipo_documento",
    )

    config = CATALOGO_SCHEMAS[tipo_selecionado]

    arquivo = st.file_uploader(
        "Envie o arquivo para checagem de qualidade",
        type=None,
        key=f"upload_qualidade_{tipo_selecionado}",
    )

    if arquivo is None:
        st.stop()

    conteudo = arquivo.read().decode("latin-1")

    if config["formato"] == "posicional":
        registros = validar_arquivo_posicional(conteudo, config["schema"], config["regras"])
    else:
        registros = validar_arquivo_delimitado(conteudo, config["schema"], config["regras"])

    df = registros_para_dataframe(registros)

    if df.empty:
        st.warning("Nenhum registro encontrado no arquivo.")
        return

    with st.spinner("Rodando Expectation Suite..."):
        obter_contexto()  # ativa o contexto do GE antes de montar a suite
        suite = construir_suite_a_partir_do_schema(
            nome_suite=f"suite_{tipo_selecionado}",
            campos_schema=config["schema"],
            linhas_minimas=1,
        )
        resultado = rodar_validacao(df, suite, nome_dataset=f"tipo_{tipo_selecionado}")

    if resultado["sucesso_geral"]:
        st.success("✅ Dataset passou em todas as expectativas de qualidade.")
    else:
        st.error("❌ O dataset falhou em uma ou mais expectativas de qualidade.")

    st.subheader("Resumo das expectativas")
    import pandas as pd

    df_resumo = pd.DataFrame(resultado["detalhes"])
    df_resumo["sucesso"] = df_resumo["sucesso"].map({True: "✅", False: "❌"})
    st.dataframe(df_resumo, use_container_width=True)

    st.subheader("Data Docs (relatório completo do Great Expectations)")
    if resultado["data_docs_path"]:
        with open(resultado["data_docs_path"], "r", encoding="utf-8") as f:
            html = f.read()
        components.html(html, height=500, scrolling=True)
    else:
        st.info("Data Docs ainda não foi gerado.")