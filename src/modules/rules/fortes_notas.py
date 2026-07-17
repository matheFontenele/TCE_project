import os
import glob
import io
import re
import json
import datetime
import pandas as pd
import streamlit as st
import pyarrow.parquet as pq
import duckdb

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
    if df is None or df.empty:
        return b""
    
    output = io.BytesIO()
    
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

# 🚀 MOTOR V8: Leitura Otimizada buscando Chaves Relacionais
@st.cache_data(show_spinner="Extraindo chaves dos Parquets em Alta Velocidade...")
def carregar_parquets_em_cache(caminhos_parquets):
    if not caminhos_parquets:
        return pd.DataFrame()
    
    colunas_alvo = ['numero_nota_fiscal', 'data_emissao', 'valor_liquido', 'codigo_municipio', 'numero_empenho']
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
# 🚀 MOTOR RELACIONAL (PANDAS + DUCKDB)
# ==========================================
def processar_cruzamento_fortes(df_fortes, df_tce, caminhos_nf_tce, nf_f, data_f, valor_f, obs_f, nf_t, data_t, valor_t, municipio_selecionado):
    df_f = df_fortes.copy()
    df_t = df_tce.copy()
    
    # Proteções de colunas
    if nf_t not in df_t.columns: df_t[nf_t] = '0'
    if data_t not in df_t.columns: df_t[data_t] = 'SEM_DATA'
    if valor_t not in df_t.columns: df_t[valor_t] = '0.00'
    if 'codigo_municipio' not in df_t.columns: df_t['codigo_municipio'] = '000'
    if 'numero_empenho' not in df_t.columns: df_t['numero_empenho'] = '0'

    # --- PADRONIZAÇÃO ---
    df_f['_debug_nota'] = padronizar_documento(df_f[nf_f])
    df_t['_debug_nota'] = padronizar_documento(df_t[nf_t])
    
    df_f['_debug_valor'] = padronizar_valor(df_f[valor_f])
    df_t['_debug_valor'] = padronizar_valor(df_t[valor_t])
    
    df_t['codigo_municipio'] = df_t['codigo_municipio'].astype(str).str.strip()
    df_t['numero_empenho'] = df_t['numero_empenho'].astype(str).str.strip()
    df_t['_debug_mun'] = df_t['codigo_municipio'].astype(str).str.zfill(3)

    # --- O FAREJADOR DE MUNICÍPIOS ---
    if municipio_selecionado != "Todos":
        # Se escolheu na tela, cravamos o código no Fortes!
        df_f['_debug_mun'] = str(municipio_selecionado).zfill(3)
    else:
        # Se escolheu "Todos", o Python vai ler a linha do Fortes e adivinhar a cidade
        import unicodedata
        def remover_acentos(txt):
            return ''.join(c for c in unicodedata.normalize('NFD', str(txt)) if unicodedata.category(c) != 'Mn').upper()

        dict_mun = carregar_dict_municipios()
        # Ex: converte {'074': '(074) TAUÁ'} para {'TAUA': '074'}
        mun_reverso = {remover_acentos(v.split(') ')[-1]): str(k).zfill(3) for k, v in dict_mun.items()}
        
        # Procura nas colunas que costumam ter o nome do cliente/cidade
        colunas_busca = [c for c in df_f.columns if any(w in c.lower() for w in ['cliente', 'praça', 'praca', 'municipio', 'cidade', 'endereço', 'endereco'])]
        if not colunas_busca: colunas_busca = df_f.columns
        
        df_f['_str_linha'] = df_f[colunas_busca].astype(str).agg(' '.join, axis=1).apply(remover_acentos)
        
        def achar_codigo_mun(row_limpa):
            # Ordena os municípios do maior nome pro menor para garantir precisão
            for nome, cod in sorted(mun_reverso.items(), key=lambda x: len(x[0]), reverse=True):
                if re.search(rf'\b{nome}\b', str(row_limpa)):
                    return cod
            return "000"
            
        df_f['_debug_mun'] = df_f['_str_linha'].apply(achar_codigo_mun)

    # --- A NOVA CHAVE MESTRA TRIPLA (BLINDAGEM ABSOLUTA) ---
    df_f['__CHAVE_MESTRA__'] = df_f['_debug_nota'] + "_" + df_f['_debug_valor'] + "_" + df_f['_debug_mun']
    df_t['__CHAVE_MESTRA__'] = df_t['_debug_nota'] + "_" + df_t['_debug_valor'] + "_" + df_t['_debug_mun']
    
    df_f = df_f[df_f['_debug_nota'] != '0']
    df_t = df_t[df_t['_debug_nota'] != '0']

    cols_debug_f = ['__CHAVE_MESTRA__'] + [c for c in df_f.columns if c.startswith('_debug')]
    cols_debug_t = ['__CHAVE_MESTRA__'] + [c for c in df_t.columns if c.startswith('_debug')]
    df_debug_f = df_f[cols_debug_f].head(300)
    df_debug_t = df_t[cols_debug_t].head(300)

    # Removemos as colunas auxiliares sujas para não aparecerem no relatório final
    df_f = df_f.rename(columns={c: f'FORTES_{c}' for c in df_f.columns if not c.startswith('__') and not c.startswith('_debug') and not c.startswith('_str')})
    df_t = df_t.rename(columns={c: f'TCE_{c}' for c in df_t.columns if not c.startswith('__') and not c.startswith('_debug')})

    # --- 2. (Agrupando e Somando Pagamentos) ---
    caminhos_pag = [f.replace('notas_fiscais', 'notas_pagamentos') for f in caminhos_nf_tce]
    arquivos_pag_validos = []
    
    for p in caminhos_pag:
        if os.path.exists(p):
            try:
                schema_pag = pq.read_schema(p)
                if 'numero_empenho' in schema_pag.names and 'codigo_municipio' in schema_pag.names:
                    arquivos_pag_validos.append(p)
            except:
                pass

    con = duckdb.connect(database=':memory:')
    con.register('tabela_fortes', df_f)
    con.register('tabela_tce', df_t)
    
    if arquivos_pag_validos:
        pag_files_str = "[" + ", ".join([f"'{p}'" for p in arquivos_pag_validos]) + "]"
        
        # Faz um SUM, um MAX e um STRING_AGG para consolidar as Notas de Pagamento
        join_pagamento = f"""
            LEFT JOIN (
                SELECT 
                    CAST(codigo_municipio AS VARCHAR) AS codigo_municipio,
                    CAST(numero_empenho AS VARCHAR) AS numero_empenho,
                    SUM(CAST(valor_nota_pagamento AS DOUBLE)) AS valor_total_pago,
                    MAX(CAST(data_nota_pagamento AS VARCHAR)) AS ultima_data_pagamento,
                    STRING_AGG(CAST(numero_nota_pagamento AS VARCHAR), ', ') AS numeros_notas_pagamento,
                    'Pago' AS flag_pagamento
                FROM read_parquet({pag_files_str}, union_by_name=True)
                WHERE numero_empenho IS NOT NULL
                GROUP BY codigo_municipio, numero_empenho
            ) AS pag 
            ON t.TCE_codigo_municipio = pag.codigo_municipio 
            AND t.TCE_numero_empenho = pag.numero_empenho
        """
        cols_pagamento = """
            CASE 
                WHEN t.__CHAVE_MESTRA__ IS NULL THEN ''
                ELSE COALESCE(pag.flag_pagamento, 'Não Pago') 
            END AS "Status Pagamento",
            pag.numeros_notas_pagamento AS "Nº Nota de Pagamento",
            pag.valor_total_pago AS "Total Pago (R$ TCE)",
            pag.ultima_data_pagamento AS "Data Últ. Pagamento (TCE)"
        """
    else:
        join_pagamento = ""
        cols_pagamento = """
            'Sem Arquivo de Pagamento' AS "Status Pagamento", 
            NULL AS "Nº Nota de Pagamento",
            NULL AS "Total Pago (R$ TCE)", 
            NULL AS "Data Últ. Pagamento (TCE)"
        """

    # Monta a lista de colunas para excluir dinamicamente (evita erro caso a coluna _str_linha não exista)
    colunas_excluir = [c for c in ['__CHAVE_MESTRA__', '_debug_nota', '_debug_valor', '_debug_mun', '_str_linha'] if c in df_f.columns]
    str_exclude = ", ".join(colunas_excluir)

    # Query Mestra atualizada para limpar as colunas novas geradas dinamicamente
    query = f"""
        SELECT 
            f.* EXCLUDE ({str_exclude}),
            CASE WHEN t.__CHAVE_MESTRA__ IS NOT NULL THEN 'Encontrado' ELSE 'Não Encontrado' END AS "Status TCE",
            t.TCE_numero_empenho AS "Empenho Vinculado",
            {cols_pagamento}
        FROM tabela_fortes AS f
        LEFT JOIN tabela_tce AS t ON f.__CHAVE_MESTRA__ = t.__CHAVE_MESTRA__
        {join_pagamento}
    """
    
    df_relatorio = con.execute(query).df()
    
    # Limpeza visual das colunas
    df_relatorio.columns = [c.replace('FORTES_', '') for c in df_relatorio.columns]

    # Renomeando colunas para nível de usuário
    df_relatorio = df_relatorio.rename(columns={
        'Documento': 'Nº da Nota Fiscal'
    })

    # Ocultar colunas sujas/inúteis do Fortes
    colunas_para_remover = [
        'Valor do Serviço Selecionado', 
        'Valor do Produto Selecionado', 
        'Valor dos Vencimentos de Cheque',
        'Valor dos Vencimentos de Cartão',
        'Valor da Baixa dos Vencimentos  de Cheque',
        'Valor Cancelado',
        'ISS',
        'IRRF',
        'PIS/COFINS/CSL',
        'INSS',
        'Valor ICMS Substituição',
        'Valor IPI',
        'Código do Grupo Econômico',
        'Valor do Desconto Financeiro',
        'Quantidade dos Itens',
        'Valor do Desconto dos Itens',
        'Valor Outras Retenções',
        'Juros Recebidos',
        'Multas Recebidas',
        'Valor Juros Baixas Vencimentos Diversos',
        'Valor Multa Baixas Vencimentos',
        'Valor Juros Baixas Vencimentos Cheque',
        'Valor Juros Baixas Vencimentos',
        'Cheque Juros Vencimento Diversos',
        'Multa Vencimento Diversos',
        'Descontos Vencimento Diversos',
        'Desconto Incondicional',
        'RESIDUOTOTAL',
        'Código Natureza Operação',
        'Nome Natureza Operação',
        'OUTROSDESCONTOS',
        'Valor Juros',
        'Valor Líquido Provisão'
    ]
    
    df_relatorio = df_relatorio.drop(columns=[col for col in colunas_para_remover if col in df_relatorio.columns])
    
    # Cálculos
    qtd_ambos = len(df_relatorio[df_relatorio['Status TCE'] == 'Encontrado'])
    qtd_so_fortes = len(df_relatorio[df_relatorio['Status TCE'] == 'Não Encontrado'])
    qtd_pagas = len(df_relatorio[df_relatorio['Status Pagamento'] == 'Pago'])
    
    qtd_tce_unico = df_t['__CHAVE_MESTRA__'].nunique()
    qtd_so_tce = max(0, qtd_tce_unico - qtd_ambos)
    
    return df_relatorio, qtd_ambos, qtd_so_fortes, qtd_so_tce, qtd_pagas, df_debug_f, df_debug_t

