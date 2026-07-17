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

def obter_caminho_arquivos_modal(prefixo, ano, codigo_mun):
    if codigo_mun == "Todos":
        return sorted(glob.glob(f"data/{prefixo}_{ano}_*.parquet") + glob.glob(f"data/{prefixo}_{ano}.parquet"))
    mensais = glob.glob(f"data/{prefixo}_{ano}_*_{codigo_mun}.parquet")
    anuais = glob.glob(f"data/{prefixo}_{ano}_{codigo_mun}.parquet")
    return sorted(mensais + anuais)

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
    
    for k in ['numero_nota_fiscal', 'numero_empenho', 'numero_nota_pagamento', 'numero_contrato', 'numero_tombamento']:
        if k in row and not pd.isna(row[k]): infos["numero"] = str(row[k]).replace('.0', ''); break
    for k in ['data_emissao', 'data_emissao_empenho', 'data_nota_pagamento', 'data_aquisicao', 'data_incorporacao']:
        if k in row and not pd.isna(row[k]): infos["data"] = row[k]; break
    for k in ['valor_liquido', 'valor_bruto', 'valor_empenhado', 'valor_nota_pagamento', 'valor_aquisicao', 'valor_total_item']:
        if k in row and not pd.isna(row[k]): infos["valor"] = float(row[k]); break
    for k in ['nome_negociante', 'nome_responsavel_pagamento', 'nome_credor', 'nome_proprietario']:
        if k in row and not pd.isna(row[k]) and str(row[k]).strip(): infos["fornecedor"] = str(row[k]); break
    for k in ['numero_documento_negociante', 'cpf_responsavel_pagamento', 'cpf_cnpj_emitente']:
        if k in row and not pd.isna(row[k]) and str(row[k]).strip(): infos["doc_fornecedor"] = str(row[k]); break
            
    return infos

# ==============================================================================
# DIALOG (MODAL) UNIVERSAL COM DUCKDB DINÂMICO
# ==============================================================================
@st.dialog("📋 Detalhes do Documento", width="large")
def exibir_modal_detalhes(row, categoria, ano, codigo_mun, config_atual=None, id_unico=None):
    
    # Validação de segurança
    if config_atual is None: config_atual = {}

    infos = extrair_informacoes_principais(row)
    
    cod_municipio = str(row.get('codigo_municipio', '')).strip()
    cod_orgao = str(row.get('codigo_orgao', '')).strip()
    cod_unidade = str(row.get('codigo_unidade_orcamentaria', '')).strip()
    
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

    # Histórico Flexível (Mapeado pelo JSON)
    desc_key = config_atual.get("layout_card", {}).get("corpo_texto", "")
    if desc_key and desc_key in row and not pd.isna(row[desc_key]):
        st.markdown("#### 📜 Descrição")
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
                
                arquivos_alvo = obter_caminho_arquivos_modal(prefixo_alvo, ano, codigo_mun)
                arquivos_alvo_validos = [f for f in arquivos_alvo if os.path.exists(f)]
                
                if not arquivos_alvo_validos:
                    st.caption("Bases não baixadas.")
                    continue

                clausulas_where = []
                for chave in chaves_join:
                    valor_linha_limpo = normalizar_chave(row[chave])
                    
                    # TÁTICA "SNIPER DUPLO":
                    # 1. Compara como texto (Ignora '.0' do Parquet e junta letras se houver)
                    # 2. Compara como BIGINT (Resolve o problema do Pandas remover os Zeros à esquerda ex: '074' vira '74')
                    clausulas_where.append(f"""(
                        regexp_replace(CAST({chave} AS VARCHAR), '\.0$', '') = '{valor_linha_limpo}' 
                        OR TRY_CAST(regexp_replace(CAST({chave} AS VARCHAR), '\.0$', '') AS BIGINT) = TRY_CAST('{valor_linha_limpo}' AS BIGINT)
                    )""")
                
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

    # Expander de Dados Brutos (Movido para o final da tela)
    with st.expander("🛠️ Ver Todos os Dados Extraídos", expanded=False):
        dict_row = dict(row)
        chaves_relevantes = {k: v for k, v in dict_row.items() if not pd.isna(v) and str(v).strip() != "" and not k.startswith('_')}
        
        html_table = "<table style='width:100%; font-size:0.85rem; border-collapse: collapse;'>"
        for k, v in chaves_relevantes.items():
            val_formatado = str(v)
            if 'valor' in k.lower() and isinstance(v, (int, float)): val_formatado = f"R$ {formatar_moeda_modal(v)}"
            elif 'data' in k.lower(): val_formatado = formatar_data_modal(v)
            html_table += f"<tr style='border-bottom: 1px solid #ddd;'><td style='padding:8px; font-weight:600; color:#555;'>{k}</td><td style='padding:8px;'>{val_formatado}</td></tr>"
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