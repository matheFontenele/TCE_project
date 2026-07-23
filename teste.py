import streamlit as st
import pandas as pd
import duckdb
import os
import glob

# ==============================================================================
# FUNÇÕES AUXILIARES
# ==============================================================================
def obter_caminho_arquivos_modal(prefixo, ano, codigo_mun):
    """Busca arquivos Parquet no disco (usado pelo modal e pelos exportadores)."""
    if codigo_mun == "Todos":
        return sorted(glob.glob(f"data/{prefixo}_{ano}_*.parquet") + glob.glob(f"data/{prefixo}_{ano}.parquet"))
    mensais = glob.glob(f"data/{prefixo}_{ano}_*_{codigo_mun}.parquet")
    anuais = glob.glob(f"data/{prefixo}_{ano}_{codigo_mun}.parquet")
    return sorted(mensais + anuais)

def normalizar_chave(valor):
    """Limpa a chave para o Sniper Duplo do DuckDB (Remove .0 e zeros à esquerda)."""
    if pd.isna(valor) or valor is None: return ""
    v = str(valor).strip()
    import re
    v = re.sub(r'\.0$', '', v)
    v = re.sub(r'^0+', '', v)
    return v

def formatar_moeda_modal(valor):
    try:
        if pd.isna(valor) or valor is None: return "0,00"
        return f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except: return "0,00"

def formatar_data_modal(data_raw):
    try:
        if pd.isna(data_raw) or not data_raw or str(data_raw).strip() == "": return "Não Informada"
        dt_str = str(data_raw).split('T')[0].strip()
        if '-' in dt_str:
            p = dt_str.split('-')
            if len(p) == 3: return f"{p[2]}/{p[1]}/{p[0]}"
        return dt_str
    except: return "Não Informada"

