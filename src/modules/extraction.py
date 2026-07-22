import streamlit as st
import pandas as pd
import os
import glob
import re
import time
import requests
import threading
import concurrent.futures
import json
import datetime
from pathlib import Path
from tqdm import tqdm


# ==============================================================
# CONFIGURAÇÕES EXTRAS
# ==============================================================

PASTA_DADOS = "/app/data"
# Cria um armazenamento local por thread para garantir isolamento e thread-safety
thread_local = threading.local()

# Carrega a lista de municípios do arquivo JSON para uso na geração das tarefas de extração
def carregar_municipios():
    """
    Carrega municípios do JSON da raiz do projeto
    """
    try:
        caminho_json = Path(__file__).resolve().parents[2] / 'municipios.json'
        
        if not caminho_json.exists():
            caminho_json = Path('/app/municipios.json')
        
        print(f"📂 Carregando JSON de: {caminho_json}")
        
        if not caminho_json.exists():
            print(f"🚨 ERRO: {caminho_json} não encontrado")
            print(f"📍 Diretório atual: {os.getcwd()}")
            return []
        
        with open(caminho_json, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        print(f"✅ {len(data['elements'])} municípios carregados!")
        return data['elements']
        
    except Exception as e:
        print(f"🚨 ERRO: {type(e).__name__}: {e}")
        return []


# Função de logs terminal so que bonito, colorido e formatado para o terminal (Linux)
def formatar_log_terminal(msg: str) -> str:
    RESET = "\033[0m"
    VERDE = "\033[32m"
    AMARELO = "\033[33m"
    VERMELHO = "\033[31m"
    AZUL = "\033[34m"
    CYAN = "\033[36m"
    NEGRITO = "\033[1m"

    if "Iniciando extração" in msg or "Total de tarefas" in msg:
        return f"{AZUL}⚡ {msg}{RESET}"
        
    if "Resumo:" in msg:
        return f"\n{CYAN}{NEGRITO}📊 {msg}{RESET}\n"

    if "🚨 [ALERTA]" in msg:
        return f"{AMARELO}{NEGRITO}{msg}{RESET}"

    # 🟢 SUCESSO (Criado com sucesso)
    if "✅ Criado com sucesso:" in msg:
        conteudo = msg.replace("✅ Criado com sucesso:", "").strip()
        partes = conteudo.split(" ")
        nome_arq = partes[0]
        detalhes = " ".join(partes[1:]) if len(partes) > 1 else "Salvo."
        return f"{VERDE}│ 🟢 [SALVO]    │{RESET} {nome_arq:<65} {VERDE}│{RESET} {detalhes}"

    # 🟡 IGNORADO (Arquivo já existente)
    if "⏭️ Ignorado:" in msg:
        conteudo = msg.replace("⏭️ Ignorado:", "").replace("já existe.", "").strip()
        return f"{AMARELO}│ 🟡 [IGNORADO] │{RESET} {conteudo:<65} {AMARELO}│{RESET} Já em disco"

    # 🔵 VAZIO (Sem dados ou descartado por ano)
    if "⚠️ Vazio:" in msg:
        conteudo = msg.replace("⚠️ Vazio:", "").strip()
        partes = conteudo.split(" - ")
        nome_arq = partes[0]
        detalhes = partes[1] if len(partes) > 1 else "Sem registros no TCE"
        return f"{AZUL}│ 🔵 [VAZIO]    │{RESET} {nome_arq:<65} {AZUL}│{RESET} {detalhes}"

    # 🔴 FALHA (Tratamento dinâmico de erros de conexão/API)
    if "❌" in msg or "Erro" in msg:
        conteudo = msg.replace("❌", "").strip()
        nome_arq = "Erro de Processamento"
        detalhes = conteudo

        # Captura o nome do arquivo dinamicamente de dentro da mensagem de erro
        if "em " in conteudo:
            partes_em = conteudo.split("em ")
            detalhes = partes_em[0].strip()
            restante = partes_em[1].split(":")
            nome_arq = restante[0].strip()
            if len(restante) > 1:
                detalhes += " -> " + ":".join(restante[1:]).strip()
        elif "para " in conteudo:
            partes_para = conteudo.split("para ")
            detalhes = partes_para[0].strip()
            restante = partes_para[1].split(":")
            nome_arq = restante[0].strip()
            if len(restante) > 1:
                detalhes += " -> " + ":".join(restante[1:]).strip()

        # Encurta a mensagem técnica de SSL/EOF para manter o terminal scannable
        if "SSLEOFError" in detalhes or "Max retries exceeded" in detalhes:
            detalhes = "Conexão abortada pelo Firewall do TCE (SSL UNEXPECTED_EOF)"

        return f"{VERMELHO}│ 🔴 [FALHA]    │{RESET} {nome_arq:<65} {VERMELHO}│{RESET} {VERMELHO}{detalhes}{RESET}"

    return msg

# ==============================================================
# SISTEMA DE FILTROS E CONSULTA DE ARQUIVOS (OTIMIZADO)
# ==============================================================
def renderizar_aba_consulta(tipo_dado_selecionado):
    if not os.path.exists(PASTA_DADOS):
        st.warning(f"📂 O diretório `{PASTA_DADOS}` ainda não foi criado. Realize uma extração primeiro.")
        return

    # Lista arquivos guardados que começam com o prefixo correto do endpoint
    arquivos_disponiveis = [
        f for f in os.listdir(PASTA_DADOS) 
        if f.startswith(tipo_dado_selecionado) and f.endswith('.parquet')
    ]

    if not arquivos_disponiveis:
        st.warning(f"⚠️ Nenhum arquivo de de dados encontrado para este painel.")
        st.info("Configure os parâmetros na barra lateral e clique em 'Executar Extração'.")
        return

    # Extração reversa inteligente de metadados baseada no nome do arquivo
    anos = set()
    meses = set()
    municipios = set()

    for arq in arquivos_disponiveis:
        partes = arq.replace('.parquet', '').split('_')
        if len(partes) >= 3:
            anos.add(partes[-3])
            meses.add(partes[-2])
            municipios.add(partes[-1])

    st.markdown("### 🛠️ Filtros de Especificação")
    col1, col2, col3 = st.columns(3)

    with col1:
        ano_sel = st.selectbox("📅 Ano", ["Todos"] + sorted(list(anos)), key=f"ano_{tipo_dado_selecionado}")
    with col2:
        mes_sel = st.selectbox("📆 Mês", ["Todos"] + sorted(list(meses)), key=f"mes_{tipo_dado_selecionado}")
    with col3:
        mun_sel = st.selectbox("🏙️ Código Município", ["Todos"] + sorted(list(municipios)), key=f"mun_{tipo_dado_selecionado}")

    # Aplicação estruturada dos filtros na listagem
    arquivos_filtrados = arquivos_disponiveis
    if ano_sel != "Todos":
        arquivos_filtrados = [f for f in arquivos_filtrados if f.split('_')[-3] == ano_sel]
    if mes_sel != "Todos":
        arquivos_filtrados = [f for f in arquivos_filtrados if f.split('_')[-2] == mes_sel]
    if mun_sel != "Todos":
        arquivos_filtrados = [f for f in arquivos_filtrados if f.split('_')[-1].replace('.parquet', '') == mun_sel]

    st.divider()

    if arquivos_filtrados:
        st.success(f"🎯 Restam **{len(arquivos_filtrados)}** arquivos correspondentes aos filtros aplicados.")
        arquivo_final = st.selectbox("📄 Selecione o arquivo exato:", arquivos_filtrados, key=f"file_sel_{tipo_dado_selecionado}")
        
        caminho_completo = os.path.join(PASTA_DADOS, arquivo_final)
        
        if st.button(f"📊 Carregar e Visualizar Dados", use_container_width=True, type="primary", key=f"btn_{tipo_dado_selecionado}"):
            try:
                with st.spinner("Decodificando base Parquet de alta performance..."):
                    df = pd.read_parquet(caminho_completo)
                
                m1, m2 = st.columns(2)
                m1.metric("Total de Linhas", f"{len(df):,}".replace(",", "."))
                m2.metric("Total de Colunas", len(df.columns))
                
                st.markdown("### 📋 Tabela de Dados")
                st.dataframe(df, use_container_width=True, hide_index=True)
                
                # Download nativo convertido em tempo de execução
                st.download_button(
                    label="📥 Exportar Base Completa para CSV (Excel)",
                    data=df.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig'),
                    file_name=arquivo_final.replace('.parquet', '.csv'),
                    mime='text/csv',
                    use_container_width=True,
                    key=f"dl_{tipo_dado_selecionado}"
                )
            except Exception as e:
                st.error(f"❌ Falha crítica ao ler arquivo de disco: {e}")
    else:
        st.error("❌ Nenhuma combinação encontrada para os filtros selecionados.")

# ==============================================================
# INICIO DE BLOCOS DE PROCESSAMENTO
# ==============================================================

def obter_sessao(forcar_nova=False):
    # Se forçado, deleta a sessão antiga do escopo local da Thread
    if forcar_nova and hasattr(thread_local, "session"):
        try:
            thread_local.session.close()
        except:
            pass
        delattr(thread_local, "session")

    if not hasattr(thread_local, "session"):
        thread_local.session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=1, pool_maxsize=1)
        thread_local.session.mount('https://', adapter)
        
    return thread_local.session

