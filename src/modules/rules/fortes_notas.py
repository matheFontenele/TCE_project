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
    
    # Validação do Amarelo (Valor bate exatamente)
    coluna_valor_nota = 'Valor do Contas a Receber' if 'Valor do Contas a Receber' in df_export.columns else 'Valor Total'
    val_nota_padrao = padronizar_valor(df_export[coluna_valor_nota])
    val_pago_padrao = padronizar_valor(df_export['Valor Pago (TCE)'])
    is_yellow = is_subrow & (val_nota_padrao == val_pago_padrao)
    
    # A divergência de data é marcada apenas na célula "Data Ultimo Pagamento"
    # pela máscara calculada após a consolidação dos pagamentos.
    is_date_divergent = pd.Series([False] * len(df_export), index=df_export.index)

    # Converte tudo para string para podermos apagar os textos repetidos sem dar erro
    df_export = df_export.astype(str).replace('nan', '').replace('None', '')
    
    # Limpa os dados da nota nas sub-linhas, deixando APENAS os dados do pagamento fracionado
    colunas_preservadas = [
        'Forma de Pagamento', 'Forma de Pagamento_1', 'Número Pagamento', 'Data Confirmação Pagto (TCE)',
        'Valor Pago (TCE)', '_chave_agrupamento'
    ]
    colunas_para_limpar = [c for c in df_export.columns if c not in colunas_preservadas]
    for col in colunas_para_limpar:
        df_export.loc[is_subrow, col] = ""
        
    # Coloca uma seta visual nos sub-pagamentos
    df_export.loc[is_subrow, 'Forma de Pagamento'] = "  ↳ " + df_export.loc[is_subrow, 'Forma de Pagamento']
    
    # Apaga a coluna técnica de agrupamento ANTES de pegarmos os índices
    df_export = df_export.drop(columns=['_chave_agrupamento', '_divergencia_data_ultimo_pagamento'], errors='ignore')
    
    # Pega a posição (index) exata da coluna de Data para pintarmos apenas ela
    try:
        idx_col_data = df_export.columns.get_loc('Data Confirmação Pagto (TCE)')
    except KeyError:
        idx_col_data = -1
    
    def pintar_linha_excel(row):
        idx = row.name # Índice real numérico da linha atual
        estilos = [''] * len(row) # Inicia a linha totalmente sem cor
        
        # 1. BACKGROUND BASE (Pinta a linha inteira)
        if is_yellow.iloc[idx]:
            estilos = ['background-color: #ffff00; color: #000000; font-weight: bold;'] * len(row)
        elif is_subrow.iloc[idx]:
            estilos = ['color: #6c757d'] * len(row) # Texto cinza para filhos normais
        elif df['Status TCE'].iloc[idx] == 'Encontrado':
            estilos = ['background-color: #c6efce; color: #006100'] * len(row)
            
        # 2. SOBRESCREVE A COR APENAS DA DATA (Se houver divergência)
        if is_date_divergent.iloc[idx] and idx_col_data != -1:
            # Preserva a cor de fundo (ex: amarelo), mas injeta a fonte vermelha em negrito
            bg_atual = estilos[idx_col_data].split('color:')[0] 
            estilos[idx_col_data] = bg_atual + 'color: #ff0000; font-weight: bold;'
            
        return estilos
        
    idx_col_ultimo_pagamento = next(
        (df_export.columns.get_loc(c) for c in ['Data Ultimo Pagamento', 'Última Data de Recebimento'] if c in df_export.columns),
        -1,
    )

    def pintar_data_ultimo_pagamento(row):
        idx = row.name
        valor_celula = row.iloc[idx_col_ultimo_pagamento] if idx_col_ultimo_pagamento != -1 else ''
        if idx_col_ultimo_pagamento != -1 and str(valor_celula).strip() and df.get('_divergencia_data_ultimo_pagamento', pd.Series(False, index=df.index)).iloc[idx]:
            return ['background-color: #ff4b4b; color: #ffffff; font-weight: bold;' if i == idx_col_ultimo_pagamento else '' for i in range(len(row))]
        return [''] * len(row)

    # As extras são destacadas em laranja; em outros casos preserva-se a
    # marcação existente de nota encontrada/subpagamento.
    def pintar_linha_com_fonte(row):
        idx = row.name
        fonte = str(df_export.iloc[idx].get('Forma de Pagamento_1', '')).strip()
        if is_subrow.iloc[idx] and fonte.startswith('Despesa Extra Orçamentária'):
            return ['background-color: #f4b183; color: #7f3f00;'] * len(row)
        return pintar_linha_excel(row)

    df_styled = df_export.style.apply(pintar_linha_com_fonte, axis=1).apply(pintar_data_ultimo_pagamento, axis=1)
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df_styled.to_excel(writer, index=False, sheet_name='Auditoria Fortes')
        worksheet = writer.sheets['Auditoria Fortes']
        
        # Habilita o Agrupamento (Collapse) nativo do Excel
        for i, sub in enumerate(is_subrow):
            row_idx = i + 2 # +1 do cabeçalho, +1 porque o Excel começa no 1 (logo linha 2)
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

