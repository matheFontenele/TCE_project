import os
import sys
import datetime
import json
from tqdm import tqdm
from modules.extraction import processar_lote, gerar_tarefas, executar_pipeline, formatar_log_terminal

# =========================================================================
# WIZARD INTERATIVO DE TERMINAL (CLI ENGINE)
# =========================================================================

def carregar_json_seguro(caminho):
    try:
        with open(caminho, 'r', encoding='utf-8') as f:
            conteudo_arquivo = json.load(f)

            if isinstance(conteudo_arquivo, dict):
                return conteudo_arquivo.get("elements", conteudo_arquivo)
            
            return conteudo_arquivo
            
    except Exception as e:
        print(f"\033[31m❌ Erro crítico no código ao ler '{caminho}': {e}\033[0m")
        sys.exit(1)


def questionar_municipio(lista_muns):
    mapa_cod = {m['codigo_municipio']: m for m in lista_muns}
    
    while True:
        resp = input("\n🏙️  \033[1mMUNICÍPIO\033[0m [Código exato | 'L' para Listar | 'T' ou Enter para Todos]: ").strip().upper()

        if resp in ['T', '']:
            print("   └── Alvo: \033[33mTodos os Municípios do Estado\033[0m")
            return None

        if resp == 'L':
            print("\n   ─── LISTA OFICIAL DE MUNICÍPIOS (TCE-CE) ───")
            muns_ord = sorted(lista_muns, key=lambda x: x['nome_municipio'])
            # Imprime em 3 colunas paralelas para não estourar o buffer da tela
            for i in range(0, len(muns_ord), 3):
                bloco = muns_ord[i:i+3]
                linha = "   ".join([f"\033[36m{m['codigo_municipio']}\033[0m: {m['nome_municipio'][:16]:<16}" for m in bloco])
                print(f"   {linha}")
            print("   ────────────────────────────────────────────")
            continue
            
        if resp in mapa_cod:
            escolhido = mapa_cod[resp]
            print(f"   └── Alvo: \033[32m{escolhido['nome_municipio']} ({resp})\033[0m")
            return escolhido
            
        print("   \033[31m❌ Código não encontrado. Digite 'L' para consultar a tabela.\033[0m")


def questionar_endpoint(config_ends):
    lista_opcoes = list(config_ends.items())
    mapa_prefixos = {meta["prefixo"].lower(): meta["prefixo"] for _, meta in lista_opcoes}
    
    # Cria atalhos numéricos [1, 2... N] dinamicamente lendo a posição do JSON
    mapa_num = {}
    for idx, (_, meta) in enumerate(lista_opcoes):
        n = idx + 1
        mapa_num[str(n)] = meta["prefixo"]
        mapa_num[f"{n:02d}"] = meta["prefixo"]

    while True:
        resp = input("\n📦  \033[1mENDPOINT\033[0m [Prefixo ou Nº | 'L' para Listar | 'T' ou Enter para Todos]: ").strip().lower()
        
        if resp in ['t', '']:
            print("   └── Alvo: \033[33mTodos os Endpoints cadastrados\033[0m")
            return None
            
        if resp == 'l':
            print("\n   ─── ENDPOINTS CADASTRADOS NO SISTEMA ───")
            for idx, (nome_ui, meta) in enumerate(lista_opcoes):
                print(f"   [\033[33m{idx+1:02d}\033[0m] \033[36m{meta['prefixo']:<38}\033[0m ({nome_ui})")
            print("   ────────────────────────────────────────")
            continue
            
        alvo = mapa_num.get(resp) or mapa_prefixos.get(resp)
        
        if alvo:
            print(f"   └── Alvo: \033[32m{alvo}\033[0m")
            return [alvo]
            
        print("   \033[31m❌ Opção inválida. Digite 'L' para ver os índices correspondentes.\033[0m")


def questionar_anos():
    ano_atual = datetime.date.today().year
    while True:
        resp = input(f"\n📅  \033[1mANO(S)\033[0m [Ex: 2026 | Período: 2008-2026 | 'T' ou Enter para 2008..{ano_atual}]: ").strip().upper()
        
        if resp in ['T', '']:
            anos = list(range(2008, ano_atual + 1))
            print(f"   └── Alvo: \033[33m{anos[0]} até {anos[-1]}\033[0m")
            return anos
            
        if '-' in resp:
            try:
                p1, p2 = resp.split('-')
                a1, a2 = int(p1.strip()), int(p2.strip())
                anos = list(range(min(a1, a2), max(a1, a2) + 1))
                print(f"   └── Alvo: \033[32mPeríodo {anos[0]} ➔ {anos[-1]}\033[0m")
                return anos
            except ValueError:
                pass
        else:
            try:
                ano_v = int(resp)
                print(f"   └── Alvo: \033[32mAno estrito {ano_v}\033[0m")
                return [ano_v]
            except ValueError:
                pass
                
        print("   \033[31m❌ Formato incorreto. Digite um ano (ex: 2025) ou intervalo (ex: 2008-2026).\033[0m")


