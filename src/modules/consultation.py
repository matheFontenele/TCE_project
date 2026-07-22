# consultation.py
import streamlit as st
import pandas as pd
import glob
import re
import json
import os
import duckdb  # 🚀 O nosso novo motor relacional
import pyarrow.parquet as pq # Para leitura dinâmica de Schemas
from pathlib import Path
from src.modules.extraction import carregar_municipios
from src.components.details_modal import exibir_modal_detalhes
from components.details_modal_pagamento import exibir_modal_detalhes_pagamento
from modules.exportadores import renderizar_botoes_exportacao
from components.details_modal import obter_caminho_arquivos_modal
from components.dynamic_card import renderizar_card_dinamico

anos = list(range(2008, 2027))

# ==============================================================================
# FUNÇÕES DE TRATAMENTO E CARREGAMENTO DE DADOS (Consulta Geral)
# ==============================================================================

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
config_endpoints = carregar_configuracoes()

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

# ==============================================================================
# MOTOR GENÉRICO DE FILTRAGEM (Data-Driven)
# ==============================================================================
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

# ==============================================================================
# RENDERIZADOR DA PÁGINA
# =============================================================================
def render_consultation_page():
    st.header("🔍 Consulta Detalhada")

    for k in ["consulta_realizada", "df_resultado", "filtros_aplicados"]:
        if k not in st.session_state: 
            st.session_state[k] = False if k == "consulta_realizada" else (pd.DataFrame() if k == "df_resultado" else {})

    lista_municipios = carregar_municipios()
    opcoes_mun = {f"{m['nome_municipio']} ({m['codigo_municipio']})": m for m in lista_municipios}
    categorias_disponiveis = list(config_endpoints.keys())

    if not categorias_disponiveis:
        st.warning("⚠️ O arquivo endpoints.json não foi carregado corretamente.")
        return

    # 2. MOTOR HÍBRIDO DE INTERFACE (Monta a UI lendo o JSON)
    with st.expander("Opções de Filtro", expanded=not st.session_state.consulta_realizada):
        categoria_sel = st.selectbox("Tipo de Documento", categorias_disponiveis)
        config_atual = config_endpoints.get(categoria_sel, {})
        
        col_ini, col_fim, col_mun = st.columns(3)
        ano_inicial = col_ini.selectbox("Ano Inicial", anos, index=2)
        ano_final = col_fim.selectbox("Ano Final", anos, index=5)
        municipio_sel = col_mun.selectbox("Município", options=["Todos"] + list(opcoes_mun.keys()))
        
        st.markdown("<br>", unsafe_allow_html=True)
        st.caption(f"🎯 Filtros específicos para: **{categoria_sel}**")
        
        filtros_dinamicos = {}
        schema_unificado = list(config_atual.get("campos_ui", []))

        for col_id, col_label in config_atual.get("colunas_texto", {}).items():
            schema_unificado.append({
                "id": col_id, "label": f"🏷️ {col_label}", "tipo": "text", "placeholder": f"Digite {col_label.lower()}..."
            })

        if schema_unificado:
            cols_grid = st.columns(3)
            for i, campo in enumerate(schema_unificado):
                with cols_grid[i % 3]:
                    c_id, c_label, c_tipo = campo["id"], campo["label"], campo["tipo"]
                    if c_tipo == "select":
                        val = st.selectbox(c_label, campo["opcoes"])
                        if val != "TODOS": filtros_dinamicos[c_id] = val
                    elif c_tipo == "number":
                        val = st.number_input(c_label, value=int(campo.get("default", 0)), step=int(campo.get("step", 1)))
                        if val > 0: filtros_dinamicos[c_id] = val
                    elif c_tipo == "text":
                        val = st.text_input(c_label, placeholder=campo.get("placeholder", ""), key=f"dyn_{categoria_sel}_{c_id}").strip()
                        if val: filtros_dinamicos[c_id] = val
        else: st.write("*(Nenhum atributo avançado mapeado)*")

        st.divider()
        
        if ano_inicial > ano_final:
            st.error("⚠️ O Ano Inicial não pode ser maior que o Ano Final.")
            btn_consultar = False
        else:
            col_b, col_f, col_btn = st.columns([5, 3, 2])
            filtro_geral = col_b.text_input("Busca Geral", placeholder="Nº empenho, histórico, contrato, patrimônio...").strip()
            filtro_fornecedor = col_f.text_input("Fornecedor / Responsável", placeholder="CNPJ, Razão Social...").strip()
            
            col_btn.markdown("<div style='padding-top: 28px;'></div>", unsafe_allow_html=True)
            btn_consultar = col_btn.button("Consultar", use_container_width=True)

    # ==============================================================================
    # 3. DISPARO DA CONSULTA (O Controller Otimizado com DuckDB)
    # ==============================================================================
    if btn_consultar and ano_inicial <= ano_final:
        with st.spinner(f"Processando bases de {categoria_sel}..."):
            prefixo = config_atual.get("prefixo")
            codigo_mun_busca = "Todos" if municipio_sel == "Todos" else opcoes_mun[municipio_sel]['codigo_municipio']

            dfs_anos = []
            
            # --- 3.1 CARGA INICIAL DO OBJETO PRINCIPAL (Mais Limpo) ---
            for a in range(int(ano_inicial), int(ano_final) + 1):
                arquivos = obter_caminho_arquivos(prefixo, a, codigo_mun_busca)
                if not arquivos: continue
                    
                df_ano = carregar_e_filtrar(arquivos, colunas_texto=config_atual.get("colunas_texto", {}))
                if df_ano.empty: continue

                if municipio_sel != "Todos" and 'municipio_referencia' in df_ano.columns:
                    df_ano = df_ano[df_ano['municipio_referencia'].str.upper() == opcoes_mun[municipio_sel]['nome_municipio'].upper()]
                if df_ano.empty: continue

                df_ano['match_reason'] = ""
                dfs_anos.append(df_ano)

            df_consolidado = pd.concat(dfs_anos, ignore_index=True) if dfs_anos else pd.DataFrame()
            
            # Aplica os filtros visuais do sistema ANTES do join (Reduz a carga)
            df_final = filtrar_dataframe(df_consolidado, filtros_dinamicos, filtro_geral, filtro_fornecedor, config_atual)

            # --- 3.2 O MOTOR RELACIONAL DUCKDB PARA REFERENCIAMENTOS ---
            if categoria_sel == "Notas de Empenho" and not df_final.empty:
                con = duckdb.connect(database=':memory:')
                con.register('df_emp', df_final)
                
                caminhos_liq = []
                caminhos_pag = []
                for a in range(int(ano_inicial), int(ano_final) + 1):
                    caminhos_liq.extend(obter_caminho_arquivos("liquidacoes", a, codigo_mun_busca))
                    caminhos_pag.extend(obter_caminho_arquivos("notas_pagamentos", a, codigo_mun_busca))
                    
                # Verifica quais arquivos realmente existem no HD
                arquivos_liq_validos = [f"'{p}'" for p in caminhos_liq if os.path.exists(p)]
                arquivos_pag_validos = [f"'{p}'" for p in caminhos_pag if os.path.exists(p)]

                join_liq = ""
                col_liq_final = "0.0 AS valor_liquidado,"
                
                # Inteligência de Schema: Descobre o nome da coluna correta na hora
                if arquivos_liq_validos:
                    nome_col_liq = "valor_liquidado"
                    try:
                        schema = pq.read_schema(arquivos_liq_validos[0].replace("'", ""))
                        if 'valor_bruto_nota_liquidacao' in schema.names: nome_col_liq = 'valor_bruto_nota_liquidacao'
                        elif 'valor_bruto' in schema.names: nome_col_liq = 'valor_bruto'
                    except: pass

                    join_liq = f"""
                        LEFT JOIN (
                            SELECT 
                                CAST(codigo_municipio AS VARCHAR) as mun,
                                CAST(codigo_orgao AS VARCHAR) as org,
                                CAST(numero_empenho AS VARCHAR) as emp,
                                SUM(TRY_CAST(COALESCE(NULLIF(CAST({nome_col_liq} AS VARCHAR), ''), '0') AS DOUBLE)) as total_liq
                            FROM read_parquet([{', '.join(arquivos_liq_validos)}], union_by_name=True)
                            GROUP BY 1, 2, 3
                        ) AS l 
                        ON CAST(e.codigo_municipio AS VARCHAR) = l.mun 
                        AND CAST(e.codigo_orgao AS VARCHAR) = l.org 
                        AND CAST(e.numero_empenho AS VARCHAR) = l.emp
                    """
                    col_liq_final = "COALESCE(l.total_liq, 0.0) AS valor_liquidado,"

                join_pag = ""
                col_pag_val = "0.0"
                if arquivos_pag_validos:
                    nome_col_pag = "valor_nota_pagamento"
                    try:
                        schema = pq.read_schema(arquivos_pag_validos[0].replace("'", ""))
                        if 'valor_pago' in schema.names: nome_col_pag = 'valor_pago'
                    except: pass

                    join_pag = f"""
                        LEFT JOIN (
                            SELECT 
                                CAST(codigo_municipio AS VARCHAR) as mun,
                                CAST(codigo_orgao AS VARCHAR) as org,
                                CAST(numero_empenho AS VARCHAR) as emp,
                                SUM(TRY_CAST(COALESCE(NULLIF(CAST({nome_col_pag} AS VARCHAR), ''), '0') AS DOUBLE)) as total_pag
                            FROM read_parquet([{', '.join(arquivos_pag_validos)}], union_by_name=True)
                            GROUP BY 1, 2, 3
                        ) AS p 
                        ON CAST(e.codigo_municipio AS VARCHAR) = p.mun 
                        AND CAST(e.codigo_orgao AS VARCHAR) = p.org 
                        AND CAST(e.numero_empenho AS VARCHAR) = p.emp
                    """
                    col_pag_val = "COALESCE(p.total_pag, 0.0)"

                # A Query Final: Injeta os totais de forma relacional direto na base fitrada
                query = f"""
                    SELECT 
                        e.*,
                        {col_liq_final}
                        {col_pag_val} AS valor_pago,
                        CASE 
                            WHEN {col_pag_val} <= 0 THEN 'PENDENTE'
                            WHEN {col_pag_val} >= TRY_CAST(COALESCE(NULLIF(CAST(e.valor_empenhado AS VARCHAR), ''), '0') AS DOUBLE) THEN 'PAGO'
                            ELSE 'PARCIAL'
                        END AS status_pagamento
                    FROM df_emp AS e
                    {join_liq}
                    {join_pag}
                """
                
                df_final = con.execute(query).df()

            st.session_state.df_resultado = df_final
            st.session_state.consulta_realizada = True
            st.session_state.filtros_aplicados = {
                "categoria_sel": categoria_sel, "ano_inicial": ano_inicial, "ano_final": ano_final,
                "municipio_sel": municipio_sel, "codigo_mun_busca": codigo_mun_busca, "prefixo": prefixo,
                "filtro_geral": filtro_geral, "filtro_fornecedor": filtro_fornecedor, **filtros_dinamicos
            }

    # ==============================================================================
    # 4. EXIBIÇÃO DOS RESULTADOS (Invocando o dynamic_card.py)
    # ==============================================================================
    if st.session_state.consulta_realizada:
        df = st.session_state.df_resultado
        filtros = st.session_state.filtros_aplicados
        
        st.subheader(f"LISTA DE {filtros['categoria_sel'].upper()} - {filtros['ano_inicial']} A {filtros['ano_final']}")
        if filtros['municipio_sel'] != "Todos": st.write(f"📍 Município: **{opcoes_mun[filtros['municipio_sel']]['nome_municipio']}**")
            
        if not df.empty:
            st.caption(f"Foram encontrados **{len(df):,}** registros.")

            limite = 20
            for index, row in df.head(limite).iterrows():
                renderizar_card_dinamico(
                    row=row,
                    index=index,
                    categoria=filtros['categoria_sel'],
                    config_atual=config_endpoints.get(filtros['categoria_sel'], {}),
                    filtro_geral=filtros['filtro_geral'],
                    ano_inicial=filtros['ano_inicial'],
                    codigo_mun_busca=filtros['codigo_mun_busca']
                )

            if len(df) > limite: st.info(f"Exibindo os {limite} primeiros registros de alta relevância.")

            st.divider()
            st.markdown("### 📥 Opções de Exportação")
            
            csv_data = df.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig')
            st.download_button(
                label=f"📥 Exportar Grid Atual ({len(df):,} linhas)", 
                data=csv_data, 
                file_name=f"TCE_{filtros['prefixo']}_{filtros['ano_inicial']}_{filtros['ano_final']}.csv", 
                mime="text/csv", use_container_width=True
            )

            if filtros['categoria_sel'] == "Notas de Empenho":
                renderizar_botoes_exportacao(
                    df_empenhos_filtrados=df,
                    ano_inicial=filtros['ano_inicial'],
                    ano_final=filtros['ano_final'],
                    codigo_mun=filtros['codigo_mun_busca'],
                    obter_caminho_func=obter_caminho_arquivos_modal,
                    carregar_e_filtrar_func=carregar_e_filtrar
                )
        else:
            st.warning("Nenhum registro encontrado para os filtros aplicados.")
    else:
        st.info("Selecione os parâmetros acima e clique em 'Consultar' para carregar a base de dados.")