def parse_data_flex(valor):
    """Converte datas ISO, dd/mm/aaaa e YYYYMMDD dos Parquets/planilhas."""
    if valor is None or pd.isna(valor):
        return pd.NaT
    texto = str(valor).strip()
    if not texto or texto.lower() in {'nan', 'nat', 'none', 'null'}:
        return pd.NaT
    if re.fullmatch(r'\d{8}', texto):
        try:
            return pd.Timestamp(datetime.datetime.strptime(texto, '%Y%m%d'))
        except ValueError:
            return pd.NaT
    return pd.to_datetime(texto, errors='coerce', dayfirst=True)

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

def priorizar_pagamento_unico(df_pagamentos, df_nota_fiscal):
    """
    Quando há múltiplos pagamentos para o mesmo empenho, aplica regras de priorização
    para encontrar o pagamento mais provável vinculado à nota fiscal.
    """
    if df_pagamentos.empty:
        return df_pagamentos
    
    # Estratégia 1: Se há apenas 1 pagamento, retorna ele
    if len(df_pagamentos) == 1:
        return df_pagamentos
    
    # Estratégia 2: Busca por correspondência de valor
    valor_nf = df_nota_fiscal.get('valor_liquido', 0)
    try:
        valor_nf_float = float(valor_nf) if valor_nf and valor_nf != '0' else 0
    except:
        valor_nf_float = 0
    
    if valor_nf_float > 0:
        # Calcula diferença absoluta entre valor do pagamento e valor da NF
        df_pagamentos = df_pagamentos.copy()
        df_pagamentos['_diff_valor'] = abs(
            pd.to_numeric(df_pagamentos.get('valor_pagamento', 0), errors='coerce').fillna(0) - valor_nf_float
        )
        # Ordena pela menor diferença (pagamento mais próximo do valor da NF)
        df_pagamentos = df_pagamentos.sort_values('_diff_valor')
    
    # Estratégia 3: Prioriza "Nota de Pagamento" sobre outros tipos
    if 'tipo_pagamento' in df_pagamentos.columns:
        df_pagamentos['_prioridade_tipo'] = df_pagamentos['tipo_pagamento'].apply(
            lambda x: 1 if x == 'Nota de Pagamento' else 2
        )
        df_pagamentos = df_pagamentos.sort_values('_prioridade_tipo')
    
    # Remove colunas auxiliares
    cols_aux = [c for c in df_pagamentos.columns if c.startswith('_')]
    df_pagamentos = df_pagamentos.drop(columns=cols_aux, errors='ignore')
    
    # Retorna apenas o primeiro (mais relevante)
    return df_pagamentos.head(1)

def escolher_coluna_valor(schema, preferidas):
    """Prioriza nomes canônicos (iguais ao JSON do modal) antes de chutar a primeira coluna 'valor'."""
    for nome in preferidas:
        if nome in schema:
            return nome
    cand = [c for c in schema if 'valor' in c.lower()]
    return cand[0] if cand else None