# =========================================================================
# PONTO DE ENTRADA DO SISTEMA
# =========================================================================
if __name__ == "__main__":
    def log_colorido_terminal(msg):
        if os.getenv("TCE_AIRFLOW_RUN") == "1":
            print(msg, flush=True)
        else:
            tqdm.write(formatar_log_terminal(msg))

    anos_alvos = []
    mun_alvo = None
    endpoints_alvo = None
    mes_fim = 12

    # 1. ENCRUZILHADA DE COMPORTAMENTO
    if len(sys.argv) > 1:
        # =====================================================================
        # MODO AUTÔNOMO (Acionado pelo Docker Compose / Cronjobs)
        # Exemplo: python main.py 2008 2026
        # =====================================================================
        ano_inicio = int(sys.argv[1])
        ano_fim = int(sys.argv[2]) if len(sys.argv) >= 3 else ano_inicio
        mes_fim = int(sys.argv[3]) if len(sys.argv) >= 4 else 12
        anos_alvos = list(range(min(ano_inicio, ano_fim), max(ano_inicio, ano_fim) + 1))
        
        print(f"\033[36m🤖 [MODO AUTÔNOMO] Disparando extração em lote para: {anos_alvos}\033[0m")

    else:
        # =====================================================================
        # MODO INTERATIVO (Acionado pelo desenvolvedor no terminal via 'tce')
        # =====================================================================
        print("\033[H\033[J", end="") # Limpa a tela de forma nativa via ANSI
        print("\033[36m" + "═"*64)
        print(" 🏛️   TCE-CE INGESTION ENGINE — TERMINAL INTERATIVO")
        print("═"*64 + "\033[0m")

        base_muns = carregar_json_seguro('municipios.json')
        base_ends = carregar_json_seguro('endpoints.json')

        mun_alvo = questionar_municipio(base_muns)
        endpoints_alvo = questionar_endpoint(base_ends)
        anos_alvos = questionar_anos()

        # Quadro de Confirmação Pré-Voo
        print("\n\033[33m" + "─"*64)
        print(" 📋 RESUMO DO DESPACHO:")
        str_mun = f"{mun_alvo['nome_municipio']} ({mun_alvo['codigo_municipio']})" if mun_alvo else "TODOS OS MUNICÍPIOS"
        str_end = endpoints_alvo[0] if endpoints_alvo else "TODOS OS ENDPOINTS"
        
        print(f" 🔹 Município : {str_mun}")
        print(f" 🔹 Endpoint  : {str_end}")
        print(f" 🔹 Anos      : {anos_alvos}")
        print("─"*64 + "\033[0m")

        conf = input(" 🚀 Confirmar disparo do pipeline? [S/n]: ").strip().upper()
        if conf == 'N':
            print("\n\033[31m🛑 Operação cancelada pelo operador.\033[0m\n")
            sys.exit(0)

    # =========================================================================
    # EXECUÇÃO DA EXTRAÇÃO
    # =========================================================================
    print(f"\n\033[36m🚀 Inicializando pool de threads...\033[0m")

    try:
        falhas_totais = 0
        for ano in anos_alvos:
            print(f"\n\033[34m📅 Processando lote consolidado do ano: {ano}\033[0m")
            resumo = executar_pipeline(
                ano=ano,
                municipio_selecionado=mun_alvo,
                endpoints_alvo=endpoints_alvo,
                log_func=log_colorido_terminal,
                mes_max=mes_fim if ano == max(anos_alvos) else 12,
            )
            falhas_totais += resumo.get("erros", 0)
            if resumo.get("erros", 0):
                print(
                    f"[BACKFILL][PAUSADO] Ano {ano} terminou com "
                    f"{resumo['erros']} falhas/pendências. O Airflow retomará os arquivos "
                    "ausentes na próxima tentativa/execução.",
                    flush=True,
                )
                break

        if falhas_totais:
            sys.exit(2)
        print("\n✅ Ingestão finalizada sem falhas pendentes.\n", flush=True)

    except KeyboardInterrupt:
        print("\n\033[31m🛑 Interrompido à força (Ctrl+C). Fechando conexões...\033[0m\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n\033[31m❌ Erro crítico no motor de extração: {e}\033[0m\n")
        sys.exit(1)
