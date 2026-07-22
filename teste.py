import os
import glob
import io
import re
import json
import datetime
import unicodedata
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

# ==========================================
# 🔗 NORMALIZAÇÃO DE NOME DE FORNECEDOR (para casar Nota Fiscal <-> Despesa Extra)
# ==========================================
SUFIXOS_EMPRESARIAIS = [
    ' LTDA ME', ' LTDA EPP', ' LTDA', ' ME', ' EPP', ' EIRELI',
    ' S/A', ' SA', ' S.A.', ' - EPP', ' - ME'
]

def normalizar_nome_credor(nome):
    """
    Padroniza o nome do fornecedor/credor para permitir o cruzamento entre
    'nome_negociante' (notas_empenho) e 'nome_credor_despesa_extra'
    (despesas_extra_orcamentarias), que costumam vir grafados de forma
    quase idêntica, mas não 100% iguais (espaços, sufixo societário, etc.).
    """
    if pd.isna(nome):
        return ""
    txt = str(nome).upper().strip()
    txt = ''.join(c for c in unicodedata.normalize('NFD', txt) if unicodedata.category(c) != 'Mn')
    txt = re.sub(r'\s+', ' ', txt).strip()
    for suf in SUFIXOS_EMPRESARIAIS:
        if txt.endswith(suf):
            txt = txt[: -len(suf)].strip()
    return txt

# 🚀 MOTOR V8: Leitura Otimizada buscando Chaves Relacionais
@st.cache_data(show_spinner="Extraindo chaves dos Parquets...")
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


@st.cache_data(show_spinner="Carregando ponte de fornecedores (Notas de Empenho)...")
def carregar_mapa_negociantes(codigo_mun_busca):
    """
    Constrói { (codigo_municipio, numero_empenho): nome_negociante_normalizado }
    a partir dos arquivos notas_empenho_*.
    Serve de PONTE: a Nota Fiscal só tem o CNPJ do emitente, mas a Despesa
    Extra Orçamentária só tem o NOME do credor. A tabela de Empenho tem os
    dois (numero_empenho + nome_negociante), então usamos ela para descobrir
    qual nome corresponde a qual nota fiscal.
    """
    caminhos = glob.glob(os.path.join(PASTA_TCE, f"notas_empenho_*_{codigo_mun_busca}.parquet"))
    if not caminhos:
        return {}

    colunas_alvo = ['codigo_municipio', 'numero_empenho', 'nome_negociante']
    dfs = []
    for c in caminhos:
        try:
            schema = pq.read_schema(c)
            cols_disp = [col for col in colunas_alvo if col in schema.names]
            if 'numero_empenho' in cols_disp and 'nome_negociante' in cols_disp:
                dfs.append(pd.read_parquet(c, columns=cols_disp))
        except Exception:
            continue

    if not dfs:
        return {}

    df = pd.concat(dfs, ignore_index=True)
    df['codigo_municipio'] = df['codigo_municipio'].astype(str).str.strip().str.zfill(3)
    df['numero_empenho'] = df['numero_empenho'].astype(str).str.strip()
    df['nome_normalizado'] = df['nome_negociante'].apply(normalizar_nome_credor)
    df = df.drop_duplicates(subset=['codigo_municipio', 'numero_empenho'])

    return {
        (row['codigo_municipio'], row['numero_empenho']): row['nome_normalizado']
        for _, row in df.iterrows()
    }