# Função que processa cada tarefa individualmente, responsável por fazer a requisição, aplicar o filtro defensivo e salvar o arquivo parquet
# Função que processa cada tarefa individualmente, responsável por fazer a requisição, aplicar o filtro defensivo e salvar o arquivo parquet
def processar_lote(task):
    caminho_arquivo = task['caminho_arquivo']
    nome_arquivo = os.path.basename(caminho_arquivo)
    
    # Checkpoint: se o arquivo já existe (seja com dados ou fantasma), pula instantaneamente!
    if os.path.exists(caminho_arquivo):
        return "IGNORADO", f"⏭️ Ignorado: {nome_arquivo} já existe."

    time.sleep(0.7)  # Pequena pausa para evitar picos de requisições em loops rápidos

    # Recupera a sessão persistente da thread atual
    session = obter_sessao()

    # Header amigável para evitar bloqueio por WAF/Firewall simples
    headers_requisicao = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Connection": "keep-alive"
    }

    try:
        todos_dados = []
        start_index = task['params'].get('$start_index', 0)
        count_limite = task['params'].get('$count', 1000)

        # 🔄 Loop de Paginação Ativa
        while True:
            task['params']['$start_index'] = start_index
            
            # 🛡️ Mini-loop de tolerância a falhas de rede/SSL (Backoff Exponencial)
            max_tentativas = 5
            response = None
            
            for tentativa in range(max_tentativas):
                try:
                    response = session.get(
                        task['url'], 
                        headers=headers_requisicao, 
                        params=task['params'], 
                        timeout=45
                    )
                    break  # Conectou com sucesso? Sai do loop de retentativa
                except (requests.exceptions.SSLError, requests.exceptions.RequestException) as e:
                    if tentativa == max_tentativas - 1:
                        return "ERRO_CONEXAO", f"❌ Derrubado pelo TCE após {max_tentativas} tentativas em {nome_arquivo}: {str(e)}"
                    
                    # Se deu erro de SSL/Conexão, fechamos a conexão atual para não travar o pool
                    tempo_espera = (tentativa + 1) * 7
                    time.sleep(tempo_espera)
                    session = obter_sessao(forcar_nova=True)
            
            # Executa a validação se a resposta de rede foi bem-sucedida
            if response and response.status_code == 200:
                elementos = response.json().get("elements", [])
                if not elementos:
                    break  # Não vieram mais registros, interrompe o loop de paginação
                
                todos_dados.extend(elementos)
                
                # Se a API retornou menos que o limite, chegamos na última página
                if len(elementos) < count_limite:
                    break
                
                start_index += count_limite  # Avança o ponteiro para a próxima página
                time.sleep(0.5)
            else:
                status_atual = response.status_code if response is not None else "Sem Resposta"
                return "ERRO_API", f"❌ Erro na API para {nome_arquivo}: Status {status_atual}"

        # Captura o ano em que o script está rodando para a regra do Cache Negativo
        ano_atual = datetime.date.today().year

        if todos_dados:
            df = pd.DataFrame(todos_dados)
            
            # ==============================================================
            # 🛡️ FILTRO DEFENSIVO ADAPTADO
            # ==============================================================
            ano_esperado = str(task['ano_referencia'])
            if 'exercicio_orcamento' in df.columns:
                df['exercicio_orcamento_str'] = df['exercicio_orcamento'].astype(str)
                df = df[df['exercicio_orcamento_str'].str.startswith(ano_esperado)]
                df = df.drop(columns=['exercicio_orcamento_str'])
            
            if df.empty:
                # 👻 CACHE NEGATIVO 1: A API mandou lixo de outro ano.
                if int(task['ano_referencia']) < ano_atual:
                    pd.DataFrame().to_parquet(caminho_arquivo, engine='pyarrow', compression='snappy')
                    return "VAZIO", f"⚠️ Vazio: {nome_arquivo} - Lixo descartado. Fantasma criado."
                return "VAZIO", f"⚠️ Vazio: {nome_arquivo} - Descartado por inconsistência de ano."
            # ==============================================================

            df['municipio_referencia'] = task['municipio_nome']
            df.to_parquet(caminho_arquivo, engine='pyarrow', compression='snappy')
            return "BAIXADO", f"✅ Criado com sucesso: {nome_arquivo} ({len(df)} linhas)"

        # 👻 CACHE NEGATIVO 2: A API não retornou absolutamente nada.
        if int(task['ano_referencia']) < ano_atual:
            pd.DataFrame().to_parquet(caminho_arquivo, engine='pyarrow', compression='snappy')
            return "VAZIO", f"⚠️ Vazio: {nome_arquivo} - Fantasma gerado (Poupará a API futuramente)."

        return "VAZIO", f"⚠️ Vazio: {nome_arquivo} - Sem registros (Ignorado no cache de {ano_atual})."

    except Exception as e:
        return "ERRO_CONEXAO", f"❌ Erro inesperado em {nome_arquivo}: {str(e)}"

