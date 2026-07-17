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
# FUNÇÕES DE APOIO E PADRONIZAÇÃO 
# ==========================================
def carregar_dict_municipios():
    caminho_json = os.path.join(ROOT_DIR, 'municipios.json')
    try:
        with open(caminho_json, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return {str(m['codigo_municipio']).zfill(3): f"({str(m['codigo_municipio']).zfill(3)}) {m['nome_municipio'].upper()}" for m in data['elements']}
    except Exception:
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

def padronizar_data(serie):
    s = serie.astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
    s = s.str.split(r'[T\s]').str[0]
    dt = pd.to_datetime(s, errors='coerce', dayfirst=True)
    return dt.dt.strftime('%Y-%m-%d').fillna('SEM_DATA')

def padronizar_valor(serie):
    def limpar_moeda(x):
        x = str(x).upper().replace('R$', '').strip()
        if x in ['NAN', 'NONE', 'NULL', '']: return '0.00'
        # Remove tudo que não for número, ponto ou vírgula
        x = re.sub(r'[^\d\.,]', '', x)
        if ',' in x and '.' in x:
            if x.rfind(',') > x.rfind('.'):
                x = x.replace('.', '').replace(',', '.')
            else:
                x = x.replace(',', '')
        elif ',' in x:
            x = x.replace(',', '.')
        try:
            return f"{float(x):.2f}"
        except:
            return "0.00"
    return serie.apply(limpar_moeda)

@st.cache_data(show_spinner="Decodificando bases Parquet na memória...")
def carregar_parquets_em_cache(caminhos_parquets):
    if not caminhos_parquets:
        return pd.DataFrame()
    dfs = [pd.read_parquet(c) for c in caminhos_parquets]
    return pd.concat(dfs, ignore_index=True)

def carregar_relatorio_fortes(arquivo_upload):
    try:
        if arquivo_upload.name.lower().endswith('.csv'):
            bytes_data = arquivo_upload.getvalue()
            try:
                texto_decodificado = bytes_data.decode('utf-8-sig') 
            except UnicodeDecodeError:
                texto_decodificado = bytes_data.decode('latin-1')

            linhas = texto_decodificado.splitlines()
            linha_cabecalho_idx = 0
            separador = ';'
            
            for i, linha in enumerate(linhas[:30]):
                linha_lower = linha.lower()
                if 'documento' in linha_lower and ('cliente' in linha_lower or 'valor' in linha_lower or 'cpf/cnpj' in linha_lower):
                    linha_cabecalho_idx = i
                    separador = ';' if ';' in linha else ','
                    break
            
            csv_limpo = "\n".join(linhas[linha_cabecalho_idx:])
            df = pd.read_csv(io.StringIO(csv_limpo), sep=separador, dtype=str)
        else:
            df = pd.read_excel(arquivo_upload, dtype=str)
            colunas_str = " ".join(df.columns.astype(str)).lower()
            if not ('documento' in colunas_str and 'cliente' in colunas_str):
                for i, row in df.iterrows():
                    row_str = " ".join(row.dropna().astype(str)).lower()
                    if 'documento' in row_str and ('cliente' in row_str or 'valor' in row_str):
                        df.columns = row.tolist()
                        df = df.iloc[i+1:].reset_index(drop=True)
                        break

        df = df.loc[:, ~df.columns.astype(str).str.contains('^unnamed', case=False)]
        df.columns = df.columns.astype(str).str.replace('\n', ' ').str.replace('\r', '').str.strip()
        df = df.dropna(how='all', axis=1).dropna(how='all', axis=0)
        
        return df
    except Exception as e:
        st.error(f"Erro ao ler arquivo do Fortes: {e}")
        return None

def priorizar_coluna(colunas, palavras_chave):
    colunas_lower = [str(c).lower().strip() for c in colunas]
    
    # 1. Tenta Match Exato
    for pw in palavras_chave:
        for i, col in enumerate(colunas_lower):
            if pw == col:
                return i
                
    # 2. Tenta Match Parcial
    for pw in palavras_chave:
        for i, col in enumerate(colunas_lower):
            if pw in col:
                return i
    return 0

# ==========================================
# MOTOR FORTES x TCE (CHAVE DINÂMICA)
# ==========================================
def processar_cruzamento_fortes(df_fortes, df_tce, nf_f, nf_t, usar_data, data_f, data_t, usar_valor, valor_f, valor_t):
    df_f = df_fortes.copy()
    df_t = df_tce.copy()
    
    # 1. Base obrigatória (Número da Nota)
    df_f['_debug_nota'] = padronizar_documento(df_f[nf_f])
    df_t['_debug_nota'] = padronizar_documento(df_t[nf_t])
    
    df_f['__CHAVE_MESTRA__'] = df_f['_debug_nota']
    df_t['__CHAVE_MESTRA__'] = df_t['_debug_nota']
    
    df_f = df_f[df_f['_debug_nota'] != '0']
    df_t = df_t[df_t['_debug_nota'] != '0']
        
    # 2. Concatena a Data se selecionado
    if usar_data and data_f and data_t:
        df_f['_debug_data'] = padronizar_data(df_f[data_f])
        df_t['_debug_data'] = padronizar_data(df_t[data_t])
        df_f['__CHAVE_MESTRA__'] += "_" + df_f['_debug_data']
        df_t['__CHAVE_MESTRA__'] += "_" + df_t['_debug_data']
        
    # 3. Concatena o Valor se selecionado
    if usar_valor and valor_f and valor_t:
        df_f['_debug_valor'] = padronizar_valor(df_f[valor_f])
        df_t['_debug_valor'] = padronizar_valor(df_t[valor_t])
        df_f['__CHAVE_MESTRA__'] += "_" + df_f['_debug_valor']
        df_t['__CHAVE_MESTRA__'] += "_" + df_t['_debug_valor']

    # --- SEPARA OS DADOS DE DEBUG PARA MOSTRAR NA TELA ---
    cols_debug_f = ['__CHAVE_MESTRA__'] + [c for c in df_f.columns if c.startswith('_debug')]
    cols_debug_t = ['__CHAVE_MESTRA__'] + [c for c in df_t.columns if c.startswith('_debug')]
    df_debug_f = df_f[cols_debug_f].head(50)
    df_debug_t = df_t[cols_debug_t].head(50)

    df_f = df_f.rename(columns={c: f'FORTES_{c}' for c in df_f.columns if not c.startswith('__') and not c.startswith('_debug')})
    df_t = df_t.rename(columns={c: f'TCE_{c}' for c in df_t.columns if not c.startswith('__') and not c.startswith('_debug')})

    df_mesclado = pd.merge(df_f, df_t, on='__CHAVE_MESTRA__', how='outer', indicator=True)
    
    def limpar_lixo(df):
        cols_drop = [c for c in df.columns if '__CHAVE' in c or '_merge' in c or '_debug' in c]
        return df.drop(columns=cols_drop, errors='ignore').dropna(axis=1, how='all')

    df_ambos = limpar_lixo(df_mesclado[df_mesclado['_merge'] == 'both'])
    df_so_fortes = limpar_lixo(df_mesclado[df_mesclado['_merge'] == 'left_only'])
    df_so_tce = limpar_lixo(df_mesclado[df_mesclado['_merge'] == 'right_only'])
    
    return df_ambos, df_so_fortes, df_so_tce, df_debug_f, df_debug_t

# ==========================================
# RENDERIZAÇÃO DA INTERFACE
# ==========================================
def render_bloco_fortes():
    st.markdown("#### 📊 Auditoria Fortes × TCE (Notas Fiscais)")
    st.caption("Cruza o relatório financeiro do sistema Fortes com as Notas Fiscais extraídas do TCE-CE.")
    
    os.makedirs(PASTA_INTERNAL, exist_ok=True)
    st.divider()

    # --- 1. UPLOAD FORTES ---
    st.markdown("**📥 1. Relatório do Sistema Fortes (Excel ou CSV)**")
    arquivo_upload = st.file_uploader("Faça o upload do arquivo (.xlsx, .xls ou .csv):", type=['xlsx', 'xls', 'csv'], key="up_fortes")
    
    df_fortes = None
    if arquivo_upload:
        df_fortes = carregar_relatorio_fortes(arquivo_upload)
        if df_fortes is not None and not df_fortes.empty:
            st.success(f"✅ Arquivo carregado e estruturado: **{len(df_fortes):,}** linhas.")

    # --- 2. SELEÇÃO INTELIGENTE DE PARQUETS ---
    st.markdown("**📄 2. Seleção Inteligente de Bases do TCE-CE**")
    todos_parquets = glob.glob(os.path.join(PASTA_TCE, "*.parquet"))
    nomes_arquivos = [os.path.basename(f) for f in todos_parquets]
    
    dict_mun = carregar_dict_municipios()
    anos_completos = [str(y) for y in range(datetime.date.today().year + 2, 2007, -1)]
    municipios_completos = sorted(list(dict_mun.keys())) if dict_mun else []
    
    col_f_arq1, col_f_arq2, col_f_arq3, col_btn_ext = st.columns([1.5, 1, 1.5, 1.5])
    with col_f_arq1:
        st.text_input("📂 Categoria da Base:", value="notas_fiscais", disabled=True)
        f_arq_cat = "notas_fiscais"
    with col_f_arq2:
        f_arq_ano = st.selectbox("📅 Ano:", ["Todos"] + anos_completos)
    with col_f_arq3:
        f_arq_mun = st.selectbox("📍 Município:", ["Todos"] + municipios_completos, format_func=lambda x: dict_mun.get(x, x) if x != "Todos" else "Todos")
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

    arquivos_filtrados = []
    padrao_busca = re.compile(r"^(.*?)_([0-9]{4})(?:_[0-9]{2})?_([0-9]+)\.parquet$")
    for arq in nomes_arquivos:
        match = padrao_busca.search(arq)
        if match:
            cat_arq, ano_arq, mun_arq = match.group(1), match.group(2), match.group(3)
            if cat_arq == f_arq_cat and (f_arq_ano == "Todos" or ano_arq == f_arq_ano) and (f_arq_mun == "Todos" or mun_arq == f_arq_mun):
                arquivos_filtrados.append(arq)

    selecionados_tce = []
    if arquivos_filtrados:
        st.success(f"🎯 Encontrado(s) **{len(arquivos_filtrados)}** arquivo(s) de **Notas Fiscais**.")
        col_chk, _ = st.columns([1, 1])
        with col_chk:
            selecionar_todos = st.checkbox("✅ Selecionar todos os filtrados para a auditoria", value=True, key="chk_all_fortes")
        if selecionar_todos: selecionados_tce = arquivos_filtrados
        else: selecionados_tce = st.multiselect("📄 Selecione manualmente os arquivos:", options=arquivos_filtrados, default=arquivos_filtrados)

    # --- 3. CONSTRUTOR DE CHAVES DINÂMICO ---
    if df_fortes is not None and selecionados_tce:
        st.divider()
        caminhos_completos = [os.path.join(PASTA_TCE, arq) for arq in selecionados_tce]
        df_tce_filtrado = carregar_parquets_em_cache(caminhos_completos)

        st.markdown("**⚙️ 3. Construtor de Chave de Cruzamento**")
        st.caption("A Nota, Data e Valor foram marcados automaticamente para compor a chave única da Nota Fiscal.")
        
        c_chk1, c_chk2, c_chk3 = st.columns(3)
        with c_chk1: st.checkbox("📄 Número da Nota", value=True, disabled=True)
        with c_chk2: usar_data = st.checkbox("📅 Data", value=True)
        with c_chk3: usar_valor = st.checkbox("💰 Valor", value=True)
        
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Sistema Fortes**")
            idx_nota_f = priorizar_coluna(df_fortes.columns, ['documento'])
            nf_f = st.selectbox("Coluna da Nota:", options=df_fortes.columns, index=idx_nota_f, key="nf_f")
            
            data_f = None
            if usar_data:
                idx_data_f = priorizar_coluna(df_fortes.columns, ['data de emissão', 'data'])
                data_f = st.selectbox("Coluna da Data:", options=df_fortes.columns, index=idx_data_f, key="data_f")
                
            valor_f = None
            if usar_valor:
                idx_valor_f = priorizar_coluna(df_fortes.columns, ['valor total', 'valor líquido', 'valor bruto', 'valor'])
                valor_f = st.selectbox("Coluna de Valor:", options=df_fortes.columns, index=idx_valor_f, key="valor_f")
                
        with c2:
            st.markdown("**Base TCE-CE**")
            idx_nota_t = priorizar_coluna(df_tce_filtrado.columns, ['numero_nota_fiscal'])
            nf_t = st.selectbox("Coluna da Nota:", options=df_tce_filtrado.columns, index=idx_nota_t, key="nf_t")
            
            data_t = None
            
                
            valor_t = None
            if usar_valor:
                idx_valor_t = priorizar_coluna(df_tce_filtrado.columns, ['valor_liquido', 'valor_bruto', 'valor_nota', 'valor'])
                valor_t = st.selectbox("Coluna de Valor:", options=df_tce_filtrado.columns, index=idx_valor_t, key="valor_t")

        # ================================
        # GATILHO DA AUDITORIA
        # ================================
        st.write("")
        if st.button("🚀 Iniciar Auditoria Inteligente", type="primary", use_container_width=True):
            with st.spinner("Padronizando valores, limpando pontuações e processando cruzamento..."):
                df_ambos, df_so_fortes, df_so_tce, df_debug_f, df_debug_t = processar_cruzamento_fortes(
                    df_fortes, df_tce_filtrado, nf_f, nf_t, usar_data, data_f, data_t, usar_valor, valor_f, valor_t
                )
                
                if len(df_ambos) == 0:
                    st.error("🚨 O cruzamento resultou em 0. Veja abaixo como o sistema formatou as suas chaves e identifique a divergência.")
                    with st.expander("🛠️ Modo Diagnóstico (Por que não casou?)", expanded=True):
                        col_d1, col_d2 = st.columns(2)
                        with col_d1:
                            st.markdown("**O que o Fortes gerou:**")
                            st.dataframe(df_debug_f)
                        with col_d2:
                            st.markdown("**O que o TCE gerou:**")
                            st.dataframe(df_debug_t)
                
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