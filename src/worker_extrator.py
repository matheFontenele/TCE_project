# src/worker_extrator.py
import time
import schedule
import subprocess
from datetime import datetime

def calcular_anos_alvo():
    hoje = datetime.now()
    anos_envolvidos = set()
    
    for i in range(1, 4): # Volta 1, 2 e 3 meses
        mes_calculado = hoje.month - i
        ano_calculado = hoje.year
        
        # Se o mês der zero ou negativo, significa que voltamos para o ano anterior
        if mes_calculado <= 0:
            ano_calculado -= 1
            
        anos_envolvidos.add(ano_calculado)
        
    # Retorna o menor e o maior ano do intervalo descoberto
    return min(anos_envolvidos), max(anos_envolvidos)

def rotina_mensal_extracao():
    hoje = datetime.now()
    
    # Trava de Segurança: Executa APENAS no dia 01 do mês
    if hoje.day != 1:
        return
        
    print(f"\n{'='*60}")
    print(f"🤖 [WORKER] Iniciando Extração Automática Mensal ({hoje.strftime('%d/%m/%Y %H:%M')})")
    print(f"{'='*60}")
    
    ano_inicio, ano_fim = calcular_anos_alvo()
    print(f"📡 Anos identificados para o retroativo de 3 meses: {ano_inicio} a {ano_fim}")
    
    try:
        # Chama o seu main.py usando o Modo Autônomo
        # É o equivalente a digitar no terminal: python src/main.py 2026 2026
        comando = ["python", "src/main.py", str(ano_inicio), str(ano_fim)]
        
        print(f"🚀 Disparando comando: {' '.join(comando)}")
        
        # O subprocess.run pausa este script até o main.py terminar, exibindo os logs no console do Docker!
        processo = subprocess.run(comando, check=True)
        
        print(f"\n✅ [WORKER] Rotina concluída com sucesso (Código {processo.returncode}). Dormindo até o próximo mês.")
        
    except subprocess.CalledProcessError as e:
        print(f"\n❌ [WORKER] Ocorreu um erro na extração do main.py. Código de erro: {e.returncode}")
    except Exception as e:
        print(f"\n❌ [WORKER] Erro crítico no robô agendador: {e}")

# =========================================================================
# AGENDADOR (CRON)
# =========================================================================
# Verifica todos os dias de madrugada. A madrugada evita sobrecarregar o servidor do TCE.
HORARIO_EXECUCAO = "03:00"
schedule.every().day.at(HORARIO_EXECUCAO).do(rotina_mensal_extracao)

if __name__ == "__main__":
    print(f"⏳ Robô Agendador Iniciado! Checando gatilhos diariamente às {HORARIO_EXECUCAO}...")
    
    # Loop infinito extremamente leve (não consome CPU)
    while True:
        schedule.run_pending()
        time.sleep(60) # Acorda a cada 1 minuto, checa se é a hora certa, e volta a dormir