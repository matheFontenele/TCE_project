import streamlit as st
import pandas as pd
import re
from src.components.details_modal import exibir_modal_detalhes
from src.modules.data_utils import formatar_moeda, formatar_data

# ==============================================================================
# CSS NATIVO STREAMLIT
# ==============================================================================
CSS_CARDS = """
<style>
    .report-card {
        background-color: var(--secondary-background-color, #F0F2F6);
        color: var(--text-color, #10162F) !important;
        border: 1px solid rgba(128, 128, 128, 0.2); 
        border-radius: 12px;
        padding: 24px;
        margin-bottom: 16px; 
        border-left: 8px solid var(--primary-color, #ff4b4b);
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }

    .report-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 16px rgba(0, 0, 0, 0.15);
    }

    .card-header-row { display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px; }
    
    .card-header { 
        color: var(--faded-text-color, #64748b); 
        font-weight: 700; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.5px; 
    }

    .match-badge {
        background-color: #fef08a; color: #854d0e; font-size: 0.7rem; font-weight: bold; 
        padding: 2px 8px; border-radius: 20px; text-transform: uppercase;
    }

    .status-badge { font-size: 0.7rem; font-weight: bold; padding: 2px 10px; border-radius: 20px; text-transform: uppercase; margin-left: 8px; }
    
    .status-pago { background-color: rgba(34, 197, 94, 0.15); color: #10b981; border: 1px solid rgba(34, 197, 94, 0.3); }
    .status-parcial { background-color: rgba(234, 179, 8, 0.15); color: #f59e0b; border: 1px solid rgba(234, 179, 8, 0.3); }
    .status-pendente { background-color: rgba(239, 68, 68, 0.15); color: #ef4444; border: 1px solid rgba(239, 68, 68, 0.3); }

    .card-vendor { font-size: 1.2rem; font-weight: 800; margin: 6px 0; }
    
    /* CSS ANTIGO (MANTIDO PARA FALLBACK) */
    .card-org { color: var(--primary-color, #ff4b4b); font-size: 0.85rem; font-weight: 600; }
    .card-date { color: var(--faded-text-color, #64748b); font-size: 0.85rem; font-weight: 600; }

    /* NOVO CSS DOS METADADOS INTELIGENTES */
    .meta-tags-container { 
        display: flex; 
        flex-wrap: wrap; 
        gap: 8px; 
        margin: 10px 0; 
    }
    .meta-tag { 
        font-size: 0.75rem; 
        background-color: rgba(128, 128, 128, 0.08); 
        padding: 4px 10px; 
        border-radius: 12px; 
        border: 1px solid rgba(128, 128, 128, 0.15); 
        display: inline-flex; 
        align-items: center;
        gap: 4px;
        color: var(--text-color); 
        opacity: 0.9; 
    }

    .card-detail-txt {
        font-size: 0.9rem; 
        line-height: 1.5; 
        color: var(--text-color) !important; 
        opacity: 0.85; 
        margin-top: 10px; 
        min-height: 52px;
    }

    .values-grid { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 16px; }
    .value-col { flex: 1 1 28%; background-color: var(--background-color, #FFFFFF); padding: 12px; border-radius: 8px; border: 1px solid rgba(128, 128, 128, 0.15); text-align: center; }
    .value-title { font-size: 0.7rem; color: var(--faded-text-color, #64748b); font-weight: 700; text-transform: uppercase; margin-bottom: 4px; }
    .value-num { font-family: 'Roboto Mono', monospace; font-weight: 800; font-size: 1.2rem; }

    mark { background-color: #fef08a !important; color: #000000 !important; font-weight: bold; padding: 0 2px; border-radius: 3px; }
</style>
"""

# Função auxiliar apenas para o Marca Texto (Busca Geral)
def _highlight(texto, termo):
    if not termo or not texto: return str(texto)
    return re.compile(re.escape(termo), re.IGNORECASE).sub(lambda m: f"<mark>{m.group(0)}</mark>", str(texto))

