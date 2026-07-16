import streamlit as st

# Importações dos Motores de Regras
from src.modules.rules.patrimonial import render_bloco_patrimonial

# 🚀 1. Importando a nova regra do Fortes
from src.modules.rules.fortes_notas import render_bloco_fortes

def render_aba_comparacao():
    """Página Layout: HUB Central de Regras de Negócio e Comparação"""
    
    st.header("⚖️ Central de Regras de Negócio")
    
    modo_selecionado = st.radio(
        "Módulos Disponíveis:",
        options=[
            "🏛️ Comparação Patrimonial",
            "📊 Auditoria Fortes"
        ],
        horizontal=True,
        label_visibility="collapsed"
    )
    
    st.divider()
    
    # 🚦 Direcionamento de Rotas
    if modo_selecionado == "🏛️ Comparação Patrimonial":
        render_bloco_patrimonial()
        
    elif modo_selecionado == "📊 Auditoria Fortes":
        render_bloco_fortes()