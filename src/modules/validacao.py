"""
Aba de Validação — valida arquivos TCE-CE localmente usando motor Polars.
"""
from io import BytesIO

import pandas as pd
import streamlit as st

from src.schemas.veiculos import CATALOGO_SCHEMAS
from src.validators.motor_polars import validar_arquivo


def render_validacao_page():
    st.header("✅ Validação de Arquivos TCE-CE")

    tipo_selecionado = st.selectbox(
        "Tipo de documento",
        options=list(CATALOGO_SCHEMAS.keys()),
        format_func=lambda k: f"{k} - {CATALOGO_SCHEMAS[k]['nome']}",
    )

    config = CATALOGO_SCHEMAS[tipo_selecionado]
    st.info(f"📄 Padrão de arquivo esperado: `{config['arquivo_pattern']}`")

    arquivo = st.file_uploader(
        "Envie o arquivo gerado para o TCE-CE",
        type=None,
        key=f"upload_validacao_{tipo_selecionado}",
    )

    if arquivo is None:
        st.stop()

    conteudo = arquivo.read().decode("latin-1")

    # Usa motor Polars unificado
    resultado = validar_arquivo(conteudo, tipo_selecionado)
    
    total = resultado["total_registros"]
    total_invalidos = resultado["total_invalidos"]
    validos = total - total_invalidos
    linhas_invalidas = resultado["linhas_invalidas"]
    df_erros = resultado["df_erros"]

    col1, col2, col3 = st.columns(3)
    col1.metric("Total de registros", total)
    col2.metric("✅ Válidos", validos)
    col3.metric("❌ Com divergência", total_invalidos)

    if total_invalidos == 0:
        st.success("Nenhuma divergência encontrada. Arquivo pronto para envio ao TCE-CE.")
        return

    st.subheader("Registros com divergência")

    # Converte df_erros (Polars) para pandas para exibição
    df_erros_pd = df_erros.to_pandas()
    
    # Agrupa erros por linha para melhor visualização
    erros_agrupados = []
    for linha in sorted(linhas_invalidas):
        erros_linha = df_erros_pd[df_erros_pd["_linha_num"] == linha]
        erros_formatados = []
        for _, row in erros_linha.iterrows():
            campo_check = row["campo_check"]
            erro = row["erro"]
            erros_formatados.append(f"**{campo_check}**: {erro}")
        
        erros_agrupados.append({
            "Linha": linha,
            "Erros": " | ".join(erros_formatados),
        })

    df_display = pd.DataFrame(erros_agrupados)
    st.dataframe(df_display, use_container_width=True)

    # Download do relatório completo
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df_erros_pd.to_excel(writer, index=False, sheet_name="Erros_Detalhados")
    buffer.seek(0)

    st.download_button(
        "⬇️ Baixar relatório de divergências (Excel)",
        data=buffer,
        file_name=f"divergencias_{tipo_selecionado}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )