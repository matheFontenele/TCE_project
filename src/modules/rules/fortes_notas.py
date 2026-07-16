import os
import glob
import io
import re
import json
import datetime
import pandas as pd
import streamlit as st
import pyarrow.parquet as pq

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

def converter_df_para_xlsx_estilizado(df):
    """Converte o DataFrame para Excel aplicando a pintura verde nas linhas encontradas"""
    if df is None or df.empty:
        return b""
    
    output = io.BytesIO()
    
    # Regra de cor nativa para o Excel
    def pintar_linha_excel(row):
        if row.get('Status TCE') == 'Encontrado':
            return ['background-color: #c6efce; color: #006100'] * len(row)
        return [''] * len(row)
        
    df_styled = df.style.apply(pintar_linha_excel, axis=1)
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_styled.to_excel(writer, index=False, sheet_name='Auditoria Fortes')
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
        x = re.sub(r'[^\d\.,]', '', x)
        if x == '': return '0.00'
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

@st.cache_data(show_spinner="Extraindo chaves dos Parquets em Alta Velocidade...")
def carregar_parquets_em_cache(caminhos_parquets):
    if not caminhos_parquets:
        return pd.DataFrame()
    
    colunas_alvo = ['numero_nota_fiscal', 'data_emissao', 'valor_liquido']
    dfs = []
    
    for c in caminhos_parquets:
        try:
            schema = pq.read_schema(c)
            cols_no_arquivo = schema.names
            cols_para_carregar = list(set(colunas_alvo).intersection(cols_no_arquivo))
            
            if cols_para_carregar:
                df = pd.read_parquet(c, columns=cols_para_carregar)
                for col in cols_para_carregar:
                    df[col] = df[col].astype(str)
                dfs.append(df)
        except Exception:
            continue
            
    return pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()

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

