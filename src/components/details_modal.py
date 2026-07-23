# src/components/details_modal.py
import streamlit as st
import pandas as pd
import glob
import re
import requests
import os
from datetime import datetime
from src.modules.motor_relacional import obter_dados_relacionados
from modules.gerador_pdf import gerar_pdf_empenho
from src.modules.data_utils import formatar_moeda, formatar_data

# ==============================================================================
# FUNÇÕES AUXILIARES DO MODAL
# ==============================================================================
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
            
    return sorted(list(set(arquivos_validos))) 

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

# ==============================================================================
# DIALOG (MODAL) UNIVERSAL 100% DATA-DRIVEN
# ==============================================================================
@st.dialog("📋 Detalhes do Documento", width="large")
def exibir_modal_detalhes(row, categoria, ano, codigo_mun, config_atual=None, id_unico=None):
    
    if config_atual is None: config_atual = {}
    dicionario_nomes = config_atual.get("colunas_texto", {}) 
    layout = config_atual.get("layout_card", {})

    # Captura os dados básicos para o cabeçalho superior
    cod_municipio = str(row.get('codigo_municipio', '')).strip()
    cod_orgao = str(row.get('codigo_orgao', '')).strip()
    cod_unidade = str(row.get('codigo_unidade_orcamentaria', '')).strip()
    ano_real_doc = str(row.get('exercicio_orcamento', ano))[:4]
    municipio_nome = row.get('municipio_referencia', 'Não Informado')
    
    # 1. TÍTULO INTELIGENTE (Usa o Topo Esq ou Título Grande mapeado)
    col_topo_esq = layout.get("topo_esq", "")
    col_titulo_grande = layout.get("titulo_grande", "")
    
    val_topo_esq = str(row.get(col_topo_esq, "")).strip() if col_topo_esq else ""
    val_titulo_grande = str(row.get(col_titulo_grande, "")).strip() if col_titulo_grande else ""
    
    label_titulo = f"{categoria.upper()} — {val_topo_esq}" if val_topo_esq else categoria.upper()
    
    st.write(f"### {label_titulo}")
    st.caption(f"📍 {municipio_nome} — Exercício: {ano_real_doc} | {val_titulo_grande}")
    st.divider()

    # ==============================================================================
    # 2. INFORMAÇÕES PRINCIPAIS (Geradas pelos Metadados do JSON)
    # ==============================================================================
    metadados_config = layout.get("metadados", [])
    
    if metadados_config:
        st.markdown("#### 📄 Informações Principais")
        
        num_cols = min(len(metadados_config), 3)
        cols_metadados = st.columns(num_cols)
        
        for index, meta in enumerate(metadados_config):
            with cols_metadados[index % num_cols]:
                tipo = str(meta.get("tipo", "texto")).lower()
                label = meta.get("label", "Dado")
                coluna = meta.get("coluna", "")
                
                val_raw = row.get(coluna, "")
                
                if pd.isna(val_raw) or str(val_raw).strip() == "":
                    st.markdown(f"**{label}:** Não informado")
                    continue
                
                # 👇 Usando os formatadores importados do data_utils
                if tipo == "data":
                    st.markdown(f"📅 **{label}:** {formatar_data(val_raw)}")
                elif tipo == "valor":
                    st.markdown(f"💰 **{label}:** R$ {formatar_moeda(val_raw)}")
                elif tipo == "local":
                    st.markdown(f"📍 **{label}:** {str(val_raw).strip()}")
                else:
                    st.markdown(f"✏️ **{label}:** {str(val_raw).strip()}")
        st.divider()

    # ==============================================================================
    # 3. INFORMAÇÕES DE ORÇAMENTO (Opcional)
    # ==============================================================================
    if cod_orgao and cod_municipio:
        st.markdown("#### 🏛️ Informações de Orçamento")
        nome_orgao = buscar_nome_orgao_api(cod_municipio, ano_real_doc, cod_orgao)
        col1, col2 = st.columns(2)
        with col1: st.markdown(f"**Unidade Gestora:** {municipio_nome} - U.O. {cod_unidade}")
        with col2: st.markdown(f"**Órgão:** `{cod_orgao}` — {nome_orgao if nome_orgao else 'Não identificado'}")
        st.divider()

    # ==============================================================================
    # 4. DESCRIÇÃO DINÂMICA (Corpo do Texto Mapeado)
    # ==============================================================================
    desc_key = layout.get("corpo_texto", "")
    if isinstance(desc_key, list):
        partes_modal = []
        for c in desc_key:
            if c in row and not pd.isna(row[c]) and str(row[c]).strip():
                lbl = dicionario_nomes.get(c, c).replace("_", " ").title()
                partes_modal.append(f"**{lbl}:** {str(row[c]).strip()}")
                
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
    # 5. UI DE RASTREABILIDADE DINÂMICA (VIEW)
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
                arquivos_alvo = obter_caminho_arquivos_modal(prefixo_alvo, ano_real_doc, codigo_mun)
                arquivos_alvo_validos = [f for f in arquivos_alvo if os.path.exists(f)]
                
                resultado = obter_dados_relacionados(regra, row, arquivos_alvo_validos)
                
                if not resultado["sucesso"]:
                    st.caption(f"⚠️ {resultado['msg']}")
                else:
                    df_exibir = resultado["df_exibir"]
                    soma_total = resultado["soma_total"]
                    
                    dfs_relacionados[prefixo_alvo] = resultado["df_bruto"]
                    
                    st.dataframe(df_exibir, use_container_width=True, hide_index=True)
                    
                    if soma_total > 0: 
                        st.markdown(
                            f"<div style='font-size: 0.85rem; color: #888;'>"
                            f"Soma Total: <b>R$ {formatar_moeda(soma_total)}</b></div>", 
                            unsafe_allow_html=True
                        )
        st.divider()

    # ==============================================================================
    # 6. TABELA DE DADOS BRUTOS
    # ==============================================================================
    with st.expander("🛠️ Ver Todos os Dados Detalhados", expanded=False):
        dict_row = dict(row)
        chaves_relevantes = {k: v for k, v in dict_row.items() if not pd.isna(v) and str(v).strip() != "" and not k.startswith('_')}
        
        html_table = "<table style='width:100%; font-size:0.85rem; border-collapse: collapse;'>"
        for k, v in chaves_relevantes.items():
            val_formatado = str(v)
            # 👇 Usando formatadores unificados do data_utils
            if 'valor' in k.lower() and isinstance(v, (int, float)): val_formatado = f"R$ {formatar_moeda(v)}"
            elif 'data' in k.lower(): val_formatado = formatar_data(v)
            
            nome_amigavel = dicionario_nomes.get(k, k).replace("_", " ").title()
            html_table += f"<tr style='border-bottom: 1px solid #ddd;'><td style='padding:8px; font-weight:600; color:#555;'>{nome_amigavel}</td><td style='padding:8px;'>{val_formatado}</td></tr>"
        html_table += "</table>"
        st.markdown(html_table, unsafe_allow_html=True)

    # ==============================================================================
    # 7. BOTÃO PDF (Apenas Empenhos)
    # ==============================================================================
    if categoria == "Notas de Empenho":
        st.divider()
        try:
            df_liq_para_pdf = dfs_relacionados.get("liquidacoes", None)
            df_pag_para_pdf = dfs_relacionados.get("notas_pagamentos", None)
            
            pdf_data = gerar_pdf_empenho(row, df_liq_filtrada=df_liq_para_pdf, df_pag_filtrado=df_pag_para_pdf)
            st.download_button(label="🖨️ Imprimir Dossiê Completo (PDF)", data=pdf_data, file_name=f"Documento_{val_topo_esq}.pdf", mime="application/pdf", use_container_width=True)
        except Exception as e:
            st.error(f"❌ Erro ao gerar PDF: {str(e)}")