# ==============================================================================
# MOTOR DO COMPONENTE DINÂMICO
# ==============================================================================
def renderizar_card_dinamico(row, index, categoria, config_atual, filtro_geral, ano_inicial, codigo_mun_busca):
    
    st.markdown(CSS_CARDS, unsafe_allow_html=True)

    r = row.to_dict() if isinstance(row, pd.Series) else row
    layout = config_atual.get("layout_card", {})
    dicionario_nomes = config_atual.get("colunas_texto", {})

    # 1. CAPTURA RÍGIDA DOS SLOTS
    col_topo   = layout.get("topo_esq", "")
    col_titulo = layout.get("titulo_grande", "")
    col_corpo  = layout.get("corpo_texto", "")

    val_topo   = str(r.get(col_topo, "N/A")).strip()
    val_titulo = str(r.get(col_titulo, "Título não mapeado")).strip()

    label_topo   = dicionario_nomes.get(col_topo, col_topo).replace("_", " ").title()
    label_titulo = dicionario_nomes.get(col_titulo, col_titulo).replace("_", " ").title()
    
    slot_id_montado = f"{label_topo.upper()}: {val_topo}"

    # ==========================================================================
    # 2. O NOVO MOTOR DE METADADOS DINÂMICOS
    # ==========================================================================
    html_metadados = ""
    metadados_config = layout.get("metadados", [])

    if metadados_config:
        # Se o JSON já tiver a nova estrutura de metadados
        html_metadados += '<div class="meta-tags-container">'
        for meta in metadados_config:
            tipo = str(meta.get("tipo", "texto")).lower()
            label = meta.get("label", "")
            col = meta.get("coluna", "")

            val_raw = r.get(col, "")
            
            # Se o dado estiver vazio, pula o selo
            if pd.isna(val_raw) or str(val_raw).strip() == "":
                continue

            # Roteador de Tipos e Emoticons (Usando o data_utils)
            if tipo == "data":
                val_str = formatar_data(val_raw)
                icone = "📅"
            elif tipo == "valor":
                val_str = f"R$ {formatar_moeda(val_raw)}"
                icone = "💰"
            elif tipo == "local":
                val_str = str(val_raw).strip()
                icone = "📍"
            else: # texto padrão
                val_str = str(val_raw).strip()
                icone = "✏️"

            if filtro_geral:
                val_str = _highlight(val_str, filtro_geral)

            html_metadados += f'<span class="meta-tag">{icone} <strong>{label}:</strong> {val_str}</span>'
            
        html_metadados += '</div>'
    else:
        # FALLBACK: Se o endpoint ainda usar a versão antiga (meta_1 e meta_2)
        val_meta1 = str(r.get(layout.get("meta_1", "municipio_referencia"), "Local indisponível")).strip()
        val_meta2 = formatar_data(r.get(layout.get("meta_2", "")))
        html_metadados = f'<div class="card-org">📍 {val_meta1}</div><span class="card-date">📅 {val_meta2}</span>'

    # ==========================================================================
    # 3. CORPO E DESTAQUES
    # ==========================================================================
    if isinstance(col_corpo, list):
        partes = []
        for c in col_corpo:
            val = str(r.get(c, "")).strip()
            if pd.notna(r.get(c)) and val and val.lower() != "nan":
                lbl = dicionario_nomes.get(c, c).replace("_", " ").title()
                partes.append(f"<b>{lbl}:</b> {val}")
        val_corpo = " &nbsp;&bull;&nbsp; ".join(partes) if partes else "Sem descrição."
        label_corpo = "Especificações"
    else:
        val_corpo  = str(r.get(col_corpo, "Sem descrição.")).strip()
        label_corpo = dicionario_nomes.get(col_corpo, col_corpo).replace("_", " ").title()

    badge_match = f'<span class="match-badge">🔍 {r["match_reason"]}</span>' if r.get("match_reason") else ""
    
    badge_status = ""
    status = r.get("status_pagamento")
    if status and not pd.isna(status) and str(status).strip() != "":
        status_str = str(status).strip().upper()
        badge_status = f'<span class="status-badge status-{status_str.lower()}">{status_str}</span>'

    if filtro_geral:
        slot_id_montado = _highlight(slot_id_montado, filtro_geral)
        val_titulo      = _highlight(val_titulo, filtro_geral)
        val_corpo       = _highlight(val_corpo, filtro_geral)

    detalhe_view = val_corpo[:220] + "..." if len(val_corpo) > 220 and "<mark>" not in val_corpo[210:] else val_corpo

    # ==========================================================================
    # 4. GRID FINANCEIRO
    # ==========================================================================
    grid_financeiro_json = layout.get("grid_financeiro", [])
    grid_valores_html = ""

    for item in grid_financeiro_json:
        col_alvo = item.get("coluna")
        label_caixa = item.get("label_caixa", "Valor")

        if col_alvo not in r or pd.isna(r[col_alvo]) or r[col_alvo] == "":
            continue

        val_num = r[col_alvo]
        val_str = formatar_moeda(val_num)

        cor_texto = "color: var(--text-color);"
        if any(w in col_alvo.lower() for w in ['pago', 'pagamento', 'liquido']): cor_texto = "color: #10b981;"
        elif any(w in col_alvo.lower() for w in ['liquidado']): cor_texto = "color: #3b82f6;"

        prefixo_moeda = "" if any(w in col_alvo.lower() for w in ['quantidade', 'qtd']) else "R$ "
        grid_valores_html += f'<div class="value-col"><div class="value-title">{label_caixa}</div><div class="value-num" style="{cor_texto}">{prefixo_moeda}{val_str}</div></div>'

    # ==========================================================================
    # 5. MONTAGEM FINAL DO HTML
    # ==========================================================================
    card_box = (
        f'<div class="report-card">'
        f'  <div class="card-header-row"><div class="card-header">{slot_id_montado} {badge_status}</div>{badge_match}</div>'
        f'  <div class="card-vendor"><span style="font-size: 0.75rem; color: var(--faded-text-color, #64748b); text-transform: uppercase;">{label_titulo}</span><br>{val_titulo}</div>'
        
        # INJEÇÃO DOS METADADOS DINÂMICOS (OU FALLBACK)
        f'  {html_metadados}'
        
        f'  <div class="card-detail-txt"><strong>{label_corpo}:</strong> {detalhe_view}</div>'
        f'  <div class="values-grid">{grid_valores_html}</div>'
        f'</div>'
    )
    st.markdown(card_box, unsafe_allow_html=True)

    # ==========================================================================
    # 6. BOTÕES E MODAIS
    # ==========================================================================
    if st.button("DETALHES 🔎", key=f"btn_det_{categoria}_{index}", use_container_width=True):     
        exibir_modal_detalhes(row=row, categoria=categoria, ano=ano_inicial, codigo_mun=codigo_mun_busca, config_atual=config_atual, id_unico=index)