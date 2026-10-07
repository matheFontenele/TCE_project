import streamlit as st
import numpy as np
import pandas as pd
import io

def normalizar_coluna_chave(df, coluna):
    """Garante que as chaves de cruzamento estejam limpas e padronizadas."""
    if df.empty or coluna not in df.columns:
        return df
    df[coluna] = (
        df[coluna].astype(str)
        .str.strip()
        .str.replace(r'\.0$', '', regex=True)
        .str.replace(r'^0+', '', regex=True)
    )
    return df

def obter_func_segura(func, prefixo, ano, codigo_mun):
    """Garante execução amigável da função de glob sem travar o app."""
    try:
        return func(prefixo, ano, codigo_mun)
    except:
        return []

def gerar_dataframe_detalhado(df_empenhos_filtrados, ano_inicial, ano_final, codigo_mun, obter_caminho_func, carregar_e_filtrar_func):
    """
    Gera o DataFrame detalhado combinando empenhos, liquidações/notas fiscais e pagamentos.
    Apenas cruza dados reais existentes no TCE, sem criar linhas fictícias.
    """
    if df_empenhos_filtrados is None or df_empenhos_filtrados.empty:
        return pd.DataFrame()

    def to_numeric_br(series):
        if pd.api.types.is_numeric_dtype(series):
            return pd.to_numeric(series, errors='coerce').fillna(0.0)
        s = series.astype(str)
        if s.str.contains(',', na=False).any():
            s = s.str.replace('.', '', regex=False).str.replace(',', '.', regex=False)
        return pd.to_numeric(s, errors='coerce').fillna(0.0)

    chaves_base = ['codigo_municipio', 'codigo_orgao', 'numero_empenho']

    def processar_df_ano(df, ano_fallback):
        if df is None or df.empty:
            return None
        for c in chaves_base:
            if c in df.columns:
                df[c] = df[c].astype(str).str.strip().str.replace(r'\.0$', '', regex=True).str.replace(r'^0+', '', regex=True)
        
        if 'exercicio_orcamento' in df.columns:
            df['exercicio_ref'] = df['exercicio_orcamento'].astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
        elif 'exercicio_empenho' in df.columns:
            df['exercicio_ref'] = df['exercicio_empenho'].astype(str).str.strip().str.replace(r'\.0$', '', regex=True)
        elif ano_fallback:
            df['exercicio_ref'] = str(ano_fallback)
        else:
            df['exercicio_ref'] = ""
        return df

    # 1. Carregar bases correlatas em loop
    dfs_nf_acumulados = []
    dfs_pag_acumulados = []

    for ano_corrente in range(int(ano_inicial), int(ano_final) + 1):
        arq_fiscais = obter_func_segura(obter_caminho_func, "notas_fiscais", ano_corrente, codigo_mun)
        if not arq_fiscais:
            arq_fiscais = obter_func_segura(obter_caminho_func, "liquidacoes", ano_corrente, codigo_mun)
            
        arq_pag = obter_func_segura(obter_caminho_func, "notas_pagamentos", ano_corrente, codigo_mun)

        df_nf_ano = processar_df_ano(carregar_e_filtrar_func(arq_fiscais), ano_corrente)
        df_pag_ano = processar_df_ano(carregar_e_filtrar_func(arq_pag), ano_corrente)

        if df_nf_ano is not None and not df_nf_ano.empty:
            dfs_nf_acumulados.append(df_nf_ano)
        if df_pag_ano is not None and not df_pag_ano.empty:
            dfs_pag_acumulados.append(df_pag_ano)

    df_nf = pd.concat(dfs_nf_acumulados, ignore_index=True) if dfs_nf_acumulados else pd.DataFrame()
    df_pag = pd.concat(dfs_pag_acumulados, ignore_index=True) if dfs_pag_acumulados else pd.DataFrame()

    df_empenhos_filtrados = processar_df_ano(df_empenhos_filtrados, None)
    chaves = ['exercicio_ref'] + chaves_base

    # 2. Criar sequenciadores
    if not df_nf.empty:
        cols_sort_nf = [c for c in ['data_liquidacao', 'numero_nota_fiscal'] if c in df_nf.columns]
        if cols_sort_nf: 
            df_nf = df_nf.sort_values(by=chaves + cols_sort_nf)
        df_nf['seq_mov'] = df_nf.groupby(chaves).cumcount()
    else:
        df_nf = pd.DataFrame(columns=chaves + ['seq_mov'])

    if not df_pag.empty:
        cols_sort_pag = [c for c in ['data_nota_pagamento', 'numero_nota_pagamento'] if c in df_pag.columns]
        if cols_sort_pag: 
            df_pag = df_pag.sort_values(by=chaves + cols_sort_pag)
        df_pag['seq_mov'] = df_pag.groupby(chaves).cumcount()
    else:
        df_pag = pd.DataFrame(columns=chaves + ['seq_mov'])

    # 3. Unir movimentos (apenas dados reais do TCE)
    df_movimentos = pd.merge(df_nf, df_pag, on=chaves + ['seq_mov'], how='outer', suffixes=('_nf', '_pag'))

    # 4. Cruzar com os cabeçalhos dos Empenhos
    df_consolidado = pd.merge(df_empenhos_filtrados, df_movimentos, on=chaves, how='left')

    # 5. CÁLCULO DE PRAZOS E ATRASOS
    if 'data_liquidacao' in df_consolidado.columns:
        dt_liqui = pd.to_datetime(df_consolidado['data_liquidacao'], errors='coerce')
    else:
        dt_liqui = pd.Series([pd.NaT] * len(df_consolidado))
        
    if 'data_emissao_empenho' in df_consolidado.columns:
        dt_emissao = pd.to_datetime(df_consolidado['data_emissao_empenho'], errors='coerce')
    else:
        dt_emissao = pd.Series([pd.NaT] * len(df_consolidado))
        
    # Data base: Prioriza liquidação, se não houver, usa a emissão do empenho
    dt_base = dt_liqui.fillna(dt_emissao)
    
    if 'data_nota_pagamento' in df_consolidado.columns:
        dt_pag = pd.to_datetime(df_consolidado['data_nota_pagamento'], errors='coerce')
    else:
        dt_pag = pd.Series([pd.NaT] * len(df_consolidado))
    
    df_consolidado['data_prevista_pagamento'] = (dt_base + pd.Timedelta(days=30)).dt.strftime('%Y-%m-%d')
    df_consolidado['data_prevista_pagamento'] = df_consolidado['data_prevista_pagamento'].fillna('')

    # Status: PAGO se tem data de pagamento, PENDENTE se tem liquidação, EMPENHADO se só tem empenho
    df_consolidado['status_execucao'] = np.where(
        dt_pag.notna(), 
        'PAGO', 
        np.where(dt_base.notna(), 'PENDENTE', 'EMPENHADO')
    )

    # Cálculo de dias de atraso
    hoje = pd.Timestamp.now().normalize()
    prazo_limite = dt_base + pd.Timedelta(days=30)

    atraso_se_pago = (dt_pag - prazo_limite).dt.days
    atraso_se_pendente = (hoje - prazo_limite).dt.days

    df_consolidado['dias_atraso'] = np.where(
        dt_pag.notna(),
        atraso_se_pago,
        np.where(dt_base.notna(), atraso_se_pendente, 0)
    )
    df_consolidado['dias_atraso'] = df_consolidado['dias_atraso'].clip(lower=0).fillna(0).astype(int)

    # 6. MAPEAMENTO DE SINÔNIMOS
    if 'codigo_unidade_orcamentaria' in df_consolidado.columns:
        df_consolidado['codigo_unidade'] = df_consolidado['codigo_unidade_orcamentaria']
    if 'modalidade_nota_empenho' in df_consolidado.columns:
        df_consolidado['modalidade_empenho'] = df_consolidado['modalidade_nota_empenho']
    if 'descricao_historico_empenho' in df_consolidado.columns:
        df_consolidado['descricao_empenho'] = df_consolidado['descricao_historico_empenho']
    if 'data_referencia_doc' in df_consolidado.columns:
        df_consolidado['data_referencia_empenho'] = df_consolidado['data_referencia_doc']

    if 'data_referencia_doc_nf' in df_consolidado.columns:
        df_consolidado['data_referencia_liquidacao'] = df_consolidado['data_referencia_doc_nf']
    elif 'data_referencia_doc' in df_consolidado.columns:
        df_consolidado['data_referencia_liquidacao'] = df_consolidado['data_referencia_doc']

    if 'valor_bruto' in df_consolidado.columns:
        df_consolidado['valor_liquidado'] = to_numeric_br(df_consolidado['valor_bruto'])
    else:
        df_consolidado['valor_liquidado'] = 0.0

    if 'data_referencia_pag' in df_consolidado.columns:
        df_consolidado['data_referencia'] = df_consolidado['data_referencia_pag']
    if 'numero_documento_caixa' in df_consolidado.columns:
        df_consolidado['nu_documento_caixa'] = df_consolidado['numero_documento_caixa']

    # 7. Estrutura rígida de saída
    colunas_solicitadas = [
        'codigo_municipio', 'exercicio_orcamento', 'codigo_orgao', 'codigo_unidade',
        'numero_empenho', 'data_emissao_empenho', 'data_referencia_empenho', 'modalidade_empenho', 
        'codigo_elemento_despesa', 'nome_negociante', 'descricao_empenho',
        'valor_anterior_saldo_dotacao', 'valor_empenhado', 'valor_atual_saldo_dotacao',
        'data_liquidacao', 'data_referencia_liquidacao', 'tipo_nota_fiscal', 'numero_nota_fiscal', 
        'data_emissao', 'valor_bruto', 'valor_desconto', 'valor_liquido', 'valor_liquidado', 
        'valor_base_calculo_iss', 'valor_aliquota_iss',
        'numero_nota_pagamento', 'data_nota_pagamento', 'data_referencia', 'nu_documento_caixa', 
        'valor_nota_pagamento', 'valor_empenhado_a_pagar',
        'status_execucao', 'data_prevista_pagamento', 'dias_atraso'
    ]
    
    for col in colunas_solicitadas:
        if col not in df_consolidado.columns:
            df_consolidado[col] = None

    if 'exercicio_ref' in df_consolidado.columns:
        df_consolidado = df_consolidado.drop(columns=['exercicio_ref'])
    if 'seq_mov' in df_consolidado.columns:
        df_consolidado = df_consolidado.drop(columns=['seq_mov'])

    return df_consolidado[colunas_solicitadas]


