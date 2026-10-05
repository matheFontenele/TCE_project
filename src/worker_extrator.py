import os
import sys
import time
import subprocess
import logging
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


def executar_extracao_por_anos(ano_inicio, ano_fim, mes_fim=12):
    """Dispara o main.py; qualquer falha pendente sobe para o Airflow."""
    raiz = _raiz_projeto()
    main_path = os.path.join(raiz, "src", "main.py")
    comando = [sys.executable, "-u", main_path, str(ano_inicio), str(ano_fim), str(mes_fim)]
    ambiente = os.environ.copy()
    ambiente["TCE_AIRFLOW_RUN"] = "1"

    logger = logging.getLogger("airflow.task")
    logger.info("[BACKFILL][INICIO] Período %s-01 até %s-%02d", ano_inicio, ano_fim, mes_fim)
    logger.info("[BACKFILL][COMANDO] %s", " ".join(comando))

    processo = subprocess.Popen(
        comando,
        cwd=raiz,
        env=ambiente,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    try:
        if processo.stdout is not None:
            for linha in processo.stdout:
                linha = linha.rstrip()
                if linha:
                    logger.info("[EXTRATOR] %s", linha)
    finally:
        if processo.stdout is not None:
            processo.stdout.close()

    codigo_saida = processo.wait()
    if codigo_saida != 0:
        logger.error("[BACKFILL][FALHA] Processo terminou com código %s", codigo_saida)
        raise subprocess.CalledProcessError(codigo_saida, comando)

    logger.info("[BACKFILL][OK] Extração concluída (código %s).", codigo_saida)
    return codigo_saida


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
