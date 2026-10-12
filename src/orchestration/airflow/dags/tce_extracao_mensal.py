"""Backfill incremental diário dos dados do TCE e atualização das views DuckDB."""
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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

    @task(task_id="verificar_armazenamento")
    def verificar_armazenamento():
        from src.quality.armazenamento import verificar_espaco_livre

        # Guarda antes de baixar: se o volume estiver crítico, falha aqui (e o
        # on_failure_callback avisa no Slack) em vez de encher o disco no meio
        # da extração. Limite em AIRFLOW_MIN_FREE_SPACE_GB (padrão 20 GB).
        info = verificar_espaco_livre(PROJECT_ROOT / "data")
        logging.info(
            "[ARMAZENAMENTO][OK] livre=%.1f GB de %.1f GB (mínimo %.1f GB) em %s",
            info["livre_gb"], info["total_gb"], info["minimo_gb"], info["caminho"],
        )
        return info

    @task(task_id="calcular_janela_backfill")
    def calcular_janela(**context):
        referencia = context["data_interval_end"].date()
        janela = {
            "ano_inicio": 2008,
            "ano_fim": referencia.year,
            "mes_fim": referencia.month,
            "referencia": referencia.isoformat(),
            "validar_linhas_desde": datetime.now(timezone.utc).isoformat(),
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

    @task(task_id="validar_parquets", execution_timeout=timedelta(hours=2))
    def validar_parquets(janela: dict):
        from src.quality.parquet_validator import validar_pasta_parquets, salvar_relatorio

        logging.info(
            "[VALIDAÇÃO][INICIO] Inspecionando Parquets em %s; validação de linhas desde %s.",
            PROJECT_ROOT / "data", janela["validar_linhas_desde"],
        )
        resultado = validar_pasta_parquets(
            PROJECT_ROOT / "data",
            validar_linhas_desde=datetime.fromisoformat(janela["validar_linhas_desde"]),
        )
        destino = PROJECT_ROOT / "data_quality" / "reports" / (
            f"parquet_validation_{janela['referencia']}.json"
        )
        salvar_relatorio(resultado, destino)

        logging.info(
            "[VALIDAÇÃO][RESUMO] arquivos=%s válidos=%s marcadores_vazios=%s "
            "erros_estruturais=%s avisos=%s linhas_verificadas=%s relatório=%s",
            resultado["arquivos_encontrados"], resultado["arquivos_validos"],
            resultado["marcadores_sem_dados"], resultado["erros_estruturais"],
            resultado["avisos"], resultado["linhas_verificadas"], destino,
        )
        for issue in resultado["issues"]:
            logging.warning(
                "[VALIDAÇÃO][%s][%s] %s | %s",
                issue["nivel"], issue["codigo"], issue["arquivo"], issue["mensagem"],
            )
        if resultado["issues_truncadas"]:
            logging.warning(
                "[VALIDAÇÃO] %s achados adicionais foram omitidos do log; consulte o relatório JSON.",
                resultado["issues_truncadas"],
            )
        logging.info("[VALIDAÇÃO][MODO_RELATÓRIO] A validação não bloqueia a atualização das views nesta fase.")
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

        # Reconstrói as tabelas de consulta no Postgres+pg_duckdb. Roda dentro
        # do container do Airflow, que enxerga os parquets em /app/data; o
        # container parquet-db os enxerga em /data (PARQUET_DB_SOURCE_DIR).
        if os.getenv("PARQUET_DB_ADMIN_PASSWORD"):
            refresh_script = PROJECT_ROOT / "scripts" / "refresh_parquet_db.py"
            if not refresh_script.is_file():
                raise FileNotFoundError(
                    f"Script de atualização das tabelas não encontrado: {refresh_script}"
                )
            ambiente = os.environ.copy()
            ambiente.update({
                "DATA_DIR": str(PROJECT_ROOT / "data"),
                "PARQUET_DB_SOURCE_DIR": "/data",
                "PARQUET_DB_HOST": os.getenv("PARQUET_DB_HOST", "parquet-db"),
                "PARQUET_DB_PORT": os.getenv("PARQUET_DB_PORT", "5432"),
            })
            logging.info("[PARQUET-DB][INICIO] Atualizando tabelas de consulta após a carga.")
            resultado = subprocess.run(
                [sys.executable, "-u", str(refresh_script)],
                cwd=str(PROJECT_ROOT),
                env=ambiente,
                capture_output=True,
                text=True,
                check=False,
            )
            for linha in resultado.stdout.splitlines():
                logging.info("[PARQUET-DB] %s", linha)
            for linha in resultado.stderr.splitlines():
                logging.warning("[PARQUET-DB] %s", linha)
            if resultado.returncode != 0:
                raise RuntimeError(
                    f"refresh_parquet_db.py terminou com código {resultado.returncode}"
                )
            logging.info("[PARQUET-DB][OK] Tabelas de consulta atualizadas.")

    checagem = verificar_armazenamento()
    janela = calcular_janela()
    checagem >> janela
    atualizar_views(validar_parquets(executar_extracao(janela)))


tce_extracao_mensal()
