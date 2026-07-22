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
    df_export = df.copy()
    
    # Identifica quem é a Nota original (False) e quem são os pagamentos fracionados da mesma nota (True)
    is_subrow = df_export.duplicated(subset=['_chave_agrupamento'], keep='first')
    
    # Converte tudo para string para podermos apagar os textos repetidos sem dar erro
    df_export = df_export.astype(str).replace('nan', '').replace('None', '')
    
    # Limpa os dados da nota nas sub-linhas, deixando APENAS os dados do pagamento fracionado
    colunas_preservadas = ['Forma de Pagamento', 'Data Confirmação Pagto (TCE)', 'Valor Pago (TCE)', '_chave_agrupamento']
    colunas_para_limpar = [c for c in df_export.columns if c not in colunas_preservadas]
    for col in colunas_para_limpar:
        df_export.loc[is_subrow, col] = ""
        
    # Coloca uma seta visual nos sub-pagamentos
    df_export.loc[is_subrow, 'Forma de Pagamento'] = "  ↳ " + df_export.loc[is_subrow, 'Forma de Pagamento']
    
    # Apaga a coluna técnica de agrupamento
    df_export = df_export.drop(columns=['_chave_agrupamento'], errors='ignore')
    
    def pintar_linha_excel(row):
        # O row.name é o índice numérico (0, 1, 2...)
        if is_subrow.iloc[row.name]:
            return ['background-color: #f8f9fa; color: #6c757d'] * len(row) # Cinza claro para os pagamentos filhos
        if row.get('Status TCE') == 'Encontrado':
            return ['background-color: #c6efce; color: #006100'] * len(row) # Verde para a Nota original
        return [''] * len(row)
        
    df_styled = df_export.style.apply(pintar_linha_excel, axis=1)
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_styled.to_excel(writer, index=False, sheet_name='Auditoria Fortes')
        worksheet = writer.sheets['Auditoria Fortes']
        
        # Habilita o Agrupamento (Collapse) nativo do Excel
        for i, sub in enumerate(is_subrow):
            row_idx = i + 2 # +1 do cabeçalho, +1 porque o Excel não começa no 0
            if sub:
                worksheet.row_dimensions[row_idx].outline_level = 1
                worksheet.row_dimensions[row_idx].hidden = True # A planilha já vai abrir colapsada!
                
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

# ==========================================
# CARREGAR DADOS EM CACHE
# ==========================================