# Gera a lista de tarefas a partir dos filtros aplicados na interface do Streamlit
def gerar_tarefas(ano, municipio_selecionado, endpoints_alvo=None):
    """Gera tarefas dinamicamente lendo URLs, frequências e parâmetros do endpoints.json."""
    municipios = carregar_municipios()
    if municipio_selecionado:
        municipios = [m for m in municipios if m['codigo_municipio'] == municipio_selecionado['codigo_municipio']]

    lista_de_tarefas = []

    # 1. CARREGA A VERDADE UNIVERSAL (endpoints.json)
    try:
        with open('endpoints.json', 'r', encoding='utf-8') as f:
            config_json = json.load(f)
    except Exception as e:
        print(f"🚨 [EXTRACTION] Erro crítico ao ler endpoints.json: {e}")
        return []

    exercicio = int(f"{ano}00")

    # =========================================================================
    # FUNÇÃO AUXILIAR: Monta os parâmetros dinamicamente baseado no JSON
    # =========================================================================
    def montar_params(parametros_obrigatorios, codigo_mun, mes=None):
        """
        Constrói o dicionário de parâmetros da API a partir da lista de 
        parâmetros obrigatórios declarados no endpoints.json.
        """
        params = {
            "$format": "json",
            "$count": 1000,
            "$start_index": 0
        }
        
        for param in parametros_obrigatorios:
            if param == "exercicio_orcamento":
                params[param] = exercicio
            elif param == "codigo_municipio":
                params[param] = codigo_mun
            elif param == "data_referencia_doc":
                # Formato YYYYMM (ex: 202401, 202402...)
                if mes is not None:
                    params[param] = int(f"{ano}{str(mes).zfill(2)}")
            elif param == "data_inicio":
                params[param] = f"{ano}-01-01"
            elif param == "data_fim":
                params[param] = f"{ano}-12-31"
            # Adicione aqui novos parâmetros conforme forem surgindo
            # elif param == "outro_param":
            #     params[param] = ...
        
        return params

    # =========================================================================
    # 2. ITERA PELOS ENDPOINTS E GERA AS TAREFAS
    # =========================================================================
    for nome_display, meta in config_json.items():
        prefixo = meta.get("prefixo")
        url = meta.get("endpoint")
        freq = meta.get("frequencia", "mensal").lower().strip()
        parametros_obrigatorios = meta.get("parametros_obrigatorios", [])

        if not prefixo or not url:
            continue

        # FILTRO BLINDADO: Aceita prefixo OU nome de display
        if endpoints_alvo:
            if (prefixo not in endpoints_alvo) and (nome_display not in endpoints_alvo):
                continue

        # Validação: se não houver parâmetros obrigatórios, não gera tarefas
        if not parametros_obrigatorios:
            print(f"⚠️ [EXTRACTION] {nome_display} não tem 'parametros_obrigatorios' no JSON. Ignorando.")
            continue

        # Decide quantas iterações baseado na frequência
        iteracoes_mensais = range(1, 13) if freq == "mensal" else [None]

        for mes in iteracoes_mensais:
            for m in municipios:
                # Monta o nome do arquivo (com ou sem mês)
                if mes is not None:
                    caminho = os.path.join('data', f"{prefixo}_{ano}_{mes:02d}_{m['codigo_municipio']}.parquet")
                else:
                    caminho = os.path.join('data', f"{prefixo}_{ano}_{m['codigo_municipio']}.parquet")

                # Monta os parâmetros dinamicamente
                params = montar_params(parametros_obrigatorios, m['codigo_municipio'], mes)

                lista_de_tarefas.append({
                    'dataset_nome': prefixo,
                    'url': url,
                    'municipio_nome': m['nome_municipio'],
                    'ano_referencia': ano,
                    'params': params,
                    'caminho_arquivo': caminho
                })

    return lista_de_tarefas