# ==========================================
# RENDERIZAÇÃO DA INTERFACE
# ==========================================
def render_bloco_fortes():
    st.markdown("#### 📊 Auditoria Fortes × TCE (Notas e Pagamentos)")    
    os.makedirs(PASTA_INTERNAL, exist_ok=True)
    st.divider()

    st.markdown("**📥 1. Relatório do Sistema Fortes (Excel ou CSV)**")
    arquivo_upload = st.file_uploader("Faça o upload do arquivo (.xlsx, .xls ou .csv):", type=['xlsx', 'xls', 'csv'], key="up_fortes")
    
    df_fortes = None
    if arquivo_upload:
        df_fortes = carregar_relatorio_fortes(arquivo_upload)
        if df_fortes is not None and not df_fortes.empty:
            st.success(f"✅ Arquivo carregado e estruturado: **{len(df_fortes):,}** linhas.")

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
                with st.spinner("DuckDB Relacional: Extraindo Pagamentos e Consolidando Relatório..."):
                    df_relatorio, qtd_ambos, qtd_so_fortes, qtd_so_tce, qtd_pagas, df_debug_f, df_debug_t = processar_cruzamento_fortes(
                        df_fortes, df_tce_filtrado, caminhos_completos, nf_f, data_f, valor_f, obs_f, nf_t, data_t, valor_t, f_arq_mun
                    )
                                    
                    st.markdown("### 🎯 Resultados da Auditoria")
                    m1, m2, m3, m4, m5 = st.columns(5)
                    m1.metric("✅ Encontrados (TCE)", f"{qtd_ambos:,}".replace(',', '.'))
                    m2.metric("⚠️ Não Encontrados", f"{qtd_so_fortes:,}".replace(',', '.'))
                    m3.metric("💰 Notas c/ Pagamento", f"{qtd_pagas:,}".replace(',', '.'))
                    m4.metric("🏢 Total no Fortes", f"{len(df_relatorio):,}".replace(',', '.'))
                    m5.metric("🏛️ Sobra no TCE", f"{qtd_so_tce:,}".replace(',', '.'))
                    
                    st.divider()
                    
                    def pintar_linha_web(row):
                        if row.get('Status TCE') == 'Encontrado':
                            return ['background-color: rgba(198, 239, 206, 0.3); color: #28a745'] * len(row)
                        return [''] * len(row)

                    st.markdown("##### 📄 Relatório Mestre Relacional (Base: Fortes + TCE + Pagamentos)")
                    st.dataframe(df_relatorio.style.apply(pintar_linha_web, axis=1), use_container_width=True)
                    
                    st.download_button(
                        "📥 Baixar Relatório Completo (Excel)", 
                        data=converter_df_para_xlsx_estilizado(df_relatorio), 
                        file_name="relatorio_auditoria_fortes_pagamentos.xlsx",
                        type="primary",
                        use_container_width=True
                    )

                    if qtd_so_fortes > 0 or qtd_so_tce > 0:
                        st.divider()
                        with st.expander("🐞 Modo Diagnóstico (Ver chaves brutas geradas)", expanded=(qtd_ambos==0)):
                            col_d1, col_d2 = st.columns(2)
                            with col_d1:
                                st.markdown("**Chaves no Fortes (Nota_Competencia_Valor):**")
                                st.dataframe(df_debug_f)
                            with col_d2:
                                st.markdown("**Chaves no TCE (Nota_Competencia_Valor):**")
                                st.dataframe(df_debug_t)