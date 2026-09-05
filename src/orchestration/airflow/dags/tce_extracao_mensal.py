"""
DAG de extração mensal dos dados do TCE.
"""
import logging
import os
import sys
from datetime import datetime, timedelta

# 🔒 Garantir que o worker seja importável
if "/app/src" not in sys.path:
    sys.path.insert(0, "/app/src")
    logging.info("Adicionado /app/src ao sys.path")

from airflow.decorators import dag, task


def notify_failure(context):
    dag_id = context["dag"].dag_id
    ti = context["task_instance"]
    execution_date = context.get("logical_date") or context.get("execution_date")

    mensagem = (
        ":red_circle: *Falha no pipeline TCE*\n"
        f"*DAG:* `{dag_id}`\n"
        f"*Task:* `{ti.task_id}`\n"
        f"*Execução:* `{execution_date}`\n"
        f"*Logs:* {ti.log_url}"
    )

    webhook = os.getenv("SLACK_WEBHOOK_URL")
    if webhook:
        import requests
        try:
            requests.post(webhook, json={"text": mensagem}, timeout=10)
        except Exception:
            logging.exception("Erro ao enviar alerta para o Slack.")
    else:
        logging.error("FALHA: %s | %s | %s", dag_id, ti.task_id, execution_date)


default_args = {
    "owner": "tce",
    "depends_on_past": False,
    "retries": 3,
    "retry_delay": timedelta(minutes=10),
    "on_failure_callback": notify_failure,
}


@dag(
    dag_id="tce_extracao_mensal",
    description="Extração mensal retroativa (3 meses) dos dados do TCE",
    schedule="0 3 1 * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["tce", "extracao", "mensal"],
)
def tce_extracao_mensal():

    @task
    def calcular_janela(**context):
        logging.info("=== INÍCIO calcular_janela ===")
        try:
            logging.info("sys.path: %s", sys.path[:3])
            
            hoje = context["data_interval_end"].date()
            logging.info("Data de referência: %s", hoje)
            
            from worker_extrator import calcular_anos_alvo
            logging.info("Import do worker_extrator OK")
            
            ano_inicio, ano_fim = calcular_anos_alvo(hoje)
            logging.info("Janela calculada: %s-%s", ano_inicio, ano_fim)
            
            return {"ano_inicio": ano_inicio, "ano_fim": ano_fim, "referencia": str(hoje)}
            
        except Exception as e:
            logging.exception("ERRO em calcular_janela: %s", str(e))
            raise
            
        finally:
            logging.info("=== FIM calcular_janela ===")

    @task
    def executar_extracao(janela: dict):
        logging.info("=== INÍCIO executar_extracao ===")
        try:
            from worker_extrator import executar_extracao_por_anos
            logging.info("Chamando executar_extracao_por_anos(%s, %s)", 
                        janela["ano_inicio"], janela["ano_fim"])
            
            executar_extracao_por_anos(janela["ano_inicio"], janela["ano_fim"])
            return janela
            
        except Exception as e:
            logging.exception("ERRO em executar_extracao: %s", str(e))
            raise
            
        finally:
            logging.info("=== FIM executar_extracao ===")

    @task
    def notificar_sucesso(janela: dict):
        logging.info(
            "✅ Pipeline mensal concluído: referência %s (anos %s-%s)",
            janela["referencia"], janela["ano_inicio"], janela["ano_fim"],
        )

    notificar_sucesso(executar_extracao(calcular_janela()))


tce_extracao_mensal()