# Renderiza a página de extração no Streamlit, com os controles de filtro e a área de logs
def executar_pipeline(ano, municipio_selecionado=None, endpoints_alvo=None, log_func=print):
    os.makedirs('data', exist_ok=True)
    periodo_str = f"Ano {ano}"

    log_func(f"🚀 [{periodo_str}] Calculando fila de ingestão...")

    # 1. A chamada já nasce acoplada com a nova assinatura dinâmica:
    tarefas = gerar_tarefas(ano, municipio_selecionado, endpoints_alvo=endpoints_alvo)
    
    if not tarefas:
        log_func(f"⚠️ [{periodo_str}] Nenhuma tarefa gerada para os critérios informados.")
        return

    log_func(f"📦 [{periodo_str}] Matriz montada: {len(tarefas)} requisições prontas para despacho.")

    baixados, ignorados, erros = 0, 0, 0
    erros_consecutivos = 0  
    
    # max_workers=3 é o "Ponto Doce" comprovado para não tomar IP Ban de firewall governamental
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futuros = {executor.submit(processar_lote, tarefa): tarefa for tarefa in tarefas}
        pbar = tqdm(concurrent.futures.as_completed(futuros), total=len(tarefas), desc=f"Ingerindo {periodo_str}")
        
        for futuro in pbar:
            try:
                status, msg_log = futuro.result()
                
                # ==============================================================
                # 🛡️ UPGRADE 1: O Termômetro de Saúde Expandido
                # ==============================================================
                # Soma erro se caiu a internet (ERRO_CONEXAO), se o servidor deles engasgou (ERRO_API) ou WAF 429
                if status in ["ERRO_CONEXAO", "ERRO_API"] or "Status 429" in msg_log or "Status 50" in msg_log:
                    erros_consecutivos += 1
                else:
                    erros_consecutivos = 0  # Tomou fôlego e deu certo? Zera o contador.
                
                # Defesa Nível 1: Resfriamento (O servidor do Estado deu uma engasgada)
                if erros_consecutivos == 15: 
                    log_func("🚨 [WAF ALERTA] TCE barrando requisições da API. Dormindo 30s para esfriar o IP...")
                    time.sleep(30)
                
                # ==============================================================
                # 💀 UPGRADE 2: O "Circuit Breaker" Nuclear (Salva-Vidas)
                # ==============================================================
                elif erros_consecutivos >= 30:
                    log_func("💀 [CIRCUIT BREAKER] Portal do TCE indisponível. Abortando fila para não travar o servidor!")
                    executor.shutdown(wait=False, cancel_futures=True) # Ejetar!
                    break
                # ==============================================================

                # Despacho de Log blindado contra closures/lambdas do Streamlit
                nome_func_log = getattr(log_func, '__name__', '')
                if nome_func_log == "log_colorido_terminal":
                    log_func(msg_log)
                elif log_func == print:
                    pbar.write(msg_log)
                else:
                    log_func(msg_log)
                
                if status == "BAIXADO": baixados += 1
                elif status == "IGNORADO": ignorados += 1
                else: erros += 1

            except Exception as e:
                msg_falha = f"❌ Erro crítico de alocação em thread isolada: {e}"
                pbar.write(msg_falha) if log_func == print else log_func(msg_falha)
                erros += 1
                erros_consecutivos += 1 

    log_func(f"🏁 [{periodo_str}] Ingestão finalizada -> 📥 {baixados} novos | ⏭️ {ignorados} já no HD | ⚠️ {erros} falhas.")