@st.cache_data(show_spinner="Extraindo chaves dos Parquets...")
def carregar_parquets_em_cache(caminhos_parquets):
    if not caminhos_parquets:
        return pd.DataFrame()
    
    colunas_alvo = ['numero_nota_fiscal', 'data_emissao', 'valor_liquido', 'codigo_municipio', 'numero_empenho', 'exercicio_orcamento']
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
    Constrói { (codigo_municipio, exercicio_orcamento, numero_empenho): nome_negociante_normalizado }
    Serve de PONTE segura ligando o Fornecedor ao Empenho e ao Ano correto.
    """
    caminhos = glob.glob(os.path.join(PASTA_TCE, f"notas_empenho_*_{codigo_mun_busca}.parquet"))
    if not caminhos:
        return {}

    # ADICIONADO: 'exercicio_orcamento' como alvo obrigatório
    colunas_alvo = ['codigo_municipio', 'exercicio_orcamento', 'numero_empenho', 'nome_negociante']
    dfs = []
    for c in caminhos:
        try:
            schema = pq.read_schema(c)
            cols_disp = [col for col in colunas_alvo if col in schema.names]
            if 'numero_empenho' in cols_disp and 'nome_negociante' in cols_disp and 'exercicio_orcamento' in cols_disp:
                dfs.append(pd.read_parquet(c, columns=cols_disp))
        except Exception:
            continue

    if not dfs:
        return {}

    df = pd.concat(dfs, ignore_index=True)
    df['codigo_municipio'] = df['codigo_municipio'].astype(str).str.strip().str.zfill(3)
    df['exercicio_orcamento'] = df['exercicio_orcamento'].astype(str).str[:4] # Extrai o ano
    df['numero_empenho'] = df['numero_empenho'].astype(str).str.strip()
    df['nome_normalizado'] = df['nome_negociante'].apply(normalizar_nome_credor)
    
    # Remove as duplicatas usando a tripla combinação perfeita
    df = df.drop_duplicates(subset=['codigo_municipio', 'exercicio_orcamento', 'numero_empenho'])

    # Retorna uma TUPLA DE 3 ITENS (Mun, Ano, Empenho) 
    return {
        (row['codigo_municipio'], row['exercicio_orcamento'], row['numero_empenho']): row['nome_normalizado']
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

# ==========================================================================
#  MOTOR RELACIONAL (DATA WAREHOUSE COM DUCKDB)
# ==========================================================================
def processar_cruzamento_fortes(df_fortes, df_tce, caminhos_nf_tce, nf_f, data_f, valor_f, obs_f, nf_t, data_t, valor_t, municipio_selecionado):
    
    df_f = df_fortes.copy()
    df_t = df_tce.copy()

    # --- 1. Identifica o código do município base ---
    codigo_mun_busca = "000"
    if caminhos_nf_tce:
        match_mun = re.search(r'_([0-9]{3})\.parquet', caminhos_nf_tce[0])
        if match_mun: codigo_mun_busca = match_mun.group(1)
        
    # --- 2. Proteções de colunas na base TCE ---
    if nf_t not in df_t.columns: df_t[nf_t] = '0'
    if valor_t not in df_t.columns: df_t[valor_t] = '0.00'
    if 'codigo_municipio' not in df_t.columns: df_t['codigo_municipio'] = '000'
    if 'numero_empenho' not in df_t.columns: df_t['numero_empenho'] = ''
    if 'exercicio_orcamento' not in df_t.columns: 
        if data_t in df_t.columns:
            df_t['exercicio_orcamento'] = df_t[data_t].astype(str).str[:4]
        else:
            df_t['exercicio_orcamento'] = '2000'
            
    # --- 3. PADRONIZAÇÃO BLINDADA (PYTHON) ANTES DO DUCKDB ---
    # É aqui que corrigimos o BUG do Match! O Python limpa os zeros à esquerda e arruma os decimais.
    df_f['_nota_limpa'] = padronizar_documento(df_f[nf_f])
    df_f['_val_limpo'] = padronizar_valor(df_f[valor_f])
    
    df_t['_nota_limpa'] = padronizar_documento(df_t[nf_t])
    df_t['_val_limpo'] = padronizar_valor(df_t[valor_t])
    
    # --- 4. Preparação Rápida no Pandas (Ponte e Extras) ---
    mapa_negociantes = carregar_mapa_negociantes(codigo_mun_busca)
    df_ponte = pd.DataFrame([
        {'mun': k[0], 'ano': k[1], 'emp': k[2], 'nome_limpo': v}
        for k, v in mapa_negociantes.items()
    ]) if mapa_negociantes else pd.DataFrame(columns=['mun', 'ano', 'emp', 'nome_limpo'])

    df_extra = carregar_despesas_extra_cache(codigo_mun_busca)
    if df_extra.empty:
        df_extra = pd.DataFrame(columns=['codigo_municipio', 'nome_normalizado', 'valor_rounded_liquido', 'valor_rounded_bruto', 'data_emissao_despesa_extra'])

    # --- 5. O Farejador de Municípios no Fortes ---
    if municipio_selecionado != "Todos":
        df_f['_mun_adivinhado'] = str(municipio_selecionado).zfill(3)
    else:
        dict_mun = carregar_dict_municipios()
        mun_reverso = {''.join(c for c in unicodedata.normalize('NFD', str(v.split(') ')[-1])) if unicodedata.category(c) != 'Mn').upper(): str(k).zfill(3) for k, v in dict_mun.items()}
        colunas_busca = [c for c in df_f.columns if any(w in c.lower() for w in ['cliente', 'praça', 'praca', 'municipio', 'cidade', 'endereço', 'endereco'])]
        if not colunas_busca: colunas_busca = df_f.columns
        df_f['_str_linha'] = df_f[colunas_busca].astype(str).agg(' '.join, axis=1).apply(lambda x: ''.join(c for c in unicodedata.normalize('NFD', str(x)) if unicodedata.category(c) != 'Mn').upper())
        df_f['_mun_adivinhado'] = df_f['_str_linha'].apply(lambda r: next((cod for nome, cod in sorted(mun_reverso.items(), key=lambda x: len(x[0]), reverse=True) if re.search(rf'\b{nome}\b', str(r))), "000"))

    # ==============================================================================
    # 🏗️ INÍCIO DO DATA WAREHOUSE (DUCKDB)
    # ==============================================================================
    con = duckdb.connect(database=':memory:')
    con.register('raw_fortes', df_f)
    con.register('raw_tce', df_t)
    con.register('raw_ponte', df_ponte)
    con.register('raw_extra', df_extra)

    # ---------------------------------------------------------
    # VIEW 1: FORTES
    # ---------------------------------------------------------
    con.execute(f"""
        CREATE VIEW view_fortes AS 
        SELECT *,
            (_nota_limpa || '_' || _val_limpo || '_' || _mun_adivinhado) AS CHAVE_NF
        FROM raw_fortes
        WHERE _nota_limpa != '0'
    """)

    # ---------------------------------------------------------
    # VIEW 2: TCE (Notas Fiscais + Nome do Fornecedor)
    # ---------------------------------------------------------
    con.execute("""
        CREATE VIEW view_tce AS 
        SELECT T.*,
            LPAD(CAST(T.codigo_municipio AS VARCHAR), 3, '0') AS mun_limpo,
            
            -- Chaves de Ligação já usando as colunas tratadas no Pandas:
            (T._nota_limpa || '_' || T._val_limpo || '_' || LPAD(CAST(T.codigo_municipio AS VARCHAR), 3, '0')) AS CHAVE_NF,
            (LPAD(CAST(T.codigo_municipio AS VARCHAR), 3, '0') || '_' || CAST(T.numero_empenho AS VARCHAR)) AS CHAVE_BUSCA_PAGAMENTO,
            
            COALESCE(P.nome_limpo, '') AS nome_fornecedor
            
        FROM raw_tce AS T
        LEFT JOIN raw_ponte AS P 
          ON LPAD(CAST(T.codigo_municipio AS VARCHAR), 3, '0') = P.mun 
         AND SUBSTRING(CAST(COALESCE(T.exercicio_orcamento, T.data_emissao) AS VARCHAR), 1, 4) = P.ano 
         AND CAST(T.numero_empenho AS VARCHAR) = P.emp
         
        WHERE T._nota_limpa != '0'
    """)

    # ---------------------------------------------------------
    # VIEW 3: PAGAMENTOS UNIFICADOS (COM FAREJADOR DE COLUNAS)
    # ---------------------------------------------------------
    caminhos_pag = glob.glob(os.path.join(PASTA_TCE, f"notas_pagamentos_*_{codigo_mun_busca}.parquet"))
    caminhos_chq = glob.glob(os.path.join(PASTA_TCE, f"cheques_notas_pagamentos_*_{codigo_mun_busca}.parquet"))
    caminhos_fol = glob.glob(os.path.join(PASTA_TCE, f"notas_pagamentos_folhas_*_{codigo_mun_busca}.parquet"))
    
    uniao_blocos = []
    
    # 1. NOTAS DE PAGAMENTO
    arq_pag = []
    col_val_pag = 'NULL'
    for p in caminhos_pag:
        if os.path.exists(p):
            schema = pq.read_schema(p).names
            if 'numero_empenho' in schema:
                arq_pag.append(f"'{p}'")
                if col_val_pag == 'NULL':
                    cand = [c for c in schema if 'valor' in c.lower()]
                    col_val_pag = cand[0] if cand else "''"

    if arq_pag:
        str_val_pag = f"CAST({col_val_pag} AS VARCHAR)" if col_val_pag != "''" else "''"
        uniao_blocos.append(f"""
            SELECT 
                (LPAD(CAST(codigo_municipio AS VARCHAR), 3, '0') || '_' || CAST(numero_empenho AS VARCHAR)) AS CHAVE_BUSCA_PAGAMENTO,
                CAST(data_nota_pagamento AS VARCHAR) AS data_pagamento,
                'Nota de Pagamento' AS tipo_pagamento,
                {str_val_pag} AS valor_pagamento
            FROM read_parquet([{','.join(arq_pag)}], union_by_name=True) WHERE numero_empenho IS NOT NULL
        """)

    # 2. CHEQUES
    arq_chq = []
    col_val_chq = 'NULL'
    for p in caminhos_chq:
        if os.path.exists(p):
            schema = pq.read_schema(p).names
            if 'numero_nota_empenho' in schema:
                arq_chq.append(f"'{p}'")
                if col_val_chq == 'NULL':
                    cand = [c for c in schema if 'valor' in c.lower()]
                    col_val_chq = cand[0] if cand else "''"

    if arq_chq:
        str_val_chq = f"CAST({col_val_chq} AS VARCHAR)" if col_val_chq != "''" else "''"
        uniao_blocos.append(f"""
            SELECT 
                (LPAD(CAST(codigo_municipio AS VARCHAR), 3, '0') || '_' || CAST(numero_nota_empenho AS VARCHAR)) AS CHAVE_BUSCA_PAGAMENTO,
                CAST(data_emissao_cheque AS VARCHAR) AS data_pagamento,
                'Cheque' AS tipo_pagamento,
                {str_val_chq} AS valor_pagamento
            FROM read_parquet([{','.join(arq_chq)}], union_by_name=True) WHERE numero_nota_empenho IS NOT NULL
        """)

    # 3. FOLHAS
    arq_fol = []
    col_val_fol = 'NULL'
    for p in caminhos_fol:
        if os.path.exists(p):
            schema = pq.read_schema(p).names
            if 'numero_nota_empenho' in schema:
                arq_fol.append(f"'{p}'")
                if col_val_fol == 'NULL':
                    cand = [c for c in schema if 'valor' in c.lower()]
                    col_val_fol = cand[0] if cand else "''"

    if arq_fol:
        str_val_fol = f"CAST({col_val_fol} AS VARCHAR)" if col_val_fol != "''" else "''"
        uniao_blocos.append(f"""
            SELECT 
                (LPAD(CAST(codigo_municipio AS VARCHAR), 3, '0') || '_' || CAST(numero_nota_empenho AS VARCHAR)) AS CHAVE_BUSCA_PAGAMENTO,
                CAST(data_emissao_folha AS VARCHAR) AS data_pagamento,
                'Folha de Pagto' AS tipo_pagamento,
                {str_val_fol} AS valor_pagamento
            FROM read_parquet([{','.join(arq_fol)}], union_by_name=True) WHERE numero_nota_empenho IS NOT NULL
        """)

    # 4. DESPESA EXTRA
    if not df_extra.empty:
        uniao_blocos.append("""
            SELECT 
                TCE.CHAVE_BUSCA_PAGAMENTO,
                EXTRA.data_emissao_despesa_extra AS data_pagamento,
                'Despesa Extra Orçamentária' AS tipo_pagamento,
                CAST(EXTRA.valor_rounded_liquido AS VARCHAR) AS valor_pagamento
            FROM raw_extra AS EXTRA
            JOIN view_tce AS TCE
              ON LPAD(CAST(EXTRA.codigo_municipio AS VARCHAR), 3, '0') = TCE.mun_limpo
             AND EXTRA.nome_normalizado != ''
             AND EXTRA.nome_normalizado = TCE.nome_fornecedor
             AND (EXTRA.valor_rounded_liquido = TCE._val_limpo OR EXTRA.valor_rounded_bruto = TCE._val_limpo)
        """)

    if uniao_blocos:
        con.execute("CREATE VIEW view_pagamentos_unificados AS " + " UNION ALL ".join(uniao_blocos))
    else:
        con.execute("CREATE VIEW view_pagamentos_unificados AS SELECT '' AS CHAVE_BUSCA_PAGAMENTO, '' AS data_pagamento, '' AS tipo_pagamento, '' AS valor_pagamento WHERE 1=0")

    # ==============================================================================
    # 🎯 A MEGA QUERY DE CRUZAMENTO (EXPANSIVA)
    # ==============================================================================
    cols_excluir = ['_mun_adivinhado', '_nota_limpa', '_val_limpo', 'CHAVE_NF']
    if '_str_linha' in df_f.columns: cols_excluir.append('_str_linha')
    str_exclude = ", ".join(cols_excluir)

    query_final = f"""
        SELECT 
            F.* EXCLUDE ({str_exclude}),
            F.CHAVE_NF AS "_chave_agrupamento", -- Coluna técnica invisível
            
            CASE WHEN TCE.CHAVE_NF IS NOT NULL THEN 'Encontrado' ELSE 'Não Encontrado' END AS "Status TCE",
            TCE.numero_empenho AS "Empenho Vinculado",
            
            CASE 
                WHEN TCE.CHAVE_NF IS NULL THEN ''
                WHEN PAG.CHAVE_BUSCA_PAGAMENTO IS NOT NULL THEN 'Pago'
                ELSE 'Não Pago'
            END AS "Status Pagamento",
            
            PAG.tipo_pagamento AS "Forma de Pagamento",
            PAG.data_pagamento AS "Data Confirmação Pagto (TCE)",
            PAG.valor_pagamento AS "Valor Pago (TCE)"
            
        FROM view_fortes AS F
        
        LEFT JOIN view_tce AS TCE 
            ON F.CHAVE_NF = TCE.CHAVE_NF
            
        -- JOIN direto sem GROUP BY! Ele expande a nota em várias linhas se houver vários pagamentos
        LEFT JOIN view_pagamentos_unificados AS PAG 
            ON TCE.CHAVE_BUSCA_PAGAMENTO = PAG.CHAVE_BUSCA_PAGAMENTO
            
        ORDER BY F.CHAVE_NF, PAG.data_pagamento
    """

    df_relatorio = con.execute(query_final).df()
    
    # ------------------------------------------------------------------------------
    # Limpeza e Cálculos
    df_relatorio.columns = [c.replace('FORTES_', '') for c in df_relatorio.columns]
    df_relatorio = df_relatorio.rename(columns={'Documento': 'Nº da Nota Fiscal'})

    # Lista de colunas a serem removidas no relatorio final
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
    
    # Métricas usando NUNIQUE (para não contar as sub-linhas expandidas duas vezes)
    qtd_ambos = df_relatorio[df_relatorio['Status TCE'] == 'Encontrado']['_chave_agrupamento'].nunique()
    qtd_so_fortes = df_relatorio[df_relatorio['Status TCE'] == 'Não Encontrado']['_chave_agrupamento'].nunique()
    qtd_pagas = df_relatorio[df_relatorio['Status Pagamento'] == 'Pago']['_chave_agrupamento'].nunique()
    qtd_pagas_extra = df_relatorio[df_relatorio['Forma de Pagamento'].astype(str).str.contains('Despesa Extra', na=False)]['_chave_agrupamento'].nunique()
    
    qtd_tce_unico = con.execute("SELECT COUNT(DISTINCT CHAVE_NF) FROM view_tce").fetchone()[0]
    qtd_so_tce = max(0, qtd_tce_unico - qtd_ambos)
    
    df_debug_f = con.execute("SELECT CHAVE_NF FROM view_fortes LIMIT 300").df()
    df_debug_t = con.execute("SELECT CHAVE_NF FROM view_tce LIMIT 300").df()
    
    # O tamanho base do Fortes para a métrica de tela também passa a ser nunique
    total_fortes = df_relatorio['_chave_agrupamento'].nunique()
    
    # Substituímos a variável df_relatorio pela tuple total_fortes que enviaremos para a tela
    return df_relatorio, qtd_ambos, qtd_so_fortes, qtd_so_tce, qtd_pagas, qtd_pagas_extra, total_fortes, df_debug_f, df_debug_t

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
                with st.spinner("Extraindo Pagamentos, Despesas Extra e Consolidando Relatório..."):
                    df_relatorio, qtd_ambos, qtd_so_fortes, qtd_so_tce, qtd_pagas, qtd_pagas_extra, total_fortes, df_debug_f, df_debug_t = processar_cruzamento_fortes(
                        df_fortes, df_tce_filtrado, caminhos_completos, nf_f, data_f, valor_f, obs_f, nf_t, data_t, valor_t, f_arq_mun
                    )
                                    
                    st.markdown("### 🎯 Resultados da Auditoria")
                    m1, m2, m3, m4= st.columns(4)
                    m1.metric("✅ Encontradas (TCE)", f"{qtd_ambos:,}".replace(',', '.'))
                    m2.metric("⚠️ Não Encontradas", f"{qtd_so_fortes:,}".replace(',', '.'))
                    m3.metric("🏢 Total no Fortes", f"{total_fortes:,}".replace(',', '.'))
                    m4.metric("🏛️ Sobra no TCE", f"{qtd_so_tce:,}".replace(',', '.'))
                    
                    st.divider()
                    st.markdown("##### 📄 Prévia do Relatório (Primeiros 100 registros)")
                    
                    colunas_previa = [
                        'Nº da Nota Fiscal', 
                        'Nome Fantasia do Cliente', 
                        'Valor Total', 
                        'Status TCE', 
                        'Empenho Vinculado', 
                        'Status Pagamento'
                    ]
                    
                    cols_exibir = [c for c in colunas_previa if c in df_relatorio.columns]
                    
                    # Exibe a tabela crua e leve (apenas amostra de 100)
                    st.dataframe(
                        df_relatorio[cols_exibir].head(100), 
                        use_container_width=True
                    )
                                        
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