# ==========================================
# 🔗 NORMALIZAÇÃO DE NOME DE FORNECEDOR (para casar Nota Fiscal <-> Despesa Extra)
# ==========================================
SUFIXOS_EMPRESARIAIS = [
    ' LTDA ME', ' LTDA EPP', ' LTDA', ' ME', ' EPP', ' EIRELI',
    ' S/A', ' SA', ' S.A.', ' - EPP', ' - ME'
]

def normalizar_nome_credor(nome):
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
    
    # 🎯 ADICIONADO: data_liquidacao no array alvo
    colunas_alvo = [
        'numero_nota_fiscal', 'data_emissao', 'valor_liquido', 'codigo_municipio',
        'codigo_orgao', 'codigo_unidade_orcamentaria', 'numero_empenho',
        'exercicio_orcamento', 'data_liquidacao'
    ]
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
    caminhos = glob.glob(os.path.join(PASTA_TCE, f"notas_empenho_*_{codigo_mun_busca}.parquet"))
    if not caminhos:
        return {}

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
    
    df = df.drop_duplicates(subset=['codigo_municipio', 'exercicio_orcamento', 'numero_empenho'])

    return {
        (row['codigo_municipio'], row['exercicio_orcamento'], row['numero_empenho']): row['nome_normalizado']
        for _, row in df.iterrows()
    }

@st.cache_data(show_spinner="Carregando Despesas Extra Orçamentárias...")
def carregar_despesas_extra_cache(codigo_mun_busca):
    caminhos = glob.glob(os.path.join(PASTA_TCE, f"despesas_extra_orcamentarias_*_{codigo_mun_busca}.parquet"))
    if not caminhos:
        return pd.DataFrame()

    colunas_alvo = [
        'codigo_municipio', 'codigo_orgao', 'codigo_unidade_orcamentaria',
        'numero_documento_despesa_extra', 'numero_documento_caixa', 'data_emissao_despesa_extra',
        'valor_documento_despesa_extra', 'valor_deducao_despesa_extra', 'valor_deducao_extra',
        'nome_credor_despesa_extra', 'status_estorno_despesa_extra'
    ]
    dfs = []
    for c in caminhos:
        try:
            schema = pq.read_schema(c)
            cols_disp = [col for col in colunas_alvo if col in schema.names]
            if {
                'codigo_municipio', 'codigo_orgao', 'codigo_unidade_orcamentaria',
                'data_emissao_despesa_extra', 'valor_documento_despesa_extra'
            } <= set(cols_disp):
                dfs.append(pd.read_parquet(c, columns=cols_disp))
        except Exception:
            continue

    if not dfs:
        return pd.DataFrame()

    df = pd.concat(dfs, ignore_index=True)

    for coluna in ['numero_documento_despesa_extra', 'numero_documento_caixa', 'status_estorno_despesa_extra']:
        if coluna not in df.columns:
            df[coluna] = 0 if coluna == 'status_estorno_despesa_extra' else ''

    if 'status_estorno_despesa_extra' in df.columns:
        df = df[~df['status_estorno_despesa_extra'].fillna(0).astype(str).isin(['1', 'True', 'true'])]

    df['valor_documento_despesa_extra'] = pd.to_numeric(df['valor_documento_despesa_extra'], errors='coerce').fillna(0)
    valor_deducao = df.get('valor_deducao_despesa_extra', df.get('valor_deducao_extra', 0))
    df['valor_deducao_despesa_extra'] = pd.to_numeric(valor_deducao, errors='coerce').fillna(0)
    # Regra definida para o pareamento: documento + dedução = valor da nota.
    df['valor_total_extra'] = df['valor_documento_despesa_extra'] + df['valor_deducao_despesa_extra']

    df['codigo_municipio'] = df['codigo_municipio'].astype(str).str.strip().str.zfill(3)
    df['codigo_orgao'] = df['codigo_orgao'].astype(str).str.strip().str.lstrip('0').replace('', '0')
    df['codigo_unidade_orcamentaria'] = df['codigo_unidade_orcamentaria'].astype(str).str.strip()
    df['valor_rounded_total'] = padronizar_valor(df['valor_total_extra'])
    df['data_emissao_despesa_extra'] = pd.to_datetime(
        df['data_emissao_despesa_extra'], errors='coerce'
    ).dt.strftime('%Y-%m-%d').fillna('')

    return df

@st.cache_data(show_spinner="Carregando Liquidações (Nova Chave Exata)...")
def carregar_liquidacoes_cache(codigo_mun_busca):
    caminhos = glob.glob(os.path.join(PASTA_TCE, f"liquidacoes_*_{codigo_mun_busca}.parquet"))
    if not caminhos:
        return pd.DataFrame()
        
    colunas_alvo = ['codigo_municipio', 'numero_empenho', 'data_liquidacao', 
                     'valor_liquidado', 'numero_sub_empenho_liquidacao', 'status_estornado']
    dfs = []
    for c in caminhos:
        try:
            schema = pq.read_schema(c).names
            cols_disp = [col for col in colunas_alvo if col in schema]
            if 'numero_empenho' in cols_disp and 'numero_sub_empenho_liquidacao' in cols_disp:
                dfs.append(pd.read_parquet(c, columns=cols_disp))
        except Exception:
            continue
            
    if not dfs:
        return pd.DataFrame()
        
    df = pd.concat(dfs, ignore_index=True)
    if 'status_estornado' in df.columns:
        df = df[~df['status_estornado'].fillna(False).astype(str).isin(['1', 'True', 'true'])]
        
    df['codigo_municipio'] = df['codigo_municipio'].astype(str).str.strip().str.zfill(3)
    df['numero_empenho'] = df['numero_empenho'].astype(str).str.strip()
    df['_val_liq_limpo'] = padronizar_valor(df['valor_liquidado'])
    df['_data_liq_limpa'] = padronizar_data(df['data_liquidacao'])
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

        # Os relatórios do Fortes podem variar em acentuação, caixa e pontuação
        # dos títulos. Padroniza apenas os nomes conhecidos usados pela auditoria.
        def chave_coluna(nome):
            nome = unicodedata.normalize('NFKD', str(nome))
            nome = ''.join(c for c in nome if not unicodedata.combining(c))
            return re.sub(r'[^a-z0-9]', '', nome.lower())

        nomes_esperados = {
            'Documento': {'documento', 'numerodocumento', 'numerodanota'},
            'Data de Emissão': {'datadeemissao', 'dataemissao', 'emissao'},
            'Valor Total': {'valortotal', 'valordanota', 'totaldanota', 'valor'},
            'Observação': {'observacao', 'observacoes', 'observacaodanota'},
        }
        renomear = {}
        for canonico, aliases in nomes_esperados.items():
            for coluna in df.columns:
                if chave_coluna(coluna) in aliases:
                    renomear[coluna] = canonico
                    break
        df = df.rename(columns=renomear)
        
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

    # Algumas versões antigas dos parquets de notas fiscais não incluem
    # órgão/unidade. Mantém o cruzamento principal funcional nesses arquivos;
    # despesas extras só casarão quando esses campos estiverem disponíveis.
    for coluna in ['codigo_orgao', 'codigo_unidade_orcamentaria']:
        if coluna not in df_t.columns:
            df_t[coluna] = ''

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
            
            -- Chave de Ligação da Nota Fiscal
            (T._nota_limpa || '_' || T._val_limpo || '_' || LPAD(CAST(T.codigo_municipio AS VARCHAR), 3, '0')) AS CHAVE_NF,
            
            -- ⚠️ CHAVE ÚNICA de busca de pagamento (município + empenho)
             (LPAD(CAST(T.codigo_municipio AS VARCHAR), 3, '0') || '_' || SUBSTRING(CAST(T.exercicio_orcamento AS VARCHAR), 1, 4) || '_' || REGEXP_REPLACE(LTRIM(TRIM(CAST(T.codigo_orgao AS VARCHAR)), '0'), '^$', '0') || '_' || TRIM(CAST(T.codigo_unidade_orcamentaria AS VARCHAR)) || '_' || CAST(T.numero_empenho AS VARCHAR)) AS CHAVE_BUSCA_PAGAMENTO,
             REGEXP_REPLACE(LTRIM(TRIM(CAST(T.codigo_orgao AS VARCHAR)), '0'), '^$', '0') AS orgao_limpo,
             TRIM(CAST(T.codigo_unidade_orcamentaria AS VARCHAR)) AS unidade_limpa,
            
            COALESCE(P.nome_limpo, '') AS nome_fornecedor
            
        FROM raw_tce AS T
        LEFT JOIN raw_ponte AS P 
          ON LPAD(CAST(T.codigo_municipio AS VARCHAR), 3, '0') = P.mun 
         AND SUBSTRING(CAST(COALESCE(T.exercicio_orcamento, T.data_emissao) AS VARCHAR), 1, 4) = P.ano 
         AND CAST(T.numero_empenho AS VARCHAR) = P.emp
         
        WHERE T._nota_limpa != '0'
    """)

    # ---------------------------------------------------------
    # VIEW 3: PAGAMENTOS (somente endpoint notas_pagamentos)
    # ---------------------------------------------------------
    caminhos_pag = [
        caminho for caminho in glob.glob(os.path.join(PASTA_TCE, f"notas_pagamentos_*_{codigo_mun_busca}.parquet"))
        if 'notas_pagamentos_folhas_' not in os.path.basename(caminho)
    ]

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
                    col_val_pag = escolher_coluna_valor(
                        schema, ['valor_nota_pagamento', 'valor_pago', 'valor_bruto', 'valor_liquido']
                    ) or "''"

    if arq_pag:
        str_val_pag = f"CAST({col_val_pag} AS VARCHAR)" if col_val_pag != "''" else "''"
        uniao_blocos.append(f"""
            SELECT
                (LPAD(CAST(codigo_municipio AS VARCHAR), 3, '0') || '_' || SUBSTRING(CAST(exercicio_orcamento AS VARCHAR), 1, 4) || '_' || REGEXP_REPLACE(LTRIM(TRIM(CAST(codigo_orgao AS VARCHAR)), '0'), '^$', '0') || '_' || TRIM(CAST(codigo_unidade_orcamentaria AS VARCHAR)) || '_' || CAST(numero_empenho AS VARCHAR)) AS CHAVE_BUSCA_PAGAMENTO,
                CAST(data_nota_pagamento AS VARCHAR) AS data_pagamento,
                'Nota de Pagamento' AS tipo_pagamento,
                {str_val_pag} AS valor_pagamento,
                CAST(numero_nota_pagamento AS VARCHAR) AS numero_pagamento
            FROM read_parquet([{','.join(arq_pag)}], union_by_name=True)
            WHERE numero_empenho IS NOT NULL
        """)

    # ---------------------------------------------------------
    # Tabela de pagamentos proveniente apenas de Notas de Pagamento
    # ---------------------------------------------------------
    if uniao_blocos:
        con.execute("CREATE VIEW view_pagamentos_base AS " + " UNION ALL ".join(uniao_blocos))
    else:
        con.execute("""
            CREATE VIEW view_pagamentos_base AS
            SELECT '' AS CHAVE_BUSCA_PAGAMENTO,
                   '' AS data_pagamento,
                   '' AS tipo_pagamento,
                   '' AS valor_pagamento,
                   CAST(NULL AS VARCHAR) AS numero_pagamento
            WHERE 1=0
        """)

    # ---------------------------------------------------------
    # A auditoria considera exclusivamente o endpoint notas_pagamentos.
    con.execute("CREATE VIEW view_pagamentos_unificados AS SELECT * FROM view_pagamentos_base")

        # ==============================================================================
    # 🎯 A MEGA QUERY DE CRUZAMENTO (EXPANSIVA)
    # ==============================================================================
    cols_excluir = ['_mun_adivinhado', '_nota_limpa', '_val_limpo', 'CHAVE_NF']
    if '_str_linha' in df_f.columns: cols_excluir.append('_str_linha')
    str_exclude = ", ".join(cols_excluir)

    query_final = f"""
        SELECT 
            F.* EXCLUDE ({str_exclude}),
            F.CHAVE_NF AS "_chave_agrupamento",
            
            CASE WHEN TCE.CHAVE_NF IS NOT NULL THEN 'Encontrado' ELSE 'Não Encontrado' END AS "Status TCE",
            TCE.numero_empenho AS "Empenho Vinculado",
            
            CASE 
                WHEN TCE.CHAVE_NF IS NULL THEN ''
                WHEN PAG.CHAVE_BUSCA_PAGAMENTO IS NOT NULL THEN 'Pago'
                ELSE 'Não Pago'
            END AS "Status Pagamento",
            
            PAG.tipo_pagamento AS "Forma de Pagamento_1",
            PAG.numero_pagamento AS "Número Pagamento",
            PAG.data_pagamento AS "Data Confirmação Pagto (TCE)",
            PAG.valor_pagamento AS "Valor Pago (TCE)"
            
        FROM view_fortes AS F
        
        LEFT JOIN view_tce AS TCE 
            ON F.CHAVE_NF = TCE.CHAVE_NF
            
        LEFT JOIN view_pagamentos_unificados AS PAG 
            ON TCE.CHAVE_BUSCA_PAGAMENTO = PAG.CHAVE_BUSCA_PAGAMENTO
            
        ORDER BY F.CHAVE_NF, PAG.data_pagamento
    """

    df_relatorio = con.execute(query_final).df()
    
    # ------------------------------------------------------------------------------
    # Limpeza e Cálculos
    df_relatorio.columns = [c.replace('FORTES_', '') for c in df_relatorio.columns]
    df_relatorio = df_relatorio.rename(columns={'Documento': 'Nº da Nota Fiscal'})

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

    # ==============================================================================
    # 🔥 FUNIL DE PAGAMENTOS — considera apenas Notas de Pagamento
    # ==============================================================================
    PRIORIDADE_FONTE = {
        'Nota de Pagamento': 1,
    }

    df_relatorio['_prio_fonte'] = df_relatorio['Forma de Pagamento_1'].map(PRIORIDADE_FONTE).fillna(99)
    df_relatorio['_val_pago_cmp'] = padronizar_valor(df_relatorio['Valor Pago (TCE)'])
    df_relatorio['_ordem_data_pagamento'] = df_relatorio['Data Confirmação Pagto (TCE)'].map(parse_data_flex)
    # O relatório auxiliar do Fortes pode trazer o valor rateado em "Valor"
    # (ex.: 5.800,54), enquanto o valor integral da nota está em
    # "Valor do Contas a Receber" (ex.: 6.093,00). Usa a mesma coluna da chave
    # de cruzamento para comparar pagamentos com o total da nota.
    coluna_valor_nota = valor_f if valor_f in df_relatorio.columns else 'Valor Total'
    df_relatorio['_val_nota_cmp'] = padronizar_valor(df_relatorio[coluna_valor_nota])

    blocos = []
    for _, grp in df_relatorio.groupby('_chave_agrupamento', sort=False):
        pagos = grp[
            grp['Forma de Pagamento'].notna()
            & (grp['Forma de Pagamento'].astype(str).str.strip() != '')
        ]

        if pagos.empty:
            blocos.append(grp)  # nota sem pagamento
            continue

        # ETAPA 1: existe Nota de Pagamento? Descarta cheque/folha/despesa extra
        prio_min = pagos['_prio_fonte'].min()
        pagos = pagos[pagos['_prio_fonte'] == prio_min]

        # ETAPA 2: algum pagamento fecha com o valor da nota? Descarta os outros
        exatos = pagos[pagos['_val_pago_cmp'] == pagos['_val_nota_cmp']]
        if not exatos.empty:
            pagos = exatos

        # ETAPA 3: dedupe de duplicados exatos
        pagos = pagos.drop_duplicates(
            subset=['Forma de Pagamento', 'Número Pagamento', 'Data Confirmação Pagto (TCE)', '_val_pago_cmp']
        )

        # A linha principal (primeira) deve representar o pagamento mais recente;
        # os pagamentos anteriores ficam como sublinhas recolhidas no Excel.
        blocos.append(pagos.sort_values('_ordem_data_pagamento', ascending=False, na_position='last'))

    df_relatorio = (
        pd.concat(blocos)
        .sort_values(['_chave_agrupamento', '_ordem_data_pagamento'], ascending=[True, False], na_position='last')
        .reset_index(drop=True)
    )

    col_data_fortes = next(
        (c for c in ['Data Ultimo Pagamento', 'Última Data de Recebimento'] if c in df_relatorio.columns),
        None,
    )
    df_relatorio['_divergencia_data_ultimo_pagamento'] = False
    if col_data_fortes and 'Data Confirmação Pagto (TCE)' in df_relatorio.columns:
        datas_pagamento = df_relatorio['Data Confirmação Pagto (TCE)'].map(parse_data_flex)
        ultimas_por_nota = datas_pagamento.groupby(df_relatorio['_chave_agrupamento']).transform('max')
        datas_fortes = df_relatorio[col_data_fortes].map(parse_data_flex)
        tem_datas = ultimas_por_nota.notna() & datas_fortes.notna()
        df_relatorio['_divergencia_data_ultimo_pagamento'] = (
            tem_datas & (ultimas_por_nota.dt.normalize() != datas_fortes.dt.normalize())
        )

    df_relatorio = df_relatorio.drop(columns=['_prio_fonte', '_val_pago_cmp', '_val_nota_cmp', '_ordem_data_pagamento'])

    # Bônus visual: data sem o "T00:00:00"
    if 'Data Confirmação Pagto (TCE)' in df_relatorio.columns:
        df_relatorio['Data Confirmação Pagto (TCE)'] = df_relatorio['Data Confirmação Pagto (TCE)'].map(
            lambda valor: parse_data_flex(valor).strftime('%d/%m/%Y')
            if pd.notna(parse_data_flex(valor)) else ''
        )
    
    qtd_ambos = df_relatorio[df_relatorio['Status TCE'] == 'Encontrado']['_chave_agrupamento'].nunique()
    qtd_so_fortes = df_relatorio[df_relatorio['Status TCE'] == 'Não Encontrado']['_chave_agrupamento'].nunique()
    qtd_pagas = df_relatorio[df_relatorio['Status Pagamento'] == 'Pago']['_chave_agrupamento'].nunique()
    qtd_pagas_extra = 0
    
    qtd_tce_unico = con.execute("SELECT COUNT(DISTINCT CHAVE_NF) FROM view_tce").fetchone()[0]
    qtd_so_tce = max(0, qtd_tce_unico - qtd_ambos)
    
    df_debug_f = con.execute("SELECT CHAVE_NF FROM view_fortes LIMIT 300").df()
    df_debug_t = con.execute("SELECT CHAVE_NF FROM view_tce LIMIT 300").df()
    
    total_fortes = df_relatorio['_chave_agrupamento'].nunique()
    
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

        nf_f, data_f, obs_f = 'Documento', 'Data de Emissão', 'Observação'
        # No relatório "Contas a Receber - Ficha Financeira", "Valor" pode ser
        # o valor rateado/recebido; a nota integral costuma estar em
        # "Valor do Contas a Receber". Prioriza esta última quando disponível.
        valor_f = (
            'Valor do Contas a Receber'
            if 'Valor do Contas a Receber' in df_fortes.columns
            else 'Valor Total'
        )
        nf_t, data_t, valor_t = 'numero_nota_fiscal', 'data_emissao', 'valor_liquido'

        if st.button("Iniciar", type="primary", use_container_width=True):
            
            # Observação é mantida como parâmetro legado, mas não é usada no
            # cruzamento. Não deve impedir a auditoria em relatórios sem esse campo.
            colunas_faltando_fortes = [c for c in [nf_f, data_f, valor_f] if c not in df_fortes.columns]
            
            if colunas_faltando_fortes:
                st.error(
                    f"🚨 O arquivo do Fortes não possui as colunas obrigatórias: {colunas_faltando_fortes}. "
                    f"Colunas encontradas: {list(df_fortes.columns)}. "
                    "Confira se foi enviado o relatório de notas fiscais do Fortes."
                )
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
