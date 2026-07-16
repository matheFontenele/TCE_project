import os
import glob
import io
import re
import json
import datetime
import pandas as pd
import streamlit as st

from src.modules.extraction import render_extraction_page

# Configuração de caminhos
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
PASTA_TCE = os.path.join(ROOT_DIR, 'data')
PASTA_INTERNAL = os.path.join(ROOT_DIR, 'data_internal')

# ==========================================
# FUNÇÕES DE APOIO E CACHE
# ==========================================
def carregar_dict_municipios():
    """Lê o municipios.json da raiz e cria um dicionário { '074': '(074) IGUATU' }"""
    caminho_json = os.path.join(ROOT_DIR, 'municipios.json')
    try:
        with open(caminho_json, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # Monta o dicionário preenchendo com zero à esquerda (ex: 74 -> 074)
        return {str(m['codigo_municipio']).zfill(3): f"({str(m['codigo_municipio']).zfill(3)}) {m['nome_municipio'].upper()}" for m in data['elements']}
    except Exception as e:
        return {}

def converter_df_para_xlsx(df):
    if df is None or df.empty:
        return b""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Cruzamento')
    return output.getvalue()

def padronizar_documento(serie):
    s = serie.astype(str).str.replace(r'\.0$', '', regex=True)
    s = s.str.replace(r'[^0-9]', '', regex=True)
    s = s.str.lstrip('0')
    return s.where(s != '', '0')

@st.cache_data(show_spinner="Decodificando e concatenando bases Parquet na memória...")
def carregar_parquets_em_cache(caminhos_parquets):
    if not caminhos_parquets:
        return pd.DataFrame()
    dfs = [pd.read_parquet(c) for c in caminhos_parquets]
    return pd.concat(dfs, ignore_index=True)

def extrair_metadados_arquivos(arquivos):
    categorias = set()
    padrao = re.compile(r"^(.*?)_([0-9]{4})(?:_[0-9]{2})?_([0-9]+)\.parquet$")
    for arq in arquivos:
        match = padrao.search(arq)
        if match:
            categorias.add(match.group(1))
    return sorted(list(categorias))

# ==========================================
# MOTOR FORTES x TCE
# ==========================================
def processar_cruzamento_fortes(df_fortes, df_tce, col_fortes, col_tce):
    df_f = df_fortes.rename(columns={c: f'FORTES_{c}' for c in df_fortes.columns})
    df_t = df_tce.rename(columns={c: f'TCE_{c}' for c in df_tce.columns})
    
    chave_fortes = f'FORTES_{col_fortes}'
    chave_tce = f'TCE_{col_tce}'
    
    df_f['__CHAVE_NOTA__'] = padronizar_documento(df_f[chave_fortes])
    df_t['__CHAVE_NOTA__'] = padronizar_documento(df_t[chave_tce])
    
    df_f = df_f[df_f['__CHAVE_NOTA__'] != '0']
    df_t = df_t[df_t['__CHAVE_NOTA__'] != '0']

    df_mesclado = pd.merge(df_f, df_t, on='__CHAVE_NOTA__', how='outer', indicator=True)
    
    df_ambos = df_mesclado[df_mesclado['_merge'] == 'both'].drop(columns=['_merge'])
    df_so_fortes = df_mesclado[df_mesclado['_merge'] == 'left_only'].dropna(axis=1, how='all').drop(columns=['_merge'])
    df_so_tce = df_mesclado[df_mesclado['_merge'] == 'right_only'].dropna(axis=1, how='all').drop(columns=['_merge'])
    
    return df_ambos, df_so_fortes, df_so_tce

# ==========================================
# RENDERIZAÇÃO DA INTERFACE
# ==========================================
def render_bloco_fortes():
    st.markdown("#### 📊 Auditoria Fortes × TCE (Notas Fiscais)")
    st.caption("Cruza o relatório financeiro do sistema Fortes (Excel ou CSV) com as Notas Fiscais extraídas do TCE-CE.")
    
    os.makedirs(PASTA_INTERNAL, exist_ok=True)
    st.divider()

    # --- 1. UPLOAD FORTES ---
    st.markdown("**📥 1. Relatório do Sistema Fortes (Excel ou CSV)**")
    arquivo_upload = st.file_uploader("Faça o upload do arquivo (.xlsx, .xls ou .csv):", type=['xlsx', 'xls', 'csv'], key="up_fortes")
    
    df_fortes = None
    if arquivo_upload:
        try:
            if arquivo_upload.name.lower().endswith('.csv'):
                try:
                    df_fortes = pd.read_csv(arquivo_upload, sep=';', encoding='latin-1', dtype=str)
                except Exception:
                    arquivo_upload.seek(0)
                    df_fortes = pd.read_csv(arquivo_upload, sep=',', encoding='utf-8', dtype=str)
            else:
                df_fortes = pd.read_excel(arquivo_upload, dtype=str)
                
            st.success(f"✅ Arquivo carregado: **{len(df_fortes):,}** linhas.")
            with st.expander("👁️ Pré-visualizar Arquivo Interno"):
                st.dataframe(df_fortes.head(10), use_container_width=True)
        except Exception as e:
            st.error(f"Erro ao ler o arquivo: {e}")

    # --- 2. SELEÇÃO INTELIGENTE DE PARQUETS (MACRO-FILTROS) ---
    st.markdown("**📄 2. Seleção Inteligente de Bases do TCE-CE**")
    
    todos_parquets = glob.glob(os.path.join(PASTA_TCE, "*.parquet"))
    nomes_arquivos = [os.path.basename(f) for f in todos_parquets]
    
    # 🎯 DADOS DOS FILTROS (Completos)
    dict_mun = carregar_dict_municipios()
    categorias = extrair_metadados_arquivos(nomes_arquivos)
    anos_completos = [str(y) for y in range(datetime.date.today().year + 2, 2007, -1)]
    municipios_completos = sorted(list(dict_mun.keys())) if dict_mun else []
    
    col_f_arq1, col_f_arq2, col_f_arq3, col_btn_ext = st.columns([1.5, 1, 1.5, 1.5])
    
    with col_f_arq1:
        cat_defaults = [c for c in categorias if 'nota' in c.lower() or 'fiscal' in c.lower() or 'pagamento' in c.lower()]
        idx_default = categorias.index(cat_defaults[0]) + 1 if cat_defaults else 0
        f_arq_cat = st.selectbox("📂 Categoria da Base:", ["Todas"] + categorias, index=idx_default)
        
    with col_f_arq2:
        f_arq_ano = st.selectbox("📅 Ano:", ["Todos"] + anos_completos)
        
    with col_f_arq3:
        # 🎯 O format_func exibe o nome formatado, mas retorna apenas o código ("074") para a lógica!
        f_arq_mun = st.selectbox(
            "📍 Município:", 
            ["Todos"] + municipios_completos, 
            format_func=lambda x: dict_mun.get(x, x) if x != "Todos" else "Todos"
        )
        
    with col_btn_ext:
        st.markdown("<div style='margin-top: 28px;'></div>", unsafe_allow_html=True)
        if st.button("📥 Extrair Novas Bases", use_container_width=True, key="btn_ext_fortes"):
            st.session_state['show_ext_fortes'] = not st.session_state.get('show_ext_fortes', False)
            
    if st.session_state.get('show_ext_fortes', False):
        with st.container(border=True):
            render_extraction_page()
            st.divider()
            if st.button("❌ Fechar Extração", key="close_ext_fortes"):
                st.session_state['show_ext_fortes'] = False
                st.rerun()

    # Aplica a filtragem da nomenclatura
    arquivos_filtrados = []
    padrao_busca = re.compile(r"^(.*?)_([0-9]{4})(?:_[0-9]{2})?_([0-9]+)\.parquet$")
    
    for arq in nomes_arquivos:
        match = padrao_busca.search(arq)
        if match:
            cat_arq, ano_arq, mun_arq = match.group(1), match.group(2), match.group(3)
            
            if (f_arq_cat == "Todas" or cat_arq == f_arq_cat) and \
               (f_arq_ano == "Todos" or ano_arq == f_arq_ano) and \
               (f_arq_mun == "Todos" or mun_arq == f_arq_mun):
                arquivos_filtrados.append(arq)

    selecionados_tce = []
    
    if arquivos_filtrados:
        st.success(f"🎯 Encontrado(s) **{len(arquivos_filtrados)}** arquivo(s) correspondente(s) aos filtros.")
        
        col_chk, _ = st.columns([1, 1])
        with col_chk:
            selecionar_todos = st.checkbox("✅ Selecionar todos os filtrados para a auditoria", value=True, key="chk_all_fortes")
        
        if selecionar_todos:
            selecionados_tce = arquivos_filtrados
            st.info(f"📦 Todos os **{len(arquivos_filtrados)}** arquivos estão marcados para a fusão.")
        else:
            selecionados_tce = st.multiselect(
                "📄 Selecione manualmente os arquivos que deseja cruzar:", 
                options=arquivos_filtrados,
                default=arquivos_filtrados,
                key="multi_fortes_manual"
            )
    else:
        st.warning("⚠️ Nenhum arquivo baixado atende a esses filtros. Caso precise, clique em 'Extrair Novas Bases' ao lado.")


    # --- 3. MICRO-FILTROS (DADOS) E CRUZAMENTO ---
    if df_fortes is not None and selecionados_tce:
        st.divider()
        
        # Carrega o parquet filtrado na memória
        caminhos_completos = [os.path.join(PASTA_TCE, arq) for arq in selecionados_tce]
        df_tce_raw = carregar_parquets_em_cache(caminhos_completos)
        
        st.markdown("**🛠️ 3. Filtros de Inspeção Interna (Dados do TCE)**")
        st.caption("Filtre atributos específicos dentro dos arquivos mesclados.")
        
        col_f1, col_f2 = st.columns([1, 2])
        
        col_data = next((c for c in ['data_emissao', 'data_pagamento', 'data_documento', 'data'] if c in df_tce_raw.columns), None)
        palavras_chave_credor = ['credor', 'fornecedor', 'nome', 'razao_social']
        col_forn = next((c for c in df_tce_raw.columns if any(p in c.lower() for p in palavras_chave_credor)), None)
        
        meses_disponiveis = []
        if col_data:
            df_tce_raw['_dt_temp'] = pd.to_datetime(df_tce_raw[col_data], errors='coerce')
            meses_disponiveis = sorted(df_tce_raw['_dt_temp'].dt.month.dropna().astype(int).astype(str).str.zfill(2).unique().tolist())
            
        with col_f1:
            filtro_mes = st.multiselect("📆 Mês Específico:", meses_disponiveis, key="flt_mes_interno")
            
        fornecedores_disponiveis = sorted(df_tce_raw[col_forn].astype(str).dropna().unique().tolist()) if col_forn else []
        with col_f2:
            filtro_forn = st.multiselect("🏢 Filtrar Fornecedor/Credor:", fornecedores_disponiveis, key="flt_forn_interno")
            
        # Aplica filtros internos
        df_tce_filtrado = df_tce_raw.copy()
        
        if col_data and filtro_mes:
            df_tce_filtrado = df_tce_filtrado[df_tce_filtrado['_dt_temp'].dt.month.astype(str).str.zfill(2).isin(filtro_mes)]
        if '_dt_temp' in df_tce_filtrado.columns:
            df_tce_filtrado = df_tce_filtrado.drop(columns=['_dt_temp'])
            
        if filtro_forn and col_forn:
            df_tce_filtrado = df_tce_filtrado[df_tce_filtrado[col_forn].astype(str).isin(filtro_forn)]
            
        st.divider()
        st.markdown("**⚙️ 4. Configuração das Chaves**")
        
        col_c1, col_c2 = st.columns(2)
        with col_c1:
            idx_doc = df_fortes.columns.tolist().index('Documento') if 'Documento' in df_fortes.columns else 0
            coluna_fortes = st.selectbox("Coluna da Nota no FORTES:", options=df_fortes.columns, index=idx_doc)
            
        with col_c2:
            idx_nf = next((i for i, c in enumerate(df_tce_filtrado.columns) if 'nota' in c.lower() or 'numero' in c.lower()), 0)
            coluna_tce = st.selectbox("Coluna da Nota no TCE-CE:", options=df_tce_filtrado.columns, index=idx_nf)

        # ================================
        # GATILHO DA AUDITORIA
        # ================================
        if st.button("🚀 Iniciar Auditoria", type="primary", use_container_width=True):
            with st.spinner("Processando cruzamento..."):
                df_ambos, df_so_fortes, df_so_tce = processar_cruzamento_fortes(
                    df_fortes, df_tce_filtrado, coluna_fortes, coluna_tce
                )
                
                # --- RESULTADOS ---
                st.markdown("### 🎯 Resultados da Auditoria")
                
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("📌 Bateram (Sucesso)", f"{len(df_ambos):,}".replace(',', '.'))
                m2.metric("🏢 SÓ no Fortes", f"{len(df_so_fortes):,}".replace(',', '.'))
                m3.metric("🏛️ SÓ no TCE", f"{len(df_so_tce):,}".replace(',', '.'))
                m4.metric("Total Analisado", f"{len(df_fortes) + len(df_tce_filtrado):,}".replace(',', '.'))
                
                t1, t2, t3 = st.tabs([
                    f"🏢 Só no Fortes ({len(df_so_fortes)})", 
                    f"🏛️ Só no TCE ({len(df_so_tce)})", 
                    f"✅ Casados ({len(df_ambos)})"
                ])
                
                with t1:
                    st.dataframe(df_so_fortes.head(500), use_container_width=True)
                    st.download_button("📥 Baixar Excel (Só Fortes)", data=converter_df_para_xlsx(df_so_fortes), file_name="notas_so_fortes.xlsx")
                    
                with t2:
                    st.dataframe(df_so_tce.head(500), use_container_width=True)
                    st.download_button("📥 Baixar Excel (Só TCE)", data=converter_df_para_xlsx(df_so_tce), file_name="notas_so_tce.xlsx")
                    
                with t3:
                    st.dataframe(df_ambos.head(500), use_container_width=True)
                    st.download_button("📥 Baixar Excel (Casados)", data=converter_df_para_xlsx(df_ambos), file_name="notas_batidas.xlsx")

    else:
        st.info("👈 Faça o upload do arquivo (Excel/CSV) e filtre as bases do TCE acima para iniciar a auditoria.")