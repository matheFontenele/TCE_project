import os
import sys
import time
import subprocess
from datetime import datetime

try:
    import schedule
except ImportError:
    schedule = None


def _raiz_projeto():
    return os.getenv("PROJECT_ROOT", "/app" if os.path.isdir("/app") else os.getcwd())


def calcular_anos_alvo(hoje=None):
    """Calcula os anos que cobrem o retroativo de 3 meses."""
    hoje = hoje or datetime.now()
    anos_envolvidos = set()

    for i in range(1, 4):
        mes_calculado = hoje.month - i
        ano_calculado = hoje.year
        if mes_calculado <= 0:
            ano_calculado -= 1
        anos_envolvidos.add(ano_calculado)

    return min(anos_envolvidos), max(anos_envolvidos)


def executar_extracao_por_anos(ano_inicio, ano_fim):
    """Dispara o main.py. Se falhar, o erro sobe para o Airflow (retry automatico)."""
    raiz = _raiz_projeto()
    main_path = os.path.join(raiz, "src", "main.py")
    comando = [sys.executable, main_path, str(ano_inicio), str(ano_fim)]

    print(f"🚀 Disparando comando: {' '.join(comando)}")
    processo = subprocess.run(comando, check=True, cwd=raiz)
    print(f"✅ Extracao concluida (codigo {processo.returncode}).")
    return processo.returncode


def executar_extracao_mensal(hoje=None):
    """Rotina mensal completa. O QUANDO rodar e decisao do Airflow."""
    hoje = hoje or datetime.now()
    print("=" * 60)
    print(f"🤖 [WORKER] Iniciando Extracao Mensal ({hoje.strftime('%d/%m/%Y')})")
    print("=" * 60)

    ano_inicio, ano_fim = calcular_anos_alvo(hoje)
    print(f"📡 Anos (retroativo 3 meses): {ano_inicio} a {ano_fim}")
    executar_extracao_por_anos(ano_inicio, ano_fim)


def rotina_mensal_extracao():
    """Modo legado: trava do dia 1o."""
    hoje = datetime.now()
    if hoje.day != 1:
        return
    executar_extracao_mensal(hoje)


if __name__ == "__main__":
    if len(sys.argv) == 3:
        executar_extracao_por_anos(int(sys.argv[1]), int(sys.argv[2]))
    else:
        HORARIO_EXECUCAO = "03:00"
        schedule.every().day.at(HORARIO_EXECUCAO).do(rotina_mensal_extracao)
        print(f"⏳ Robo Agendador Iniciado! Checando diariamente as {HORARIO_EXECUCAO}...")
        while True:
            schedule.run_pending()
            time.sleep(60)
