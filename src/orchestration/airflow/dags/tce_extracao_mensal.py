"""Backfill incremental diário dos dados do TCE e atualização das views DuckDB."""
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from airflow.decorators import dag, task


PROJECT_ROOT = Path(os.getenv("PROJECT_ROOT", "/app")).resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))


def notify_failure(context):
    dag_id = context["dag"].dag_id
    ti = context["task_instance"]
    execution_date = context.get("logical_date") or context.get("execution_date")
    exception = context.get("exception")
    tentativa = getattr(ti, "try_number", "?")

    mensagem = (
        ":red_circle: *Falha no pipeline de dados TCE*\n"
        f"*DAG:* `{dag_id}`\n"
        f"*Task:* `{ti.task_id}`\n"
        f"*Execução:* `{execution_date}`\n"
        f"*Tentativa:* `{tentativa}`\n"
        f"*Erro:* `{str(exception)[:1000] if exception else 'ver logs da task'}`\n"
        f"*Logs:* {ti.log_url}"
    )

    webhook = os.getenv("SLACK_WEBHOOK_URL")
    if webhook:
        import requests
        try:
            response = requests.post(webhook, json={"text": mensagem}, timeout=10)
            response.raise_for_status()
        except Exception:
            logging.exception("Não foi possível enviar o alerta de falha ao Slack.")
    else:
        logging.error("[AIRFLOW][FALHA] %s | %s | %s | %s", dag_id, ti.task_id, execution_date, exception)


default_args = {
    "owner": "tce",
    "depends_on_past": False,
    "retries": 3,
    "retry_delay": timedelta(minutes=30),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(hours=6),
    "on_failure_callback": notify_failure,
}


@dag(
    dag_id="tce_extracao_mensal",
    description="Verifica e recupera Parquets ausentes desde 2008; atualiza as views DuckDB",
    schedule="0 0 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["tce", "backfill", "parquet", "duckdb"],
)
def tce_extracao_mensal():

    @task(task_id="calcular_janela_backfill")
    def calcular_janela(**context):
        referencia = context["data_interval_end"].date()
        janela = {
            "ano_inicio": 2008,
            "ano_fim": referencia.year,
            "mes_fim": referencia.month,
            "referencia": referencia.isoformat(),
        }
        logging.info(
            "[BACKFILL][JANELA] Verificação de 2008-01 até %04d-%02d (referência %s)",
            janela["ano_fim"], janela["mes_fim"], janela["referencia"],
        )
        return janela

    @task(
        task_id="extrair_ausentes_e_reparar_invalidos",
        execution_timeout=timedelta(hours=23),
    )
    def executar_extracao(janela: dict):
        from worker_extrator import executar_extracao_por_anos

        logging.info(
            "[BACKFILL][INICIO] Período %s-01 até %s-%02d. "
            "Parquets válidos serão preservados; ausentes/corrompidos serão refeitos.",
            janela["ano_inicio"], janela["ano_fim"], janela["mes_fim"],
        )
        executar_extracao_por_anos(
            janela["ano_inicio"], janela["ano_fim"], janela["mes_fim"]
        )
        logging.info("[BACKFILL][OK] Extração finalizada sem falhas pendentes.")
        return janela

    @task(task_id="atualizar_views_duckdb", execution_timeout=timedelta(hours=1))
    def atualizar_views(janela: dict):
        script = PROJECT_ROOT / "scripts" / "criar_views.py"
        if not script.is_file():
            raise FileNotFoundError(f"Script de atualização de views não encontrado: {script}")

        logging.info("[DUCKDB][INICIO] Recriando views após a extração de %s.", janela["referencia"])
        processo = subprocess.Popen(
            [sys.executable, "-u", str(script)],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if processo.stdout is not None:
            for linha in processo.stdout:
                logging.info("[DUCKDB] %s", linha.rstrip())
        codigo_saida = processo.wait()
        if codigo_saida != 0:
            raise RuntimeError(f"criar_views.py terminou com código {codigo_saida}")
        logging.info("[DUCKDB][OK] Views atualizadas em %s/tce.duckdb.", PROJECT_ROOT)

    atualizar_views(executar_extracao(calcular_janela()))


tce_extracao_mensal()