# ==========================================
# MOTOR FORTES x TCE (RELATÓRIO ÚNICO)
# ==========================================
def processar_cruzamento_fortes(df_fortes, df_tce, nf_f, data_f, valor_f, obs_f, nf_t, data_t, valor_t):
    df_f = df_fortes.copy()
    df_t = df_tce.copy()
    
    if nf_t not in df_t.columns: df_t[nf_t] = '0'
    if data_t not in df_t.columns: df_t[data_t] = 'SEM_DATA'
    if valor_t not in df_t.columns: df_t[valor_t] = '0.00'

    # --- PADRONIZAÇÃO BÁSICA ---
    df_f['_debug_nota'] = padronizar_documento(df_f[nf_f])
    df_t['_debug_nota'] = padronizar_documento(df_t[nf_t])
    
    df_f['_debug_data'] = padronizar_data(df_f[data_f])
    df_t['_debug_data'] = padronizar_data(df_t[data_t])
    
    df_f['_debug_valor'] = padronizar_valor(df_f[valor_f])
    df_t['_debug_valor'] = padronizar_valor(df_t[valor_t])

    # --- EXTRAÇÃO DE COMPETÊNCIA ---
    meses_map = {'JAN': '01', 'FEV': '02', 'MAR': '03', 'ABR': '04', 'MAI': '05', 'JUN': '06',
                 'JUL': '07', 'AGO': '08', 'SET': '09', 'OUT': '10', 'NOV': '11', 'DEZ': '12'}
    
    def get_comp_f(x):
        x = str(x).upper().strip()
        m = re.match(r'([A-Z]{3})/(\d{2,4})', x)
        if m:
            ano = m.group(2)
            if len(ano) == 2: ano = '20' + ano
            return f"{ano}-{meses_map.get(m.group(1), '00')}"
        return 'SEM_COMP'
        
    def get_comp_t(x):
        x = str(x).strip()
        if len(x) >= 7 and x[4] == '-': return x[:7]
        return 'SEM_COMP'

    df_f['_debug_comp'] = df_f[obs_f].apply(get_comp_f) if obs_f in df_f.columns else 'SEM_COMP'
    df_t['_debug_comp'] = df_t[data_t].apply(get_comp_t)

    # --- CHAVE MESTRA ---
    df_f['__CHAVE_MESTRA__'] = df_f['_debug_nota'] + "_" + df_f['_debug_comp'] + "_" + df_f['_debug_valor']
    df_t['__CHAVE_MESTRA__'] = df_t['_debug_nota'] + "_" + df_t['_debug_comp'] + "_" + df_t['_debug_valor']
    
    df_f = df_f[df_f['_debug_nota'] != '0']
    df_t = df_t[df_t['_debug_nota'] != '0']

    # --- SEPARAÇÃO DE DEBUG ---
    cols_debug_f = ['__CHAVE_MESTRA__'] + [c for c in df_f.columns if c.startswith('_debug')]
    cols_debug_t = ['__CHAVE_MESTRA__'] + [c for c in df_t.columns if c.startswith('_debug')]
    df_debug_f = df_f[cols_debug_f].head(300)
    df_debug_t = df_t[cols_debug_t].head(300)

    # Renomeando colunas originais
    df_f = df_f.rename(columns={c: f'FORTES_{c}' for c in df_f.columns if not c.startswith('__') and not c.startswith('_debug')})
    df_t = df_t.rename(columns={c: f'TCE_{c}' for c in df_t.columns if not c.startswith('__') and not c.startswith('_debug')})

    # Cruzamento Absoluto
    df_mesclado = pd.merge(df_f, df_t, on='__CHAVE_MESTRA__', how='outer', indicator=True)
    
    # Cálculos de Métricas
    qtd_ambos = len(df_mesclado[df_mesclado['_merge'] == 'both'])
    qtd_so_fortes = len(df_mesclado[df_mesclado['_merge'] == 'left_only'])
    qtd_so_tce = len(df_mesclado[df_mesclado['_merge'] == 'right_only'])
    
    # CONSTRUÇÃO DO RELATÓRIO ÚNICO (PRIORIDADE FORTES)
    df_relatorio = df_mesclado[df_mesclado['_merge'].isin(['both', 'left_only'])].copy()
    df_relatorio['Status TCE'] = df_relatorio['_merge'].apply(lambda x: 'Encontrado' if x == 'both' else 'Não Encontrado')
    
    # Isola apenas as colunas do Fortes + o Status e limpa os prefixos
    cols_fortes = [c for c in df_relatorio.columns if c.startswith('FORTES_')]
    df_relatorio = df_relatorio[cols_fortes + ['Status TCE']].copy()
    df_relatorio.columns = [c.replace('FORTES_', '') for c in cols_fortes] + ['Status TCE']
    
    return df_relatorio, qtd_ambos, qtd_so_fortes, qtd_so_tce, df_debug_f, df_debug_t