def render_extraction_page():
    # --- INICIALIZAÇÃO DO ESTADO GLOBAL ---
    if 'log_messages' not in st.session_state:
        st.session_state.log_messages = []
    if 'arquivos_criados' not in st.session_state:
        st.session_state.arquivos_criados = []

    # 1. CARREGA O JSON NO TOPO DA PÁGINA
    try:
        with open('endpoints.json', 'r', encoding='utf-8') as f:
            config_json = json.load(f)
    except Exception as e:
        st.error(f"🚨 Erro crítico: Não foi possível ler 'endpoints.json': {e}")
        return

    # --- SIDEBAR (CONFIGURAÇÕES) ---
    with st.sidebar:
        st.header("⚙️ Parâmetros de Extração")
        
        hoje = datetime.date.today()
        ano_selecionado = st.number_input(
            "Ano de Referência",
            min_value=2008,
            max_value=2026,
            value=hoje.year,
            step=1
        )

        lista_municipios = carregar_municipios()
        opcoes_mun = {f"{m['nome_municipio']} ({m['codigo_municipio']})": m for m in lista_municipios}
        
        mun_sel_ui = st.selectbox(
            "🏙️ Município", 
            options=["Todos"] + list(opcoes_mun.keys())
        )

        mun_objeto = opcoes_mun.get(mun_sel_ui, None)

        st.subheader("📦 Tipo de Dado")
        
        # [CORREÇÃO 1] Ressuscitamos o Selectbox que havia sumido:
        opcoes_tabela = ["🌐 [ EXTRAIR TODOS OS ENDPOINTS ]"] + list(config_json.keys())
        tipo_escolhido = st.selectbox("Selecione o endpoint:", options=opcoes_tabela)

        if tipo_escolhido == "🌐 [ EXTRAIR TODOS OS ENDPOINTS ]":
            endpoints_alvo = None  
        else:
            prefixo_alvo = config_json[tipo_escolhido]["prefixo"]
            endpoints_alvo = [prefixo_alvo]

        st.divider()

        # [CORREÇÃO 2] Ressuscitamos o Botão de disparo:
        btn_extrair = st.button("🚀 Executar Extração", use_container_width=True, type="primary")

        # --- ÁREA DE LOGS E ARQUIVOS CRIADOS NA SIDEBAR ---
        log_placeholder = st.empty()

        if btn_extrair:
            st.session_state.log_messages = []
            st.session_state.arquivos_criados = []
            inicio_extracao = time.time()
            
            def stream_log(msg):
                st.session_state.log_messages.append(msg)
                
                arquivos_atuais = []
                if os.path.exists('data'):
                    for f in glob.glob(os.path.join('data', '*.parquet')):
                        if os.path.getmtime(f) >= inicio_extracao:
                            nome_arq = os.path.basename(f)
                            if nome_arq not in arquivos_atuais:
                                arquivos_atuais.append(nome_arq)
                
                st.session_state.arquivos_criados = sorted(arquivos_atuais)

                with log_placeholder.container():
                    st.caption("🪵 Logs de Extração (Tempo Real)")
                    st.code("\n".join(st.session_state.log_messages[-50:]), language="text")
                    
                    if st.session_state.arquivos_criados:
                        st.caption("📁 Arquivos criados no disco:")
                        for arq in st.session_state.arquivos_criados:
                            st.markdown(f"🔹 `{arq}`")

            # [CORREÇÃO 3] Limpado o recálculo redundante. O mun_objeto já carrega a verdade:
            with st.spinner(f"Buscando {tipo_escolhido} (Ano {ano_selecionado}) no TCE..."):
                try:
                    executar_pipeline(
                        ano=int(ano_selecionado), 
                        municipio_selecionado=mun_objeto, # <-- Passado limpo direto na veia!
                        endpoints_alvo=endpoints_alvo, 
                        log_func=stream_log
                    )
                    st.success("Extração concluída com sucesso!")
                except Exception as e:
                    st.error(f"Erro durante a extração: {e}")
        
        elif st.session_state.log_messages:
            with log_placeholder.container():
                if st.session_state.arquivos_criados:
                    st.subheader("📁 Últimos Arquivos Salvos")
                    for arq in st.session_state.arquivos_criados:
                        st.markdown(f"✅ `{arq}`")
                    st.divider()

                with st.expander("🪵 Ver Histórico de Logs", expanded=False):
                    st.code("\n".join(st.session_state.log_messages), language="text")
                    if st.button("Limpar Logs e Histórico", key="clear_logs"):
                        st.session_state.log_messages = []
                        st.session_state.arquivos_criados = []
                        st.rerun()

    # --- ÁREA PRINCIPAL ---
    st.header("Visualizar Dados")

    def carregar_e_exibir_dados_dinamico(nome_display, meta):
        pasta_dados = 'data'
        prefixo = meta.get("prefixo")
        freq = meta.get("frequencia", "mensal").lower().strip()

        if not prefixo:
            return

        if not os.path.exists(pasta_dados):
            st.warning(f"📂 O diretório `{pasta_dados}` ainda não foi criado. Realize uma extração primeiro.")
            return

        # Busca estrita com traço (_) para não misturar 'notas' com 'notas_empenho'
        prefixo_busca = f"{prefixo}_"
        arquivos_disponiveis = [
            f for f in os.listdir(pasta_dados) 
            if f.startswith(prefixo_busca) and f.endswith('.parquet')
        ]

        if not arquivos_disponiveis:
            st.warning(f"⚠️ Nenhum arquivo de `{nome_display}` encontrado no disco.")
            st.info("Configure os parâmetros na barra lateral e clique em 'Executar Extração'.")
            return

        # =========================================================================
        # 1. EXTRAÇÃO DE METADADOS (Matemática do Miolo à prova de tabelas anuais)
        # =========================================================================
        anos, meses, municipios = set(), set(), set()

        for arq in arquivos_disponiveis:
            miolo = arq.replace(prefixo_busca, "").replace(".parquet", "")
            partes = miolo.split('_')

            if freq == "mensal" and len(partes) >= 3:
                anos.add(partes[0])
                meses.add(partes[1])
                municipios.add(partes[2])
            elif freq in ["anual", "intervalo_data"] and len(partes) >= 2:
                anos.add(partes[0])
                municipios.add(partes[1]) # Na carga anual, o índice [1] é sempre o município

        st.markdown("### 🛠️ Filtros de Inspeção")
        col1, col2, col3 = st.columns(3)

        with col1:
            ano_sel = st.selectbox("📅 Ano", ["Todos"] + sorted(list(anos)), key=f"ext_ano_{prefixo}")
            
        with col2:
            if freq == "mensal":
                mes_sel = st.selectbox("📆 Mês", ["Todos"] + sorted(list(meses)), key=f"ext_mes_{prefixo}")
            else:
                st.selectbox("📆 Mês", ["N/A (Carga Anual)"], disabled=True, key=f"ext_mes_dis_{prefixo}")
                mes_sel = "Todos"
                
        with col3:
            mun_sel = st.selectbox("🏙️ Município", ["Todos"] + sorted(list(municipios)), key=f"ext_mun_{prefixo}")

        # =========================================================================
        # 2. FILTRAGEM DOS ARQUIVOS DISPONÍVEIS
        # =========================================================================
        arquivos_filtrados = []
        for arq in arquivos_disponiveis:
            miolo = arq.replace(prefixo_busca, "").replace(".parquet", "")
            p = miolo.split('_')

            match_ano = (ano_sel == "Todos") or (p[0] == ano_sel)
            match_mun = (mun_sel == "Todos") or (p[-1] == mun_sel)
            match_mes = (mes_sel == "Todos") or (p[1] == mes_sel) if freq == "mensal" else True

            if match_ano and match_mes and match_mun:
                arquivos_filtrados.append(arq)

        st.divider()

        # =========================================================================
        # 3. PRESERVAÇÃO INTEGRAL DA SUA LÓGICA DE MESCLAGEM E MULTISELECT
        # =========================================================================
        if arquivos_filtrados:
            st.success(f"🎯 Encontrado(s) **{len(arquivos_filtrados)}** arquivo(s) correspondente(s).")
            
            col_chk, _ = st.columns([1, 1])
            with col_chk:
                selecionar_todos = st.checkbox("✅ Selecionar todos os filtrados", value=False, key=f"chk_all_{prefixo}")
            
            if selecionar_todos:
                arquivos_selecionados = arquivos_filtrados
                st.info(f"📦 Todos os **{len(arquivos_filtrados)}** arquivos foram marcados para fusão.")
            else:
                arquivos_selecionados = st.multiselect(
                    "📄 Selecione os arquivos que deseja mesclar:", 
                    options=arquivos_filtrados,
                    default=None,
                    key=f"multi_{prefixo}"
                )
            
            if arquivos_selecionados:
                botao_label = f"📊 Carregar e Mesclar {len(arquivos_selecionados)} arquivo(s)"
                
                if st.button(botao_label, use_container_width=True, type="primary", key=f"btn_merge_{prefixo}"):
                    try:
                        with st.spinner("Decodificando e concatenando bases Parquet em alta velocidade..."):
                            lista_dfs = []
                            
                            for arquivo in arquivos_selecionados:
                                caminho_completo = os.path.join(pasta_dados, arquivo)
                                df_individual = pd.read_parquet(caminho_completo)
                                if not df_individual.empty:
                                    lista_dfs.append(df_individual)
                            
                            if lista_dfs:
                                df_final = pd.concat(lista_dfs, ignore_index=True)
                                
                                m1, m2 = st.columns(2)
                                m1.metric("Total de Linhas Combinadas", f"{len(df_final):,}".replace(",", "."))
                                m2.metric("Total de Colunas", len(df_final.columns))
                                
                                st.markdown("### 📋 Tabela de Dados Combinada")
                                st.dataframe(df_final, use_container_width=True, hide_index=True)
                                
                                nome_download = f"consolidado_{prefixo}"
                                if ano_sel != "Todos": nome_download += f"_{ano_sel}"
                                if mun_sel != "Todos": nome_download += f"_{mun_sel}"
                                nome_download += ".csv"
                                
                                st.download_button(
                                    label="📥 Exportar Base unificada para CSV (Excel)",
                                    data=df_final.to_csv(index=False, sep=';', encoding='utf-8-sig').encode('utf-8-sig'),
                                    file_name=nome_download,
                                    mime='text/csv',
                                    use_container_width=True,
                                    key=f"dl_consolidado_{prefixo}"
                                )
                            else:
                                st.warning("⚠️ Os arquivos selecionados não continham registros válidos.")
                                
                    except Exception as e:
                        st.error(f"❌ Erro crítico na operação de I/O do Pandas: {e}")
            else:
                st.info("💡 Escolha os arquivos acima ou marque 'Selecionar todos' para gerar a visualização.")
        else:
            st.error("❌ Nenhum arquivo encontrado para a combinação de filtros selecionada.")


    # =============================================================================
    # GERAÇÃO DINÂMICA DAS ABAS (O fim definitivo do DATA_MAP engessado)
    # =============================================================================
    try:
        with open('endpoints.json', 'r', encoding='utf-8') as f:
            CONFIG_GERAL_JSON = json.load(f)
    except Exception as e:
        st.error(f"🚨 Não foi possível carregar o arquivo endpoints.json: {e}")
        st.stop()

    nomes_das_abas = list(CONFIG_GERAL_JSON.keys())
    componentes_abas = st.tabs(nomes_das_abas)

    for idx, nome_display in enumerate(nomes_das_abas):
        with componentes_abas[idx]:
            carregar_e_exibir_dados_dinamico(nome_display, CONFIG_GERAL_JSON[nome_display])