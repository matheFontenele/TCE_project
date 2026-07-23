# src/modules/data_utils.py
import streamlit as st
import pandas as pd
import glob
import json
from pathlib import Path

@st.cache_data
def carregar_configuracoes():
    """Carrega o endpoints.json da raiz do projeto espelhando a lógica de municípios"""
    try:
        caminhos = [
            Path(__file__).resolve().parents[2] / 'endpoints.json',
            Path(__file__).resolve().parents[1] / 'endpoints.json',
            Path(__file__).resolve().parent / 'endpoints.json',
            Path('/app/endpoints.json'),
            Path('endpoints.json')
        ]
        
        for caminho in caminhos:
            if caminho.exists():
                print(f"📂 JSON de Metadados encontrado em: {caminho}")
                with open(caminho, 'r', encoding='utf-8') as f:
                    return json.load(f)
                    
        print("🚨 ERRO: endpoints.json não encontrado em nenhum diretório base.")
        return {}
    except Exception as e:
        print(f"🚨 ERRO crítico ao carregar endpoints.json: {e}")
        return {}

@st.cache_data(show_spinner="Consolidando e higienizando base Parquet...")
def carregar_e_filtrar(arquivos, colunas_texto=None):
    """Carrega os arquivos Parquet de forma geral e executa a limpeza das colunas correspondentes"""
    if not arquivos:
        return pd.DataFrame()
        
    dfs = []
    for f in arquivos:
        try:
            dfs.append(pd.read_parquet(f))
        except Exception:
            continue
            
    if not dfs:
        return pd.DataFrame()
    
    df = pd.concat(dfs, ignore_index=True)

    if colunas_texto:
        for col in colunas_texto.keys():
            if col in df.columns:
                df[col] = df[col].fillna("").astype(str).str.strip()
                
    colunas_numericas = [c for c in df.columns if 'valor' in c.lower() or 'quantidade' in c.lower() or 'qtd' in c.lower()]
    for col in colunas_numericas:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
        
    colunas_object = df.select_dtypes(include=['object']).columns
    for col in colunas_object:
        if colunas_texto and col in colunas_texto:
            continue 
        df[col] = df[col].fillna("").astype(str).str.strip()
        
    return df

def formatar_moeda(valor):
    try:
        if pd.isna(valor) or valor is None:
            return "0,00"
        val_float = float(valor)
        return f"{val_float:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return "0,00"

def formatar_data(data_raw):
    try:
        if pd.isna(data_raw) or data_raw is None or str(data_raw).strip() == "":
            return "Não Informada"
            
        data_str = str(data_raw).split('T')[0].strip()
        
        if '-' in data_str:
            partes = data_str.split('-')
            if len(partes) == 3:
                return f"{partes[2]}/{partes[1]}/{partes[0]}"
                
        elif len(data_str) == 8 and data_str.isdigit():
            return f"{data_str[6:8]}/{data_str[4:6]}/{data_str[0:4]}"
            
        return data_str
    except Exception:
        return "Não Informada"

def obter_caminho_arquivos(prefixo, ano, codigo_mun):
    if codigo_mun == "Todos":
        return sorted(glob.glob(f"data/{prefixo}_{ano}_*.parquet") + glob.glob(f"data/{prefixo}_{ano}.parquet"))
    mensais = glob.glob(f"data/{prefixo}_{ano}_*_{codigo_mun}.parquet")
    anuais = glob.glob(f"data/{prefixo}_{ano}_{codigo_mun}.parquet")
    return sorted(mensais + anuais)

def filtrar_dataframe(df, filtros_din, busca_geral, filtro_forn, config_atual):
    if df.empty: 
        return df

    for campo, valor in filtros_din.items():
        if campo in df.columns and valor and valor != "TODOS":
            if isinstance(valor, (int, float)) and valor > 0:
                df = df[df[campo] >= valor]
            elif isinstance(valor, str):
                df = df[df[campo].astype(str).str.upper().str.contains(valor.upper(), na=False)]

    if busca_geral:
        termo = str(busca_geral).strip()
        mask_global = pd.Series(False, index=df.index)
        regras = config_atual.get("colunas_texto", {})
        
        for col, motivo in regras.items():
            if col in df.columns:
                mask_col = df[col].astype(str).str.contains(termo, case=False, na=False)
                df.loc[mask_col, 'match_reason'] = motivo
                mask_global |= mask_col
                
        if regras: 
            df = df[mask_global]

    if filtro_forn:
        termos_f = [t.strip() for t in filtro_forn.split(",") if t.strip()]
        col_fornecedores = ['nome_negociante', 'numero_documento_negociante', 'nome_responsavel_pagamento', 'cpf_cnpj_emitente', 'nome_emitente', 'razao_social', 'orgao']
        col_existentes = [c for c in col_fornecedores if c in df.columns]
        
        if col_existentes:
            mask_f_global = pd.Series(False, index=df.index)
            for t in termos_f:
                mask_t = pd.Series(False, index=df.index)
                for c in col_existentes:
                    mask_t |= df[c].astype(str).str.contains(t, case=False, na=False)
                mask_f_global |= mask_t
            df = df[mask_f_global]

    return df