import os
import glob
import json
import io
import pandas as pd
import streamlit as st

from src.modules.extraction import render_extraction_page

# Ajuste de caminho: Volta 3 níveis (rules -> modules -> src -> raiz)
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
PASTA_TCE = os.path.join(ROOT_DIR, 'data')
PASTA_INTERNAL = os.path.join(ROOT_DIR, 'data_internal')

# ==========================================
# FUNÇÕES DE APOIO
# ==========================================
def converter_df_para_csv(df):
    """Cache da conversão de DataFrame para CSV para evitar travamentos."""
    if df is None or df.empty:
        return b""
    return df.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')

def padronizar_chave(serie):
    s = serie.astype(str).str.replace(r'[^0-9]', '', regex=True)
    s = s.str.lstrip('0')
    return s.where(s != '', '0')

# ==========================================
# MOTOR PATRIMONIAL
# ==========================================
def carregar_dados_patrimonio(caminho_json: str, caminhos_parquet: list) -> tuple:
    with open(caminho_json, 'r', encoding='utf-8') as f:
        json_bruto = json.load(f)
    chave_sql = list(json_bruto.keys())[0]
    df_interno = pd.DataFrame(json_bruto[chave_sql])
    dfs_tce = [pd.read_parquet(c) for c in caminhos_parquet]
    df_tce_completo = pd.concat(dfs_tce, ignore_index=True)
    return df_interno, df_tce_completo

def processar_mesclagem_patrimonio(df_interno, df_tce_completo) -> pd.DataFrame:
    df_interno['chave_join'] = padronizar_chave(df_interno['TOMBO'])
    df_tce_completo['chave_join'] = padronizar_chave(df_tce_completo['numero_registro'])
    df_mesclado = pd.merge(df_interno, df_tce_completo, on='chave_join', how='outer', suffixes=('_interno', '_tce'))
    for col in ['PAT_ID', 'patrimonio_id', 'UOE_ID']:
        if col in df_mesclado.columns:
            df_mesclado[col] = pd.to_numeric(df_mesclado[col], errors='coerce').astype('Int64')
    return df_mesclado

def exibir_resultados_patrimonio(df_interno, df_tce_completo, df_full):
    df_sucesso = df_full[df_full['TOMBO'].notna() & df_full['numero_registro'].notna()]
    df_so_json = df_full[df_full['numero_registro'].isna()]
    df_so_tce = df_full[df_full['TOMBO'].isna()]

    st.divider()
    st.markdown("### 🎯 Resultado da Mesclagem Patrimonial")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("JSON Interno", f"{len(df_interno):,}".replace(",", "."))
    c2.metric("TCE-CE", f"{len(df_tce_completo):,}".replace(",", "."))
    c3.metric("✅ Cruzados", f"{len(df_sucesso):,}".replace(",", "."))
    c4.metric("Total Linhas", f"{len(df_full):,}".replace(",", "."))

    tabs = st.tabs(["🌐 Completo", "✅ Sucesso", "⚠️ Faltam no TCE", "⚠️ Faltam na base interna"])
    dados = [df_full, df_sucesso, df_so_json, df_so_tce]
    nomes = ["patrimonio_completo.csv", "patrimonio_sucesso.csv", "patrimonio_faltantes.csv", "patrimonio_sobras.csv"]

    for i, tab in enumerate(tabs):
        with tab:
            st.write(f"Visualizando amostra (500 de **{len(dados[i]):,}** linhas encontradas):")
            st.dataframe(dados[i].head(500), use_container_width=True, hide_index=True)
            st.download_button(
                label=f"📥 Baixar CSV ({len(dados[i]):,} linhas)", data=converter_df_para_csv(dados[i]),
                file_name=nomes[i], mime="text/csv", key=f"btn_download_patrimonio_{i}"
            )

def render_bloco_patrimonial():
    st.markdown("#### 🏛️ Comparação Patrimonial")
    st.caption("Cruza o TOMBO da base interna com o `numero_registro` do TCE-CE.")
    os.makedirs(PASTA_INTERNAL, exist_ok=True)

    st.markdown("**📥 1. Dados Internos (JSON)**")
    caminho_json_salvo = None
    arquivos_json = sorted([f for f in os.listdir(PASTA_INTERNAL) if f.endswith('.json')])
    metodo_origem = st.radio("Como deseja importar os dados?", ["Selecionar arquivo existente", "Fazer novo upload"], horizontal=True, key="metodo_origem_patrimonio")

    if metodo_origem == "Selecionar arquivo existente":
        if arquivos_json:
            sel = st.selectbox("Selecione o arquivo JSON:", arquivos_json, key="sel_json_patrimonio")
            caminho_json_salvo = os.path.join(PASTA_INTERNAL, sel)
        else:
            st.warning("Nenhum arquivo JSON encontrado na pasta data_internal.")
    else:
        arquivo_upload = st.file_uploader("Upload de arquivo JSON:", type=['json'], key="upload_patrimonio")
        if arquivo_upload:
            caminho_json_salvo = os.path.join(PASTA_INTERNAL, arquivo_upload.name)
            with open(caminho_json_salvo, "wb") as f:
                f.write(arquivo_upload.getbuffer())
            st.success(f"✅ Arquivo {arquivo_upload.name} salvo com sucesso!")
            st.rerun()

    st.markdown("**📄 2. Bases do TCE-CE (Bens Incorporados)**")
    
    col_tce1, col_tce2 = st.columns([3, 1])
    prefixo = "bens_incorporados_patrimonio_municipio"
    arquivos_tce = sorted(glob.glob(os.path.join(PASTA_TCE, f"{prefixo}_*.parquet")))
    
    with col_tce1:
        selecionados = st.multiselect("Selecione os arquivos:", [os.path.basename(f) for f in arquivos_tce], key="multiselect_patrimonio")
    
    with col_tce2:
        st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
        if st.button("📥 Baixar/Atualizar Bases (TCE)", use_container_width=True, key="btn_ext_pat"):
            st.session_state['show_extrair_pat'] = not st.session_state.get('show_extrair_pat', False)
            
    if st.session_state.get('show_extrair_pat', False):
        with st.container(border=True):
            st.markdown("#### 🔄 Módulo de Extração TCE-CE Integrado")
            st.caption("Baixe as bases mais recentes sem sair do seu fluxo de comparação.")
            render_extraction_page()
            st.divider()
            if st.button("❌ Concluir Extração e Voltar", key="close_ext_pat"):
                st.session_state['show_extrair_pat'] = False
                st.rerun()

    if caminho_json_salvo and selecionados:
        if st.button("🚀 Processar Cruzamento Patrimonial", type="primary", key="btn_processar_patrimonio"):
            with st.spinner("Extraindo e processando informações em memória..."):
                caminhos = [os.path.join(PASTA_TCE, arq) for arq in selecionados]
                df_int, df_tce = carregar_dados_patrimonio(caminho_json_salvo, caminhos)
                df_full = processar_mesclagem_patrimonio(df_int, df_tce)
                exibir_resultados_patrimonio(df_int, df_tce, df_full)
    else:
        st.info("👈 Selecione o JSON e as bases do TCE para iniciar o processamento.")