def renderizar_botoes_exportacao(
    df_empenhos_filtrados,
    ano_inicial,
    ano_final,
    codigo_mun,
    obter_caminho_func,
    carregar_e_filtrar_func
):
    """Gera a interface visual dos botões lado a lado no Streamlit."""
    if df_empenhos_filtrados.empty:
        return

    col_json, col_xlsx = st.columns(2)

    with col_json:
        try:
            df_json_limpo = df_empenhos_filtrados.copy()
            colunas_remover = ['chave_composta', 'match_reason', 'status_pagamento']
            df_json_limpo = df_json_limpo.drop(columns=[c for c in colunas_remover if c in df_json_limpo.columns])
            
            json_string = df_json_limpo.to_json(orient="records", indent=4, force_ascii=False)
            st.download_button(
                label="📦 Exportar Grid em JSON",
                data=json_string,
                file_name=f"empenhos_export_{codigo_mun}_{ano_inicial}_a_{ano_final}.json",
                mime="application/json",
                use_container_width=True
            )
        except Exception as e:
            st.error(f"Erro ao gerar JSON: {e}")

    with col_xlsx:
        try:
            with st.spinner("Compilando e cruzando dados relacionais da planilha..."):
                df_detalhado = gerar_dataframe_detalhado(
                    df_empenhos_filtrados, ano_inicial, ano_final, codigo_mun, 
                    obter_caminho_func, carregar_e_filtrar_func
                )
                df_detalhado = df_detalhado.fillna('')

                df_colorido = df_detalhado.style.set_properties(
                    **{'background-color': '#FFF2CC'}, 
                    subset=['numero_nota_pagamento', 'data_nota_pagamento', 'valor_nota_pagamento']
                ).set_properties(
                    **{'background-color': '#FCE4D6'}, 
                    subset=['codigo_municipio', 'codigo_orgao', 'codigo_unidade', 'numero_empenho', 'codigo_elemento_despesa', 'valor_empenhado', 'nome_negociante', 'valor_empenhado_a_pagar']
                ).set_properties(
                    **{'background-color': '#DDEBF7'}, 
                    subset=['numero_nota_pagamento', 'nu_documento_caixa']
                ).set_properties(
                    **{'background-color': '#E2EFDA'}, 
                    subset=['data_liquidacao', 'valor_liquidado']
                )
                
                excel_buffer = io.BytesIO()
                with pd.ExcelWriter(excel_buffer, engine='openpyxl') as writer:
                    df_colorido.to_excel(writer, index=False, sheet_name='Auditoria_TCE')
                
                st.download_button(
                    label="📊 Exportar Excel Detalhado (Relacional)",
                    data=excel_buffer.getvalue(),
                    file_name=f"auditoria_empenhos_{codigo_mun}_{ano_inicial}_a_{ano_final}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )
        except Exception as e:
            st.error(f"Erro ao gerar Excel Detalhado: {e}")