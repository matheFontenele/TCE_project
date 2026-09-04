import streamlit as st
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.modules.extraction import render_extraction_page
from src.modules.consultation import render_consultation_page
from src.modules.compare import render_aba_comparacao
from src.modules.validacao import render_validacao_page

st.set_page_config(page_title="Automação TCE-CE", page_icon="🤖", layout="wide")

# Estilização para o Menu Superior
st.markdown("""
    <style>
    /* Seletor universal para botões de navegação e botões do Streamlit */
    div.stButton > button {
        width: 100% !important;
        border-radius: 8px !important;
        height: 3.5em !important;
        background-color: #1E1E24 !important; /* Grafite Profundo */
        color: #FFFFFF !important;
        border: 1px solid rgba(255, 255, 255, 0.1) !important;
        font-weight: 600 !important;
        font-size: 16px !important;
        transition: all 0.3s ease-in-out !important;
    }

    /* Hover: Efeito vermelho suave na borda */
    div.stButton > button:hover {
        background-color: #2D2D35 !important;
        border-color: #ff4b4b !important;
        color: #ff4b4b !important;
    }

    /* Botão Primário (Ativo): Fundo Vermelho */
    div.stButton > button[kind="primary"] {
        background-color: #ff4b4b !important;
        color: white !important;
        border: none !important;
        box-shadow: 0 4px 12px rgba(255, 75, 75, 0.4) !important;
    }
    
    /* Remove o contorno azul de foco que o Chrome/Streamlit coloca */
    button:focus:not(:active) {
        border-color: #ff4b4b !important;
        color: #ff4b4b !important;
    }
    </style>
""", unsafe_allow_html=True)

# Inicializa o estado de navegação
if 'modo_tela' not in st.session_state:
    st.session_state.modo_tela = 'Extração'

# Layout de Menu Superior
st.title("🤖 Painel de Automação TCE-CE")
col_nav1, col_nav2, col_nav3, col_nav4, col_spacer = st.columns([1.0, 1.0, 1.5, 1.2, 4])

with col_nav1:
    if st.button("📊 Extração", key="btn_ext", 
                 type="primary" if st.session_state.modo_tela == 'Extração' else "secondary"):
        st.session_state.modo_tela = 'Extração'
        st.rerun()

with col_nav2:
    if st.button("🔍 Consulta", key="btn_con", 
                 type="primary" if st.session_state.modo_tela == 'Consulta' else "secondary"):
        st.session_state.modo_tela = 'Consulta'
        st.rerun()

with col_nav3:
    if st.button("⚙️ Comparação", key="btn_cfg", 
                 type="primary" if st.session_state.modo_tela == 'Comparação' else "secondary"):
        st.session_state.modo_tela = 'Comparação'
        st.rerun()

with col_nav4:
    if st.button("✅ Validação", key="btn_val",
                 type="primary" if st.session_state.modo_tela == 'Validação' else "secondary"):
        st.session_state.modo_tela = 'Validação'
        st.rerun()

st.divider()

# Exibição do conteúdo
if st.session_state.modo_tela == 'Extração':
    render_extraction_page()
elif st.session_state.modo_tela == 'Consulta':
    render_consultation_page()
elif st.session_state.modo_tela == 'Comparação':
    render_aba_comparacao()
elif st.session_state.modo_tela == 'Validação':
    render_validacao_page()