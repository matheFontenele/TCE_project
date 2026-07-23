import duckdb
import pandas as pd
import csv
import os

def buscar_arquivos(prefixo):
    """Busca arquivos na pasta txt ignorando se está maiúsculo ou minúsculo"""
    pasta = 'data_internal/txt/'
    if not os.path.exists(pasta):
        return []
    return [os.path.join(pasta, f) for f in os.listdir(pasta) 
            if f.upper().startswith(prefixo.upper()) and f.upper().endswith('.VCL')]

def atualizar_dimensao_veiculos():
    """
    Lê os arquivos VM (989 - Cadastro) e DV (992 - Lotação).
    Agora capturamos o "numero_registro", que é a nossa Chave de Ouro inquebrável!
    """
    caminho_parquet = 'data_internal/dim_veiculos.parquet'
    
    arquivos_vm = buscar_arquivos('VM')
    arquivos_dv = buscar_arquivos('DV')
    todos_txts = arquivos_vm + arquivos_dv
    
    if not todos_txts:
        return False

    precisa_atualizar = True
    if os.path.exists(caminho_parquet):
        tempo_parquet = os.path.getmtime(caminho_parquet)
        tempo_txt_mais_novo = max([os.path.getmtime(f) for f in todos_txts])
        if tempo_parquet >= tempo_txt_mais_novo:
            precisa_atualizar = False 
            
    if not precisa_atualizar:
        return True

    print("🔄 Atualizando Dicionário Mestre de Veículos (Parquet)...")
    
    dados_989 = []
    for file in arquivos_vm:
        try:
            with open(file, 'r', encoding='utf-8', errors='ignore') as f:
                reader = csv.reader(f, delimiter=',', quotechar='"')
                for row in reader:
                    if not row: continue
                    codigo_linha = row[0].replace('\ufeff', '').replace('"', '').strip()
                    
                    # Layout 989: Index 18 é o numero_registro
                    if codigo_linha == '989' and len(row) >= 19:
                        dados_989.append({
                            'hash_renavam': str(row[2]).strip(),
                            'placa': str(row[3]).strip(),
                            'chassi': str(row[4]).strip(),
                            'marca': str(row[12]).strip(),
                            'modelo': str(row[13]).strip(),
                            'cor': str(row[14]).strip() if len(row) >= 15 else '',
                            'numero_registro_mestre': str(row[18]).strip() # A Chave de Ouro
                        })
        except Exception: continue

    dados_992 = []
    for file in arquivos_dv:
        try:
            with open(file, 'r', encoding='utf-8', errors='ignore') as f:
                reader = csv.reader(f, delimiter=',', quotechar='"')
                for row in reader:
                    if not row: continue
                    codigo_linha = row[0].replace('\ufeff', '').replace('"', '').strip()
                    if codigo_linha == '992' and len(row) >= 7:
                        dados_992.append({
                            'hash_renavam': str(row[6]).strip(),
                            'orgao_alocado': str(row[3]).strip(),
                            'unidade_alocada': str(row[4]).strip()
                        })
        except Exception: continue

    df_989 = pd.DataFrame(dados_989).drop_duplicates(subset=['numero_registro_mestre'], keep='last') if dados_989 else pd.DataFrame(columns=['numero_registro_mestre'])
    df_992 = pd.DataFrame(dados_992).drop_duplicates(subset=['hash_renavam'], keep='last') if dados_992 else pd.DataFrame(columns=['hash_renavam'])
    
    if df_989.empty:
        return False
        
    df_mestre = pd.merge(df_989, df_992, on='hash_renavam', how='left')
    df_mestre.to_parquet(caminho_parquet, engine='pyarrow', compression='snappy')
    return True

def buscar_dados_veiculos(df_final):
    """
    Tradutor Geral para endpoints de frota.
    Cruza o Parquet da API com o TXT usando a chave absoluta: 'numero_registro'.
    """
    if df_final is None or df_final.empty or 'numero_registro' not in df_final.columns or 'codigo_renavam' not in df_final.columns:
        return df_final

    if not atualizar_dimensao_veiculos():
        return df_final

    con = duckdb.connect(database=':memory:')
    con.register('df_base', df_final)
    
    col_chassi = "b.numero_chassi" if "numero_chassi" in df_final.columns else "NULL"
    
    query = f"""
        SELECT 
            b.*,
            COALESCE(NULLIF(dim.placa, ''), 'PLACA INDISPONÍVEL') AS placa_traduzida,
            COALESCE(NULLIF(dim.hash_renavam, ''), b.codigo_renavam) AS renavam_traduzido,
            COALESCE(NULLIF(dim.chassi, ''), {col_chassi}) AS chassi_traduzido,
            
            dim.marca AS marca_traduzida,
            dim.modelo AS modelo_traduzida,
            dim.cor AS cor_traduzida,
            dim.orgao_alocado,
            
            COALESCE(NULLIF(dim.placa, ''), 'Registro: ' || regexp_replace(CAST(b.numero_registro AS VARCHAR), '\.0$', '')) || ' - ' || 
            COALESCE(dim.marca, '') || ' ' || 
            COALESCE(dim.modelo, '') AS veiculo_completo
            
        FROM df_base AS b
        LEFT JOIN read_parquet('data_internal/dim_veiculos.parquet') AS dim
          ON regexp_replace(CAST(b.numero_registro AS VARCHAR), '\.0$', '') = regexp_replace(CAST(dim.numero_registro_mestre AS VARCHAR), '\.0$', '')
    """
    return con.execute(query).df()