# ==========================================
# RENDERIZAÇÃO DA INTERFACE
# ==========================================
def render_bloco_fortes():
    st.markdown("#### 📊 Auditoria Fortes × TCE (Notas Fiscais)")    
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
    from src.modules.extraction import render_extraction_page
    st.markdown("**📄 2. Seleção Inteligente de Bases do TCE-CE**")
    todos_parquets = glob.glob(os.path.join(PASTA_TCE, "*.parquet"))
    nomes_arquivos = [os.path.basename(f) for f in todos_parquets]
    
    dict_mun = carregar_dict_municipios()
    anos_completos = [str(y) for y in range(datetime.date.today().year + 2, 2007, -1)]
    municipios_completos = sorted(list(dict_mun.keys())) if dict_mun else []
    
    f_arq_cat = "notas_fiscais"

    col_f_arq_ano, col_f_arq_mun = st.columns([1, 1])
    with col_f_arq_ano:
        f_arq_ano = st.selectbox("📅 Ano:", ["Todos"] + anos_completos)
    with col_f_arq_mun:
        f_arq_mun = st.selectbox("📍 Município:", ["Todos"] + municipios_completos, format_func=lambda x: dict_mun.get(x, x) if x != "Todos" else "Todos")

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
        if selecionar_todos: 
            selecionados_tce = arquivos_filtrados
        else: 
            selecionados_tce = st.multiselect("📄 Selecione manualmente os arquivos:", options=arquivos_filtrados, default=arquivos_filtrados)

    # --- 3. EXECUÇÃO DA AUDITORIA ---
    if df_fortes is not None and selecionados_tce:
        st.divider()
        caminhos_completos = [os.path.join(PASTA_TCE, arq) for arq in selecionados_tce]
        df_tce_filtrado = carregar_parquets_em_cache(caminhos_completos)

        nf_f, data_f, valor_f, obs_f = 'Documento', 'Data de Emissão', 'Valor Total', 'Observação'
        nf_t, data_t, valor_t = 'numero_nota_fiscal', 'data_emissao', 'valor_liquido'

        if st.button("Iniciar", type="primary", use_container_width=True):
            
            colunas_faltando_fortes = [c for c in [nf_f, data_f, valor_f, obs_f] if c not in df_fortes.columns]
            
            if colunas_faltando_fortes:
                st.error(f"🚨 ERRO: O arquivo do Fortes importado não possui as colunas obrigatórias: {colunas_faltando_fortes}.")
            else:
                with st.spinner("Comparando chaves e construindo relatório mestre..."):
                    df_relatorio, qtd_ambos, qtd_so_fortes, qtd_so_tce, df_debug_f, df_debug_t = processar_cruzamento_fortes(
                        df_fortes, df_tce_filtrado, nf_f, data_f, valor_f, obs_f, nf_t, data_t, valor_t
                    )
                    
                    st.markdown("###  Resultados da Auditoria")
                    m1, m2, m3, m4 = st.columns(4)
                    m1.metric("✅ Encontrados (Bateram)", f"{qtd_ambos:,}".replace(',', '.'))
                    m2.metric("⚠️ Não Encontrados", f"{qtd_so_fortes:,}".replace(',', '.'))
                    m3.metric("🏢 Total no Fortes", f"{len(df_relatorio):,}".replace(',', '.'))
                    m4.metric("🏛️ Notas Sobrando no TCE", f"{qtd_so_tce:,}".replace(',', '.'), help="Notas fiscais baixadas do TCE que não constam neste relatório do Fortes.")
                    
                    st.divider()
                    
                    # Estilização visual para o painel web
                    def pintar_linha_web(row):
                        if row.get('Status TCE') == 'Encontrado':
                            return ['background-color: rgba(198, 239, 206, 0.3); color: #28a745'] * len(row)
                        return [''] * len(row)

                    st.markdown("##### 📄 Relatório Mestre (Base: Fortes)")
                    st.dataframe(df_relatorio.style.apply(pintar_linha_web, axis=1), use_container_width=True)
                    
                    st.download_button(
                        "📥 Baixar Relatório (Excel com Cores)", 
                        data=converter_df_para_xlsx_estilizado(df_relatorio), 
                        file_name="relatorio_auditoria_fortes.xlsx",
                        type="primary",
                        use_container_width=True
                    )

                    # Diagnóstico Permanente (Se algo falhar)
                    if qtd_so_fortes > 0 or qtd_so_tce > 0:
                        st.divider()
                        with st.expander("🐞 Modo Diagnóstico (Ver chaves brutas geradas)", expanded=(qtd_ambos==0)):
                            st.caption("Abaixo estão as chaves exatas geradas pelo motor. Compare as duas tabelas para descobrir por que certas notas não bateram.")
                            col_d1, col_d2 = st.columns(2)
                            with col_d1:
                                st.markdown("**Chaves no Fortes (Nota_Competencia_Valor):**")
                                st.dataframe(df_debug_f)
                            with col_d2:
                                st.markdown("**Chaves no TCE (Nota_Competencia_Valor):**")
                                st.dataframe(df_debug_t)