@st.cache_data(show_spinner="Carregando Despesas Extra Orçamentárias...")
def carregar_despesas_extra_cache(codigo_mun_busca):
    """
    Carrega TODOS os arquivos de despesas_extra_orcamentarias do município
    (ignorando o ano, igual ao Radar Histórico de pagamentos), normaliza o
    nome do credor e calcula o valor líquido (documento - dedução) e o valor
    bruto (documento), para permitir casar com o valor_liquido da Nota Fiscal
    de qualquer um dos dois jeitos.
    """
    caminhos = glob.glob(os.path.join(PASTA_TCE, f"despesas_extra_orcamentarias_*_{codigo_mun_busca}.parquet"))
    if not caminhos:
        return pd.DataFrame()

    colunas_alvo = [
        'codigo_municipio', 'numero_documento_despesa_extra', 'data_emissao_despesa_extra',
        'valor_documento_despesa_extra', 'valor_deducao_despesa_extra',
        'nome_credor_despesa_extra', 'status_estorno_despesa_extra'
    ]
    dfs = []
    for c in caminhos:
        try:
            schema = pq.read_schema(c)
            cols_disp = [col for col in colunas_alvo if col in schema.names]
            if 'valor_documento_despesa_extra' in cols_disp and 'nome_credor_despesa_extra' in cols_disp:
                dfs.append(pd.read_parquet(c, columns=cols_disp))
        except Exception:
            continue

    if not dfs:
        return pd.DataFrame()

    df = pd.concat(dfs, ignore_index=True)

    # Ignora lançamentos estornados/cancelados (não representam pagamento de fato)
    if 'status_estorno_despesa_extra' in df.columns:
        df = df[~df['status_estorno_despesa_extra'].fillna(0).astype(str).isin(['1', 'True', 'true'])]

    df['valor_documento_despesa_extra'] = pd.to_numeric(df['valor_documento_despesa_extra'], errors='coerce').fillna(0)
    df['valor_deducao_despesa_extra'] = pd.to_numeric(df.get('valor_deducao_despesa_extra', 0), errors='coerce').fillna(0)
    df['valor_liquido_extra'] = df['valor_documento_despesa_extra'] - df['valor_deducao_despesa_extra']

    df['codigo_municipio'] = df['codigo_municipio'].astype(str).str.strip().str.zfill(3)
    df['nome_normalizado'] = df['nome_credor_despesa_extra'].apply(normalizar_nome_credor)
    df['valor_rounded_liquido'] = padronizar_valor(df['valor_liquido_extra'])
    df['valor_rounded_bruto'] = padronizar_valor(df['valor_documento_despesa_extra'])
    df['data_emissao_despesa_extra'] = pd.to_datetime(
        df['data_emissao_despesa_extra'], errors='coerce'
    ).dt.strftime('%Y-%m-%d').fillna('')

    return df


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

    # --- Identifica o código do município baseado nos arquivos de NF selecionados ---
    # (movido para o topo: é usado tanto pelo bridge de fornecedores quanto
    #  pela busca de pagamentos/despesa extra logo abaixo)
    codigo_mun_busca = "000"
    if caminhos_nf_tce:
        match_mun = re.search(r'_([0-9]{3})\.parquet', caminhos_nf_tce[0])
        if match_mun: codigo_mun_busca = match_mun.group(1)
    
    # Proteções de colunas
    if nf_t not in df_t.columns: df_t[nf_t] = '0'
    if data_t not in df_t.columns: df_t[data_t] = 'SEM_DATA'
    if valor_t not in df_t.columns: df_t[valor_t] = '0.00'
    if 'codigo_municipio' not in df_t.columns: df_t['codigo_municipio'] = '000'
    if 'numero_empenho' not in df_t.columns: df_t['numero_empenho'] = ''

    # --- PADRONIZAÇÃO ---
    df_f['_debug_nota'] = padronizar_documento(df_f[nf_f])
    df_t['_debug_nota'] = padronizar_documento(df_t[nf_t])
    
    df_f['_debug_valor'] = padronizar_valor(df_f[valor_f])
    df_t['_debug_valor'] = padronizar_valor(df_t[valor_t])
    
    df_t['codigo_municipio'] = df_t['codigo_municipio'].astype(str).str.strip()
    df_t['numero_empenho'] = df_t['numero_empenho'].astype(str).str.strip()
    df_t['_debug_mun'] = df_t['codigo_municipio'].astype(str).str.zfill(3)

    # ==========================================================================
    # 🔗 PONTE FORNECEDOR: liga cada Nota Fiscal (via numero_empenho) ao nome
    # normalizado do fornecedor, para depois casar com Despesa Extra Orçamentária
    # ==========================================================================
    mapa_negociantes = carregar_mapa_negociantes(codigo_mun_busca)
    if mapa_negociantes:
        df_map = pd.DataFrame([
            {'_map_mun': k[0], 'numero_empenho': k[1], '_debug_fornecedor': v}
            for k, v in mapa_negociantes.items()
        ])
        df_t = df_t.merge(
            df_map,
            left_on=['_debug_mun', 'numero_empenho'],
            right_on=['_map_mun', 'numero_empenho'],
            how='left'
        )
        df_t['_debug_fornecedor'] = df_t['_debug_fornecedor'].fillna("")
        df_t = df_t.drop(columns=['_map_mun'], errors='ignore')
    else:
        df_t['_debug_fornecedor'] = ""

    df_despesa_extra = carregar_despesas_extra_cache(codigo_mun_busca)

    # --- O FAREJADOR DE MUNICÍPIOS ---
    if municipio_selecionado != "Todos":
        # Se escolheu na tela, cravamos o código no Fortes!
        df_f['_debug_mun'] = str(municipio_selecionado).zfill(3)
    else:
        # Se escolheu "Todos", o Python vai ler a linha do Fortes e adivinhar a cidade
        def remover_acentos(txt):
            return ''.join(c for c in unicodedata.normalize('NFD', str(txt)) if unicodedata.category(c) != 'Mn').upper()

        dict_mun = carregar_dict_municipios()
        mun_reverso = {remover_acentos(v.split(') ')[-1]): str(k).zfill(3) for k, v in dict_mun.items()}
        
        colunas_busca = [c for c in df_f.columns if any(w in c.lower() for w in ['cliente', 'praça', 'praca', 'municipio', 'cidade', 'endereço', 'endereco'])]
        if not colunas_busca: colunas_busca = df_f.columns
        
        df_f['_str_linha'] = df_f[colunas_busca].astype(str).agg(' '.join, axis=1).apply(remover_acentos)
        
        def achar_codigo_mun(row_limpa):
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
    # (colunas _debug_ são preservadas SEM prefixo TCE_/FORTES_ de propósito,
    #  pois ainda precisamos delas nos JOINs do DuckDB logo abaixo)
    df_f = df_f.rename(columns={c: f'FORTES_{c}' for c in df_f.columns if not c.startswith('__') and not c.startswith('_debug') and not c.startswith('_str')})
    df_t = df_t.rename(columns={c: f'TCE_{c}' for c in df_t.columns if not c.startswith('__') and not c.startswith('_debug')})

    # ==============================================================================
    # --- 2. (RADAR HISTÓRICO COMPLETO: Varrer Pagamentos de TODOS os anos) ---
    # ==============================================================================
    # Busca na pasta data/ TODOS os arquivos de pagamentos para essa cidade, IGNORANDO O ANO!
    caminhos_pag_normais = glob.glob(os.path.join(PASTA_TCE, f"notas_pagamentos_*_{codigo_mun_busca}.parquet"))
    caminhos_cheques = glob.glob(os.path.join(PASTA_TCE, f"cheques_notas_pagamentos_*_{codigo_mun_busca}.parquet"))
    caminhos_folhas = glob.glob(os.path.join(PASTA_TCE, f"notas_pagamentos_folhas_*_{codigo_mun_busca}.parquet"))
    
    arquivos_pag_validos = []
    arquivos_cheques_validos = []
    arquivos_folhas_validos = []

    # 1. Valida Pagamentos Comuns
    for p in caminhos_pag_normais:
        try:
            if 'numero_empenho' in pq.read_schema(p).names: arquivos_pag_validos.append(p)
        except: pass
        
    # 2. Valida Cheques
    for c in caminhos_cheques:
        try:
            if 'numero_nota_empenho' in pq.read_schema(c).names: arquivos_cheques_validos.append(c)
        except: pass

    # 3. Valida Folhas
    for f_arq in caminhos_folhas:
        try:
            if 'numero_nota_empenho' in pq.read_schema(f_arq).names: arquivos_folhas_validos.append(f_arq)
        except: pass

    con = duckdb.connect(database=':memory:')
    con.register('tabela_fortes', df_f)
    con.register('tabela_tce', df_t)

    tem_pagamento_normal = bool(arquivos_pag_validos or arquivos_cheques_validos or arquivos_folhas_validos)
    tem_despesa_extra = not df_despesa_extra.empty

    if tem_despesa_extra:
        con.register('tabela_despesa_extra', df_despesa_extra)

    # ------------------------------------------------------------------------
    # JOIN 1: Notas de Pagamento / Cheques / Folhas (via numero_empenho)
    # ------------------------------------------------------------------------
    join_pagamento = ""
    if tem_pagamento_normal:
        bloco_pag = ""
        if arquivos_pag_validos:
            files_str_pag = "[" + ", ".join([f"'{p}'" for p in arquivos_pag_validos]) + "]"
            bloco_pag = f"""
                SELECT CAST(codigo_municipio AS VARCHAR) as mun, CAST(numero_empenho AS VARCHAR) as emp, MAX(CAST(data_nota_pagamento AS VARCHAR)) as u_data
                FROM read_parquet({files_str_pag}, union_by_name=True) WHERE numero_empenho IS NOT NULL GROUP BY 1, 2
            """
            
        bloco_cheque = ""
        if arquivos_cheques_validos:
            files_str_chq = "[" + ", ".join([f"'{c}'" for c in arquivos_cheques_validos]) + "]"
            bloco_cheque = f"""
                SELECT CAST(codigo_municipio AS VARCHAR) as mun, CAST(numero_nota_empenho AS VARCHAR) as emp, MAX(CAST(data_emissao_cheque AS VARCHAR)) as u_data
                FROM read_parquet({files_str_chq}, union_by_name=True) WHERE numero_nota_empenho IS NOT NULL GROUP BY 1, 2
            """

        bloco_folha = ""
        if arquivos_folhas_validos:
            files_str_folha = "[" + ", ".join([f"'{f}'" for f in arquivos_folhas_validos]) + "]"
            bloco_folha = f"""
                SELECT CAST(codigo_municipio AS VARCHAR) as mun, CAST(numero_nota_empenho AS VARCHAR) as emp, MAX(CAST(data_emissao_folha AS VARCHAR)) as u_data
                FROM read_parquet({files_str_folha}, union_by_name=True) WHERE numero_nota_empenho IS NOT NULL GROUP BY 1, 2
            """

        uniao_blocos = [b for b in [bloco_pag, bloco_cheque, bloco_folha] if b]
        uniao_sql = " UNION ALL ".join(uniao_blocos)

        join_pagamento = f"""
            LEFT JOIN (
                SELECT mun, emp, 'Pago' as flag_pagamento, MAX(u_data) as ultima_data
                FROM ({uniao_sql})
                GROUP BY mun, emp
            ) AS pag 
            ON t.TCE_codigo_municipio = pag.mun 
            AND t.TCE_numero_empenho = pag.emp
        """

    # ------------------------------------------------------------------------
    # JOIN 2: Despesa Extra Orçamentária (via município + fornecedor + valor)
    #   - Duas variantes de valor (líquido = doc - dedução; e bruto = doc puro)
    #     pois nem sempre fica claro qual das duas é a que "bate" com a NF.
    # ------------------------------------------------------------------------
    join_despesa_extra = ""
    if tem_despesa_extra:
        join_despesa_extra = """
            LEFT JOIN (
                SELECT codigo_municipio, nome_normalizado, valor_rounded_liquido AS valor_rounded,
                       'Pago' AS flag_extra, MAX(data_emissao_despesa_extra) AS ultima_data_extra
                FROM tabela_despesa_extra
                GROUP BY 1, 2, 3
            ) AS extra_liq
            ON t._debug_mun = extra_liq.codigo_municipio
            AND t._debug_fornecedor != ''
            AND t._debug_fornecedor = extra_liq.nome_normalizado
            AND t._debug_valor = extra_liq.valor_rounded

            LEFT JOIN (
                SELECT codigo_municipio, nome_normalizado, valor_rounded_bruto AS valor_rounded,
                       'Pago' AS flag_extra, MAX(data_emissao_despesa_extra) AS ultima_data_extra
                FROM tabela_despesa_extra
                GROUP BY 1, 2, 3
            ) AS extra_bruto
            ON t._debug_mun = extra_bruto.codigo_municipio
            AND t._debug_fornecedor != ''
            AND t._debug_fornecedor = extra_bruto.nome_normalizado
            AND t._debug_valor = extra_bruto.valor_rounded
        """

    # ------------------------------------------------------------------------
    # Monta as expressões finais de Status/Forma de Pagamento
    # ------------------------------------------------------------------------
    expr_pag_flag = "pag.flag_pagamento" if tem_pagamento_normal else "NULL"
    expr_pag_data = "pag.ultima_data" if tem_pagamento_normal else "NULL"
    expr_extra_flag = "COALESCE(extra_liq.flag_extra, extra_bruto.flag_extra)" if tem_despesa_extra else "NULL"
    expr_extra_data = "COALESCE(extra_liq.ultima_data_extra, extra_bruto.ultima_data_extra)" if tem_despesa_extra else "NULL"

    cols_pagamento = f"""
        CASE 
            WHEN t.__CHAVE_MESTRA__ IS NULL THEN ''
            WHEN {expr_pag_flag} = 'Pago' THEN 'Pago'
            WHEN {expr_extra_flag} = 'Pago' THEN 'Pago'
            ELSE 'Não Pago'
        END AS "Status Pagamento",
        CASE
            WHEN t.__CHAVE_MESTRA__ IS NULL THEN NULL
            WHEN {expr_pag_flag} = 'Pago' THEN 'Nota de Pagamento'
            WHEN {expr_extra_flag} = 'Pago' THEN 'Despesa Extra Orçamentária'
            ELSE NULL
        END AS "Forma de Pagamento",
        COALESCE({expr_pag_data}, {expr_extra_data}) AS "Data Confirmação Pagto (TCE)"
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
        {join_despesa_extra}
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
        'Valor do Serviço Selecionado', 'Valor do Produto Selecionado', 'Valor dos Vencimentos de Cheque',
        'Valor dos Vencimentos de Cartão', 'Valor da Baixa dos Vencimentos  de Cheque', 'Valor Cancelado',
        'ISS', 'IRRF', 'PIS/COFINS/CSL', 'INSS', 'Valor ICMS Substituição', 'Valor IPI', 'Código do Grupo Econômico',
        'Valor do Desconto Financeiro', 'Quantidade dos Itens', 'Valor do Desconto dos Itens', 'Valor Outras Retenções',
        'Juros Recebidos', 'Multas Recebidas', 'Valor Juros Baixas Vencimentos Diversos', 'Valor Multa Baixas Vencimentos',
        'Valor Juros Baixas Vencimentos Cheque', 'Valor Juros Baixas Vencimentos', 'Cheque Juros Vencimento Diversos',
        'Multa Vencimento Diversos', 'Descontos Vencimento Diversos', 'Desconto Incondicional', 'RESIDUOTOTAL',
        'Código Natureza Operação', 'Nome Natureza Operação', 'OUTROSDESCONTOS', 'Valor Juros', 'Valor Líquido Provisão'
    ]
    
    df_relatorio = df_relatorio.drop(columns=[col for col in colunas_para_remover if col in df_relatorio.columns])
    
    # Cálculos
    qtd_ambos = len(df_relatorio[df_relatorio['Status TCE'] == 'Encontrado'])
    qtd_so_fortes = len(df_relatorio[df_relatorio['Status TCE'] == 'Não Encontrado'])
    qtd_pagas = len(df_relatorio[df_relatorio['Status Pagamento'] == 'Pago'])
    qtd_pagas_extra = len(df_relatorio[df_relatorio['Forma de Pagamento'] == 'Despesa Extra Orçamentária'])
    
    qtd_tce_unico = df_t['__CHAVE_MESTRA__'].nunique()
    qtd_so_tce = max(0, qtd_tce_unico - qtd_ambos)
    
    return df_relatorio, qtd_ambos, qtd_so_fortes, qtd_so_tce, qtd_pagas, qtd_pagas_extra, df_debug_f, df_debug_t

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
                with st.spinner("DuckDB Relacional: Extraindo Pagamentos, Despesas Extra e Consolidando Relatório..."):
                    df_relatorio, qtd_ambos, qtd_so_fortes, qtd_so_tce, qtd_pagas, qtd_pagas_extra, df_debug_f, df_debug_t = processar_cruzamento_fortes(
                        df_fortes, df_tce_filtrado, caminhos_completos, nf_f, data_f, valor_f, obs_f, nf_t, data_t, valor_t, f_arq_mun
                    )
                                    
                    st.markdown("### 🎯 Resultados da Auditoria")
                    m1, m2, m3, m4, m5, m6 = st.columns(6)
                    m1.metric("✅ Encontrados (TCE)", f"{qtd_ambos:,}".replace(',', '.'))
                    m2.metric("⚠️ Não Encontrados", f"{qtd_so_fortes:,}".replace(',', '.'))
                    m3.metric("💰 Notas c/ Pagamento", f"{qtd_pagas:,}".replace(',', '.'))
                    m4.metric("💳 Via Despesa Extra", f"{qtd_pagas_extra:,}".replace(',', '.'))
                    m5.metric("🏢 Total no Fortes", f"{len(df_relatorio):,}".replace(',', '.'))
                    m6.metric("🏛️ Sobra no TCE", f"{qtd_so_tce:,}".replace(',', '.'))
                    
                    st.divider()
                    
                    def pintar_linha_web(row):
                        if row.get('Status TCE') == 'Encontrado':
                            return ['background-color: rgba(198, 239, 206, 0.3); color: #28a745'] * len(row)
                        return [''] * len(row)

                    st.markdown("##### 📄 Relatório Mestre Relacional (Base: Fortes + TCE + Pagamentos + Despesa Extra)")
                    st.dataframe(df_relatorio.style.apply(pintar_linha_web, axis=1), use_container_width=True)
                    
                    st.download_button(
                        "📥 Baixar Relatório Completo (Excel)", 
                        data=converter_df_para_xlsx_estilizado(df_relatorio), 
                        file_name="relatorio_auditoria_fortesXtce.xlsx",
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