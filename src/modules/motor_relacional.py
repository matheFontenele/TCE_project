# src/modules/motor_relacional.py
import duckdb
import pandas as pd
import os
from src.modules.data_utils import formatar_moeda, formatar_data

def normalizar_chave(valor):
    if pd.isna(valor): return ""
    v = str(valor).strip()
    if v.endswith('.0'): v = v[:-2]
    return v

def obter_dados_relacionados(regra, row, arquivos_alvo_validos):
    """
    Engine puro de dados. Retorna um dicionário com o resultado para a UI renderizar.
    """
    if not arquivos_alvo_validos:
        return {"sucesso": False, "msg": "Bases não baixadas."}

    chaves_join = regra['chaves_join']
    
    # 1. Identifica as colunas do arquivo alvo
    try:
        df_cols = duckdb.query(f"DESCRIBE SELECT * FROM read_parquet('{arquivos_alvo_validos[0]}')").df()
        colunas_alvo = df_cols['column_name'].tolist()
    except:
        colunas_alvo = []

    clausulas_where = []
    pode_fazer_join = True

    # 2. Construtor Dinâmico de Joins
    for chave_origem in chaves_join:
        valor_linha = row.get(chave_origem)
        if pd.isna(valor_linha) and chave_origem == 'numero_empenho':
            valor_linha = row.get('numero_nota_empenho')
        elif pd.isna(valor_linha) and chave_origem == 'numero_nota_empenho':
            valor_linha = row.get('numero_empenho')
            
        if pd.isna(valor_linha) or str(valor_linha).strip() == "":
            return {"sucesso": False, "msg": f"Linha atual não possui a chave '{chave_origem}'."}

        valor_linha_limpo = normalizar_chave(valor_linha)
        chave_alvo = chave_origem

        # Tradutor Universal de Chaves (O Camaleão)
        if colunas_alvo and chave_alvo not in colunas_alvo:
            if chave_origem == 'numero_nota_empenho' and 'numero_empenho' in colunas_alvo: chave_alvo = 'numero_empenho'
            elif chave_origem == 'numero_empenho' and 'numero_nota_empenho' in colunas_alvo: chave_alvo = 'numero_nota_empenho'
            elif chave_origem == 'numero_registro' and 'codigo_renavam' in colunas_alvo: chave_alvo = 'codigo_renavam'
            elif chave_origem == 'codigo_orgao' and 'orgao' in colunas_alvo: chave_alvo = 'orgao'
            else:
                pode_fazer_join = False
                break

        if pode_fazer_join:
            clausulas_where.append(f"""(
                regexp_replace(CAST({chave_alvo} AS VARCHAR), '\.0$', '') = '{valor_linha_limpo}'
                OR TRY_CAST(regexp_replace(CAST({chave_alvo} AS VARCHAR), '\.0$', '') AS BIGINT) = TRY_CAST('{valor_linha_limpo}' AS BIGINT)
            )""")

    if not pode_fazer_join:
        return {"sucesso": False, "msg": f"A base alvo não possui a chave de ligação compatível."}

    # 3. Adiciona filtros de segurança (Ano) se existir
    if colunas_alvo:
        if 'exercicio_orcamento' in colunas_alvo and 'exercicio_orcamento' in row and not pd.isna(row['exercicio_orcamento']):
            ano = normalizar_chave(row['exercicio_orcamento'])
            if ano: clausulas_where.append(f"(CAST(exercicio_orcamento AS VARCHAR) = '{ano}')")

    where_sql = " AND ".join(clausulas_where)
    files_str = "[" + ", ".join([f"'{f}'" for f in arquivos_alvo_validos]) + "]"
    query = f"SELECT * FROM read_parquet({files_str}, union_by_name=True) WHERE {where_sql}"

    # 4. Executa a Query e formata o resultado
    try:
        df_bruto = duckdb.query(query).df()
        if df_bruto.empty:
            return {"sucesso": False, "msg": "Nenhum registro vinculado encontrado."}
            
        df_exibir = pd.DataFrame()
        soma_total = 0.0
        
        for col_config in regra.get("colunas_exibicao", []):
            label = col_config["label"]
            campo = col_config["campo"]
            tipo = col_config["tipo"]
            
            if campo in df_bruto.columns:
                if tipo == "data": 
                    df_exibir[label] = df_bruto[campo].apply(formatar_data)
                elif tipo == "moeda":
                    df_bruto[campo] = pd.to_numeric(df_bruto[campo], errors='coerce').fillna(0)
                    df_exibir[label] = df_bruto[campo].apply(formatar_moeda)
                    soma_total += df_bruto[campo].sum()
                else: 
                    df_exibir[label] = df_bruto[campo].astype(str).str.replace('.0', '', regex=False)

        return {
            "sucesso": True,
            "df_bruto": df_bruto,
            "df_exibir": df_exibir,
            "soma_total": soma_total
        }
    except Exception as e:
        return {"sucesso": False, "msg": f"Erro de banco: {str(e)}"}