# ==============================================================================
# O COMPONENTE MODAL PRINCIPAL
# ==============================================================================
@st.dialog("🔎 Visão Detalhada do Registro", width="large")
def exibir_modal_detalhes(row, categoria, ano, codigo_mun, config_atual, id_unico=None):
    
    # CSS Embutido para os cards do modal
    st.markdown("""
    <style>
        .modal-header-box { background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 16px; margin-bottom: 20px; }
        .modal-title { font-size: 1.4rem; font-weight: 800; color: #0f172a; margin-bottom: 8px; }
        .modal-subtitle { font-size: 0.9rem; color: #64748b; font-weight: 600; }
        .fin-grid { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 15px; }
        .fin-box { flex: 1; min-width: 150px; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px; text-align: center; }
        .fin-label { font-size: 0.7rem; font-weight: 700; color: #64748b; text-transform: uppercase; margin-bottom: 4px; }
        .fin-value { font-size: 1.1rem; font-weight: 800; font-family: monospace; }
        .val-green { color: #10b981; } .val-blue { color: #3b82f6; } .val-dark { color: #334155; }
    </style>
    """, unsafe_allow_html=True)

    layout = config_atual.get("layout_card", {})
    dicionario_nomes = config_atual.get("colunas_texto", {})

    # 1. LEITURA DOS SLOTS DO JSON
    col_topo = layout.get("topo_esq", "")
    col_titulo = layout.get("titulo_grande", "")
    col_meta1 = layout.get("meta_1", "")
    col_meta2 = layout.get("meta_2", "")

    # Mapeamento e fallback visual
    lbl_topo = dicionario_nomes.get(col_topo, col_topo).replace('_', ' ').title()
    lbl_titulo = dicionario_nomes.get(col_titulo, col_titulo).replace('_', ' ').title()
    
    val_topo = row.get(col_topo, "N/A")
    val_titulo = row.get(col_titulo, "Não informado")
    val_meta1 = row.get(col_meta1, "")
    val_meta2 = formatar_data_modal(row.get(col_meta2, ""))

    # ==============================================================================
    # 🏛️ CABEÇALHO DO MODAL
    # ==============================================================================
    st.markdown(f"""
        <div class="modal-header-box">
            <div style="font-size: 0.75rem; font-weight: 700; color: #ff4b4b; text-transform: uppercase; margin-bottom: 4px;">
                {lbl_topo}: {val_topo}
            </div>
            <div class="modal-title">{val_titulo}</div>
            <div class="modal-subtitle">📍 {val_meta1} &nbsp;&bull;&nbsp; 📅 {val_meta2}</div>
        </div>
    """, unsafe_allow_html=True)

    # ==============================================================================
    # 📜 DESCRIÇÃO DINÂMICA (Suporta Texto Único ou Múltiplas Colunas como Veículos)
    # ==============================================================================
    desc_key = layout.get("corpo_texto", "")
    
    if isinstance(desc_key, list):
        partes_modal = [str(row.get(c, "")).strip() for c in desc_key if c in row and not pd.isna(row[c]) and str(row[c]).strip()]
        if partes_modal:
            st.markdown(f"#### 📜 Especificações / Detalhes")
            st.info(" — ".join(partes_modal))
            st.divider()
            
    elif desc_key and desc_key in row and not pd.isna(row[desc_key]):
        label_desc = dicionario_nomes.get(desc_key, desc_key).replace('_', ' ').title()
        st.markdown(f"#### 📜 {label_desc}") 
        st.info(row[desc_key])
        st.divider()

    # ==============================================================================
    # 💰 GRID FINANCEIRO DINÂMICO
    # ==============================================================================
    grid_fin = layout.get("grid_financeiro", [])
    if grid_fin:
        html_boxes = ""
        for item in grid_fin:
            col_alvo = item.get("coluna")
            if col_alvo in row and not pd.isna(row[col_alvo]):
                valor_formatado = formatar_moeda_modal(row[col_alvo])
                lbl = item.get("label_caixa", "Valor")
                
                cor_classe = "val-dark"
                if any(w in col_alvo.lower() for w in ['pago', 'pagamento']): cor_classe = "val-green"
                elif any(w in col_alvo.lower() for w in ['liquidado']): cor_classe = "val-blue"
                
                prefixo = "" if any(w in col_alvo.lower() for w in ['quantidade', 'qtd']) else "R$ "
                
                html_boxes += f"""
                <div class="fin-box">
                    <div class="fin-label">{lbl}</div>
                    <div class="fin-value {cor_classe}">{prefixo}{valor_formatado}</div>
                </div>
                """
        if html_boxes:
            st.markdown(f'<div class="fin-grid">{html_boxes}</div>', unsafe_allow_html=True)
            st.markdown("<br>", unsafe_allow_html=True)

    # ==============================================================================
    # 🔗 RELACIONAMENTOS (DuckDB Otimizado com Camaleão e Exterminador V3)
    # ==============================================================================
    relacionamentos = config_atual.get("relacionamentos", [])
    
    if relacionamentos:
        st.markdown("### 📊 Relacionamentos de Vínculos")
        abas = st.tabs([r["titulo_aba"] for r in relacionamentos])

        for index, regra in enumerate(relacionamentos):
            with abas[index]:
                tabela_alvo = regra["tabela_alvo"]
                chaves_join = regra["chaves_join"]
                
                arquivos_alvo = obter_caminho_arquivos_modal(tabela_alvo, ano, codigo_mun)
                arquivos_alvo_validos = [f for f in arquivos_alvo if os.path.exists(f)]

                if not arquivos_alvo_validos:
                    st.info("Nenhum registro vinculado.")
                    continue
                
                # --- 1. TRADUTOR CAMALEÃO (Descobre as colunas reais da tabela alvo) ---
                try:
                    df_cols = duckdb.query(f"DESCRIBE SELECT * FROM read_parquet('{arquivos_alvo_validos[0]}')").df()
                    colunas_alvo = df_cols['column_name'].tolist()
                except:
                    colunas_alvo = []

                # --- 2. MONTAGEM DAS REGRAS DE JOIN ---
                clausulas_where = []
                for chave in chaves_join:
                    valor_linha_limpo = normalizar_chave(row.get(chave))
                    
                    if not valor_linha_limpo:
                        continue
                        
                    # O Camaleão entra em ação: traduz divergências de nomes de colunas
                    chave_alvo = chave
                    if colunas_alvo and chave_alvo not in colunas_alvo:
                        if chave == 'numero_nota_empenho' and 'numero_empenho' in colunas_alvo:
                            chave_alvo = 'numero_empenho'
                        elif chave == 'numero_empenho' and 'numero_nota_empenho' in colunas_alvo:
                            chave_alvo = 'numero_nota_empenho'
                            
                    clausulas_where.append(f"""(
                        regexp_replace(CAST({chave_alvo} AS VARCHAR), '\.0$', '') = '{valor_linha_limpo}' 
                        OR TRY_CAST(regexp_replace(CAST({chave_alvo} AS VARCHAR), '\.0$', '') AS BIGINT) = TRY_CAST('{valor_linha_limpo}' AS BIGINT)
                    )""")

                # --- 3. O EXTERMINADOR DE FANTASMAS V3 (Travas Institucionais) ---
                if colunas_alvo:
                    # Trava 1: Ano
                    if 'exercicio_orcamento' in colunas_alvo and 'exercicio_orcamento' in row and not pd.isna(row['exercicio_orcamento']):
                        valor_ano = normalizar_chave(row['exercicio_orcamento'])
                        if valor_ano: clausulas_where.append(f"(CAST(exercicio_orcamento AS VARCHAR) = '{valor_ano}')")
                        
                    # Trava 2: Órgão (Sniper Duplo)
                    if 'codigo_orgao' in colunas_alvo and 'codigo_orgao' in row and not pd.isna(row['codigo_orgao']):
                        valor_orgao = normalizar_chave(row['codigo_orgao'])
                        if valor_orgao: clausulas_where.append(f"""(
                            regexp_replace(CAST(codigo_orgao AS VARCHAR), '\.0$', '') = '{valor_orgao}' 
                            OR TRY_CAST(regexp_replace(CAST(codigo_orgao AS VARCHAR), '\.0$', '') AS BIGINT) = TRY_CAST('{valor_orgao}' AS BIGINT)
                        )""")
                        
                    # Trava 3: Unidade Orçamentária (Sniper Duplo)
                    if 'codigo_unidade_orcamentaria' in colunas_alvo and 'codigo_unidade_orcamentaria' in row and not pd.isna(row['codigo_unidade_orcamentaria']):
                        valor_uo = normalizar_chave(row['codigo_unidade_orcamentaria'])
                        if valor_uo: clausulas_where.append(f"""(
                            regexp_replace(CAST(codigo_unidade_orcamentaria AS VARCHAR), '\.0$', '') = '{valor_uo}' 
                            OR TRY_CAST(regexp_replace(CAST(codigo_unidade_orcamentaria AS VARCHAR), '\.0$', '') AS BIGINT) = TRY_CAST('{valor_uo}' AS BIGINT)
                        )""")

                if not clausulas_where:
                    st.info("Chaves de cruzamento insuficientes.")
                    continue

                # --- 4. EXECUÇÃO DA QUERY DUCKDB ---
                where_sql = " AND ".join(clausulas_where)
                files_str = "[" + ", ".join([f"'{f}'" for f in arquivos_alvo_validos]) + "]"
                query = f"SELECT * FROM read_parquet({files_str}, union_by_name=True) WHERE {where_sql}"
                
                try:
                    df_relacionado = duckdb.query(query).df()
                    
                    if not df_relacionado.empty:
                        cols_exibir = [c for c in regra["colunas_exibir"] if c in df_relacionado.columns]
                        df_display = df_relacionado[cols_exibir].copy()
                        
                        # Formata as colunas numéricas de valores para Reais
                        for col in df_display.columns:
                            if 'valor' in col.lower():
                                df_display[col] = df_display[col].apply(lambda x: f"R$ {formatar_moeda_modal(x)}")
                        
                        st.dataframe(df_display, use_container_width=True, hide_index=True)
                    else:
                        st.info("Nenhum registro vinculado.")
                except Exception as e:
                    st.error(f"Erro SQL ao cruzar dados: {e}")

    st.divider()
    with st.expander("🛠️ Ver Todos os Dados Detalhados (Tabela Completa)"):
        st.json(row.to_dict())