"""
Aba de Validação — valida arquivos posicionais do TCE-CE localmente,
antes do envio, evitando o ciclo de envio -> rejeição -> correção -> reenvio.
"""
from io import BytesIO

import pandas as pd
import streamlit as st

from src.schemas.veiculos import CATALOGO_SCHEMAS
from src.validators.posicional import validar_arquivo as validar_arquivo_posicional
from src.validators.delimitado import validar_arquivo as validar_arquivo_delimitado


def render_validacao_page():
    st.header("✅ Validação de Arquivos TCE-CE")
    st.caption(
        "Valide seus arquivos posicionais localmente antes de enviar ao TCE-CE, "
        "evitando o ciclo de envio → rejeição → correção → reenvio."
    )

    tipo_selecionado = st.selectbox(
        "Tipo de documento",
        options=list(CATALOGO_SCHEMAS.keys()),
        format_func=lambda k: f"{k} - {CATALOGO_SCHEMAS[k]['nome']}",
    )

    config = CATALOGO_SCHEMAS[tipo_selecionado]
    st.info(f"📄 Padrão de arquivo esperado: `{config['arquivo_pattern']}`")

    arquivo = st.file_uploader(
        "Envie o arquivo posicional gerado para o TCE-CE",
        type=None,
        key=f"upload_validacao_{tipo_selecionado}",
    )

    if arquivo is None:
        st.stop()

    conteudo = arquivo.read().decode("latin-1")  # arquivos do TCE costumam vir em latin-1

    if config["formato"] == "posicional":
        registros = validar_arquivo_posicional(conteudo, config["schema"], config["regras"])
    else:
        registros = validar_arquivo_delimitado(conteudo, config["schema"], config["regras"])

    total = len(registros)
    invalidos = [r for r in registros if not r.valido]
    validos = total - len(invalidos)

    col1, col2, col3 = st.columns(3)
    col1.metric("Total de registros", total)
    col2.metric("✅ Válidos", validos)
    col3.metric("❌ Com divergência", len(invalidos))

    if not invalidos:
        st.success("Nenhuma divergência encontrada. Arquivo pronto para envio ao TCE-CE.")
        return

    st.subheader("Registros com divergência")

    linhas_erro = []
    for registro in invalidos:
        for erro in registro.erros:
            linhas_erro.append({"Linha": registro.linha, "Erro": erro, **registro.campos})

    df_erros = pd.DataFrame(linhas_erro)
    st.dataframe(df_erros, use_container_width=True)

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_erros.to_excel(writer, index=False, sheet_name="Divergencias")
    buffer.seek(0)

    st.download_button(
        "⬇️ Baixar relatório de divergências (Excel)",
        data=buffer,
        file_name=f"divergencias_{tipo_selecionado}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )