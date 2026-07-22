import streamlit as st
import pandas as pd
import glob
import re
import requests
import os
import duckdb
from datetime import datetime

# IMPORTANDO O GERADOR DE PDF ISOLADO
from modules.gerador_pdf import gerar_pdf_empenho

# ==============================================================================
# FUNÇÕES AUXILIARES DO MODAL
# ==============================================================================
def formatar_moeda_modal(valor):
    try:
        if pd.isna(valor) or valor is None: return "0,00"
        return f"{float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except: return "0,00"

def formatar_data_modal(data_raw):
    try:
        if pd.isna(data_raw) or data_raw is None or str(data_raw).strip() == "": return "Não Informada"
        if isinstance(data_raw, (datetime, pd.Timestamp)): return data_raw.strftime('%d/%m/%Y')
        data_str = str(data_raw).split('T')[0]
        p = data_str.split('-')
        if len(p) == 3: return f"{p[2]}/{p[1]}/{p[0]}"
        return data_str
    except: return "Não Informada"

def obter_caminho_arquivos_modal(prefixo, ano_base, codigo_mun):
    """
    Busca os arquivos filtrando o passado. Mantém o ano base e anos futuros (Restos a Pagar).
    """
    if codigo_mun == "Todos":
        arquivos = glob.glob(f"data/{prefixo}_*.parquet")
    else:
        arquivos = glob.glob(f"data/{prefixo}_*_{codigo_mun}.parquet")
        
    arquivos_validos = []
    for arq in arquivos:
        match = re.search(r'_(\d{4})(?:_|\.)', arq)
        if match:
            ano_arquivo = int(match.group(1))
            if ano_arquivo >= int(ano_base):
                arquivos_validos.append(arq)
        else:
            arquivos_validos.append(arq)
            
    return sorted(list(set(arquivos_validos)))# O 'set' remove duplicatas caso a busca cruze nomes

def normalizar_chave(valor):
    if pd.isna(valor) or valor is None: return ""
    return re.sub(r'\.0$', '', str(valor).strip())

@st.cache_data(show_spinner=False)
def buscar_nome_orgao_api(codigo_municipio: str, ano_exercicio: str, codigo_orgao: str) -> str:
    cod_mun_fmt = str(codigo_municipio).zfill(3)
    exercicio_fmt = f"{str(ano_exercicio)[:4]}00"
    cod_org_fmt = str(codigo_orgao).zfill(2)
    url = "https://api-dados-abertos.tce.ce.gov.br/sim/orgaos"
    params = {"codigo_municipio": cod_mun_fmt, "exercicio_orcamento": exercicio_fmt, "$format": "json"}
    try:
        response = requests.get(url, params=params, timeout=5)
        if response.status_code == 200:
            for elemento in response.json().get("elements", []):
                if str(elemento.get("codigo_orgao", "")).zfill(2) == cod_org_fmt:
                    return elemento.get("nome_orgao", "").strip()
    except: pass
    return ""

def extrair_informacoes_principais(row):
    infos = {"numero": "Não Informado", "data": "Não Informada", "valor": 0.0, "fornecedor": "Não Informado", "doc_fornecedor": "Ocultado"}

    #Numero de documentos
    for k in ['numero_nota_fiscal', 'numero_empenho', 'numero_nota_pagamento', 'numero_contrato', 'numero_tombamento', 'numero_documento_despesa_extra', 'codigo_renavam']:
        if k in row and not pd.isna(row[k]): infos["numero"] = str(row[k]).replace('.0', ''); break

    #Datas
    for k in ['data_emissao', 'data_emissao_empenho', 'data_nota_pagamento', 'data_aquisicao', 'data_incorporacao', 'data_emissao_despesa_extra', 'data_inclusao_uo']:
        if k in row and not pd.isna(row[k]): infos["data"] = row[k]; break

    #Valores
    for k in ['valor_liquido', 'valor_bruto', 'valor_empenhado', 'valor_nota_pagamento', 'valor_aquisicao', 'valor_total_item', 'valor_documento_despesa_extra', 'valor_liquidado', 'valor_total_abastecimento', 'valor_total_servico', 'nome_responsavel_manutencao']:
        if k in row and not pd.isna(row[k]): infos["valor"] = float(row[k]); break

    #Nomes
    for k in ['nome_negociante', 'nome_responsavel_pagamento', 'nome_credor', 'nome_proprietario', 'nome_credor_despesa_extra', 'nome_fornecedor', 'nome_razao_prestador']:
        if k in row and not pd.isna(row[k]) and str(row[k]).strip(): infos["fornecedor"] = str(row[k]); break

    #Informações numericas extras
    for k in ['numero_documento_negociante', 'cpf_responsavel_pagamento', 'cpf_cnpj_emitente', 'dados_documento_fornecedor']:
        if k in row and not pd.isna(row[k]) and str(row[k]).strip(): infos["doc_fornecedor"] = str(row[k]); break
            
    return infos

# ==============================================================================
# DIALOG (MODAL) UNIVERSAL COM DUCKDB DINÂMICO
# ==============================================================================
@st.dialog("📋 Detalhes do Documento", width="large")
def exibir_modal_detalhes(row, categoria, ano, codigo_mun, config_atual=None, id_unico=None):
    
    # Validação de segurança e extração do dicionário
    if config_atual is None: config_atual = {}
    dicionario_nomes = config_atual.get("colunas_texto", {}) # Puxa o tradutor do JSON
    layout = config_atual.get("layout_card", {})

    infos = extrair_informacoes_principais(row)
    
    cod_municipio = str(row.get('codigo_municipio', '')).strip()
    cod_orgao = str(row.get('codigo_orgao', '')).strip()
    cod_unidade = str(row.get('codigo_unidade_orcamentaria', '')).strip()
    
    ano_real_doc = str(row.get('exercicio_orcamento', ano))[:4]
    
    st.write(f"### {categoria.upper()} Nº {infos['numero']}")
    municipio_nome = row.get('municipio_referencia', 'Não Informado')
    exercicio = str(row.get('exercicio_orcamento', ano))[:4]
    st.caption(f"📍 {municipio_nome} — Exercício: {exercicio}")
    st.divider()

    st.markdown("#### 📄 Informações Principais")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown(f"**Número do Documento:** `{infos['numero']}`")
        st.markdown(f"**Data de Registro:** {formatar_data_modal(infos['data'])}")
        st.markdown(f"**Valor:** R$ {formatar_moeda_modal(infos['valor'])}")

    with col2:
        st.markdown(f"**Responsável / Fornecedor:** {infos['fornecedor']}")
        st.markdown(f"**CNPJ/CPF:** `{infos['doc_fornecedor']}`")
        
    st.divider()

    # Informações Orçamentárias
    if cod_orgao and cod_municipio:
        st.markdown("#### 🏛️ Informações de Orçamento")
        nome_orgao = buscar_nome_orgao_api(cod_municipio, exercicio, cod_orgao)
        col1, col2 = st.columns(2)
        with col1: st.markdown(f"**Unidade Gestora:** {municipio_nome} - U.O. {cod_unidade}")
        with col2: st.markdown(f"**Órgão:** `{cod_orgao}` — {nome_orgao if nome_orgao else 'Não identificado'}")
        st.divider()

    # ==============================================================================
    # 📜 DESCRIÇÃO DINÂMICA (Traduzida)
    # ==============================================================================
    desc_key = layout.get("corpo_texto", "")
    if isinstance(desc_key, list):
        # Múltiplas colunas (Ex: Frota)
        partes_modal = [str(row.get(c, "")).strip() for c in desc_key if c in row and not pd.isna(row[c]) and str(row[c]).strip()]
        if partes_modal:
            st.markdown(f"#### 📜 Especificações / Detalhes")
            st.info(" — ".join(partes_modal))
            st.divider()
            
    elif desc_key and desc_key in row and not pd.isna(row[desc_key]):
        # Coluna única (Ex: Empenho)
        label_desc = dicionario_nomes.get(desc_key, desc_key).replace('_', ' ').title()
        st.markdown(f"#### 📜 {label_desc}") 
        st.info(row[desc_key])
        st.divider()

    # ==============================================================================
    # MOTOR DE RASTREABILIDADE DINÂMICO (DUCKDB + JSON)
    # ==============================================================================
    relacionamentos = config_atual.get("relacionamentos", [])
    dfs_relacionados = {}

    if relacionamentos:
        st.markdown(f"##### 📊 Relacionamentos de Vínculos")
        
        cols = st.columns(len(relacionamentos))
        
        for index, regra in enumerate(relacionamentos):
            with cols[index]:
                st.markdown(f"**{regra['titulo_secao']}**")
                
                prefixo_alvo = regra['tabela_alvo_prefixo']
                chaves_join = regra['chaves_join']
                
                chaves_faltando = [k for k in chaves_join if k not in row or pd.isna(row[k]) or str(row[k]).strip() == ""]
                
                if chaves_faltando:
                    st.caption("Sem dados.")
                    continue
                
                arquivos_alvo = obter_caminho_arquivos_modal(prefixo_alvo, ano_real_doc, codigo_mun)
                arquivos_alvo_validos = [f for f in arquivos_alvo if os.path.exists(f)]
                
                if not arquivos_alvo_validos:
                    st.caption("Bases não baixadas.")
                    continue

                clausulas_where = []
                for chave in chaves_join:
                    valor_linha_limpo = normalizar_chave(row[chave])
                    clausulas_where.append(f"""(
                        regexp_replace(CAST({chave} AS VARCHAR), '\.0$', '') = '{valor_linha_limpo}' 
                        OR TRY_CAST(regexp_replace(CAST({chave} AS VARCHAR), '\.0$', '') AS BIGINT) = TRY_CAST('{valor_linha_limpo}' AS BIGINT)
                    )""")
                
                try:
                    df_cols = duckdb.query(f"DESCRIBE SELECT * FROM read_parquet('{arquivos_alvo_validos[0]}')").df()
                    colunas_alvo = df_cols['column_name'].tolist()
                except:
                    colunas_alvo = []

                # =====================================================================
                # 2. MONTAGEM DAS REGRAS DE JOIN (Com tradução automática)
                # =====================================================================
                clausulas_where = []
                for chave in chaves_join:
                    valor_linha_limpo = normalizar_chave(row[chave])
                    
                    # O Camaleão entra em ação: se a chave original não existir no alvo, traduz.
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

                # =====================================================================
                # 3. O EXTERMINADOR DE FANTASMAS (Agora mais rápido)
                # =====================================================================
                if colunas_alvo:
                    # Trava 1: Ano (Exercicio)
                    if 'exercicio_orcamento' in colunas_alvo and 'exercicio_orcamento' in row and not pd.isna(row['exercicio_orcamento']):
                        valor_ano = normalizar_chave(row['exercicio_orcamento'])
                        if valor_ano:
                            clausulas_where.append(f"(CAST(exercicio_orcamento AS VARCHAR) = '{valor_ano}')")
                        
                    # Trava 2: Código do Órgão (Sniper Duplo)
                    if 'codigo_orgao' in colunas_alvo and 'codigo_orgao' in row and not pd.isna(row['codigo_orgao']):
                        valor_orgao = normalizar_chave(row['codigo_orgao'])
                        if valor_orgao:
                            clausulas_where.append(f"""(
                                regexp_replace(CAST(codigo_orgao AS VARCHAR), '\.0$', '') = '{valor_orgao}' 
                                OR TRY_CAST(regexp_replace(CAST(codigo_orgao AS VARCHAR), '\.0$', '') AS BIGINT) = TRY_CAST('{valor_orgao}' AS BIGINT)
                            )""")
                        
                    # Trava 3: Código da Unidade Orçamentária (Sniper Duplo)
                    if 'codigo_unidade_orcamentaria' in colunas_alvo and 'codigo_unidade_orcamentaria' in row and not pd.isna(row['codigo_unidade_orcamentaria']):
                        valor_uo = normalizar_chave(row['codigo_unidade_orcamentaria'])
                        if valor_uo:
                            clausulas_where.append(f"""(
                                regexp_replace(CAST(codigo_unidade_orcamentaria AS VARCHAR), '\.0$', '') = '{valor_uo}' 
                                OR TRY_CAST(regexp_replace(CAST(codigo_unidade_orcamentaria AS VARCHAR), '\.0$', '') AS BIGINT) = TRY_CAST('{valor_uo}' AS BIGINT)
                            )""")
                
                # Junta todas as regras e executa!
                where_sql = " AND ".join(clausulas_where)
                files_str = "[" + ", ".join([f"'{f}'" for f in arquivos_alvo_validos]) + "]"

                query = f"SELECT * FROM read_parquet({files_str}, union_by_name=True) WHERE {where_sql}"
                
                try:
                    df_resultado = duckdb.query(query).df()
                    dfs_relacionados[prefixo_alvo] = df_resultado
                    
                    if df_resultado.empty:
                        st.caption("Nenhum registro vinculado.")
                    else:
                        df_exibir = pd.DataFrame()
                        soma_total = 0.0
                        
                        for col_config in regra.get("colunas_exibicao", []):
                            nome_exibicao = col_config["label"]
                            campo_original = col_config["campo"]
                            tipo = col_config["tipo"]
                            
                            if campo_original in df_resultado.columns:
                                if tipo == "data":
                                    df_exibir[nome_exibicao] = df_resultado[campo_original].apply(formatar_data_modal)
                                elif tipo == "moeda":
                                    df_resultado[campo_original] = pd.to_numeric(df_resultado[campo_original], errors='coerce').fillna(0)
                                    df_exibir[nome_exibicao] = df_resultado[campo_original].apply(lambda x: formatar_moeda_modal(x))
                                    soma_total += df_resultado[campo_original].sum()
                                else:
                                    df_exibir[nome_exibicao] = df_resultado[campo_original].astype(str).str.replace('.0', '', regex=False)
                        
                        st.dataframe(df_exibir, use_container_width=True, hide_index=True)
                        
                        if soma_total > 0:
                            st.markdown(f"<div style='font-size: 0.85rem; color: #888;'>Soma: <b>R$ {formatar_moeda_modal(soma_total)}</b></div>", unsafe_allow_html=True)

                except Exception as e:
                    st.error(f"Erro SQL: {e}")
        st.divider()

    # ==============================================================================
    # TABELA DE DADOS BRUTOS (Com Tradutor de Nomes Ativado)
    # ==============================================================================
    with st.expander("🛠️ Ver Todos os Dados Detalhados", expanded=False):
        dict_row = dict(row)
        chaves_relevantes = {k: v for k, v in dict_row.items() if not pd.isna(v) and str(v).strip() != "" and not k.startswith('_')}
        
        html_table = "<table style='width:100%; font-size:0.85rem; border-collapse: collapse;'>"
        for k, v in chaves_relevantes.items():
            val_formatado = str(v)
            if 'valor' in k.lower() and isinstance(v, (int, float)): val_formatado = f"R$ {formatar_moeda_modal(v)}"
            elif 'data' in k.lower(): val_formatado = formatar_data_modal(v)
            
            # --- MÁGICA DA TRADUÇÃO AQUI ---
            # Busca o nome bonito no dicionário. Se a coluna for genérica (ex: 'data_referencia'), 
            # ele limpa os underscores automaticamente.
            nome_amigavel = dicionario_nomes.get(k, k).replace("_", " ").title()
            
            html_table += f"<tr style='border-bottom: 1px solid #ddd;'><td style='padding:8px; font-weight:600; color:#555;'>{nome_amigavel}</td><td style='padding:8px;'>{val_formatado}</td></tr>"
        html_table += "</table>"
        st.markdown(html_table, unsafe_allow_html=True)

    # ==============================================================================
    # BOTÃO PDF
    # ==============================================================================
    if categoria == "Notas de Empenho":
        st.divider()
        try:
            df_liq_para_pdf = dfs_relacionados.get("liquidacoes", None)
            df_pag_para_pdf = dfs_relacionados.get("notas_pagamentos", None)
            
            pdf_data = gerar_pdf_empenho(
                row, 
                df_liq_filtrada=df_liq_para_pdf if df_liq_para_pdf is not None and not df_liq_para_pdf.empty else None, 
                df_pag_filtrado=df_pag_para_pdf if df_pag_para_pdf is not None and not df_pag_para_pdf.empty else None
            )
            st.download_button(
                label="🖨️ Imprimir Dossiê Completo (PDF)",
                data=pdf_data,
                file_name=f"Documento_{infos['numero']}.pdf",
                mime="application/pdf",
                use_container_width=True
            )
        except Exception as e:
            st.error(f"❌ Erro ao gerar PDF: {str(e)}")