def find_abastecimento(df_final):
    """Tradutor Especializado para Abastecimentos (Engenharia Reversa via CV / 994)"""
    if df_final is None or df_final.empty:
        return df_final

    atualizar_dimensao_veiculos()
    
    # Busca a Ponte usando o prefixo CV
    arquivos_cv = buscar_arquivos('CV')
    
    dados_994 = []
    for file in arquivos_cv:
        try:
            with open(file, 'r', encoding='utf-8', errors='ignore') as f:
                reader = csv.reader(f, delimiter=',', quotechar='"')
                for row in reader:
                    if not row: continue
                    codigo_linha = row[0].replace('\ufeff', '').replace('"', '').strip()
                    
                    if codigo_linha == '994' and len(row) >= 17:
                        dados_994.append({
                            'orgao': str(row[3]).strip(),
                            'hash_verdadeiro': str(row[6]).strip(), # Esse é o elo com a dimensão veículos!
                            'data_abast': str(row[7]).strip(), 
                            'odometro': str(row[8]).strip(),
                            'valor_total': str(row[13]).strip(),
                            'cnpj_fornecedor': str(row[16]).strip()
                        })
        except Exception: continue

    df_994 = pd.DataFrame(dados_994)
    con = duckdb.connect(database=':memory:')
    con.register('df_base', df_final)
    
    if df_994.empty:
        return con.execute("""
            SELECT b.*, 'ARQUIVO DE PONTE (CV) NÃO ENCONTRADO' AS placa_traduzida, 'RENAVAM MASCARADO' AS veiculo_completo,
            CASE WHEN UPPER(TRIM(b.tipo_combustivel)) = 'D' THEN 'Diesel' WHEN UPPER(TRIM(b.tipo_combustivel)) = 'G' THEN 'Gasolina' ELSE b.tipo_combustivel END AS combustivel_traduzido
            FROM df_base AS b
        """).df()

    con.register('df_994', df_994)
    
    # QUERY BLINDADA: Duplo Join (API -> CV -> Parquet)
    query = """
        SELECT 
            b.*,
            COALESCE(NULLIF(dim.placa, ''), 'PLACA NÃO LOCALIZADA NO CADASTRO (VM)') AS placa_traduzida,
            dim.marca AS marca_traduzida,
            dim.modelo AS modelo_traduzida,
            dim.cor AS cor_traduzida,
            
            COALESCE(NULLIF(dim.placa, ''), 'RENAVAM MASCARADO') || ' - ' || 
            COALESCE(dim.marca, '') || ' ' || 
            COALESCE(dim.modelo, '') AS veiculo_completo,
            
            CASE 
                WHEN UPPER(TRIM(b.tipo_combustivel)) = 'D' THEN 'Diesel'
                WHEN UPPER(TRIM(b.tipo_combustivel)) = 'G' THEN 'Gasolina'
                WHEN UPPER(TRIM(b.tipo_combustivel)) IN ('A', 'E', 'ET') THEN 'Etanol / Álcool'
                WHEN UPPER(TRIM(b.tipo_combustivel)) IN ('GN', 'GV') THEN 'Gás Natural'
                ELSE b.tipo_combustivel 
            END AS combustivel_traduzido
            
        FROM df_base AS b
        
        -- PASSO 1: PONTE (CV - 994) com Alta Resiliência a decimais
        LEFT JOIN df_994 AS ponte
            ON TRY_CAST(regexp_replace(CAST(b.codigo_orgao AS VARCHAR), '\.0$', '') AS DOUBLE) = TRY_CAST(ponte.orgao AS DOUBLE)
            AND REPLACE(SUBSTRING(CAST(b.data_abastecimento AS VARCHAR), 1, 10), '-', '') = ponte.data_abast
            AND regexp_replace(regexp_replace(CAST(b.dados_documento_fornecedor AS VARCHAR), '\.0$', ''), '[^0-9]', '', 'g') = regexp_replace(ponte.cnpj_fornecedor, '[^0-9]', '', 'g')
            AND (
                TRY_CAST(regexp_replace(CAST(b.numero_odometro AS VARCHAR), '\.0$', '') AS DOUBLE) = TRY_CAST(ponte.odometro AS DOUBLE)
                OR TRY_CAST(b.valor_total_abastecimento AS DOUBLE) = TRY_CAST(ponte.valor_total AS DOUBLE)
            )
            
        -- PASSO 2: TRADUTOR DE VEÍCULOS (Usando o hash_renavam que montamos da Base VM/DV)
        LEFT JOIN read_parquet('data_internal/dim_veiculos.parquet') AS dim
            ON ponte.hash_verdadeiro = CAST(dim.hash_renavam AS VARCHAR)
    """
    return con.execute(query).df()