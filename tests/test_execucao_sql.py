import os
import unittest
from unittest.mock import MagicMock, patch

from src.modules import execucao_sql


class CursorFalso:
    """Cursor mínimo: registra os comandos e devolve linhas combinadas."""

    def __init__(self, descricao, linhas):
        self.description = descricao
        self._linhas = list(linhas)
        self.chamadas = []

    def execute(self, consulta, parametros=None):
        self.chamadas.append((consulta, parametros))

    def fetchall(self):
        return self._linhas


class ConexaoFalsa:
    def __init__(self, cursor):
        self._cursor = cursor
        self.autocommit = False
        self.fechada = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.fechada = True


class EnvolverConsultaTest(unittest.TestCase):
    def test_envolve_em_subselect_com_limite_mais_um(self):
        envolvido = execucao_sql._envolver_consulta("SELECT 1", 10)
        self.assertIn("SELECT * FROM (", envolvido)
        self.assertIn("SELECT 1", envolvido)
        self.assertTrue(envolvido.rstrip().endswith("LIMIT 11"))

    def test_aceita_with_e_remove_ponto_e_virgula_final(self):
        envolvido = execucao_sql._envolver_consulta("WITH t AS (SELECT 1) SELECT * FROM t;", 5)
        self.assertNotIn(";", envolvido)
        self.assertTrue(envolvido.rstrip().endswith("LIMIT 6"))

    def test_consulta_vazia_falha(self):
        with self.assertRaises(ValueError):
            execucao_sql._envolver_consulta("   ;  ", 10)


class LimiteEfetivoTest(unittest.TestCase):
    def test_respeita_o_teto_do_ambiente(self):
        with patch.dict(os.environ, {"TCE_SQL_MAX_ROWS": "50"}):
            self.assertEqual(execucao_sql._limite_efetivo(10), 10)
            self.assertEqual(execucao_sql._limite_efetivo(999), 50)
            self.assertEqual(execucao_sql._limite_efetivo(None), 50)

    def test_limite_nao_positivo_vira_um(self):
        with patch.dict(os.environ, {"TCE_SQL_MAX_ROWS": "50"}):
            self.assertEqual(execucao_sql._limite_efetivo(0), 1)

    def test_valor_invalido_cai_no_padrao(self):
        with patch.dict(os.environ, {"TCE_SQL_MAX_ROWS": "abc"}):
            self.assertEqual(execucao_sql._limite_efetivo(None), execucao_sql.MAX_LINHAS_PADRAO)


class MontarResultadoTest(unittest.TestCase):
    def test_marca_truncamento_e_corta_a_linha_extra(self):
        cursor = CursorFalso([("id",), ("nome",)], [(1, "a"), (2, "b"), (3, "c")])
        resultado = execucao_sql._montar_resultado(cursor, limite=2, inicio=0.0)
        self.assertTrue(resultado.truncado)
        self.assertEqual(resultado.linhas, [(1, "a"), (2, "b")])
        self.assertEqual(resultado.colunas, ["id", "nome"])

    def test_sem_excedente_nao_marca_truncamento(self):
        cursor = CursorFalso([("id",)], [(1,)])
        resultado = execucao_sql._montar_resultado(cursor, limite=2, inicio=0.0)
        self.assertFalse(resultado.truncado)
        self.assertEqual(resultado.linhas, [(1,)])


class ExecutarSqlPostgresTest(unittest.TestCase):
    def _executar(self, sql="SELECT 1"):
        cursor = CursorFalso([("n",)], [(1,)])
        conexao = ConexaoFalsa(cursor)
        with patch.dict(os.environ, {
            "PARQUET_DB_READ_USER": "leitor",
            "PARQUET_DB_READ_PASSWORD": "senha",
        }):
            with patch.object(execucao_sql, "_conexao_postgres", return_value=conexao):
                resultado = execucao_sql.executar_sql(sql, limite_linhas=5, backend="postgres")
        return resultado, cursor, conexao

    def test_reforca_somente_leitura_antes_da_consulta(self):
        _, cursor, _ = self._executar()
        comandos = [consulta for consulta, _ in cursor.chamadas]
        self.assertIn("SET default_transaction_read_only = on", comandos)
        self.assertTrue(any(consulta.startswith("SET statement_timeout") for consulta in comandos))

    def test_sql_do_usuario_vai_sem_interpolacao(self):
        # Um '%' do usuário não pode ser tratado como marcador pelo psycopg2.
        _, cursor, _ = self._executar("SELECT 1 WHERE 'x' LIKE '%a%'")
        consulta_final, parametros = cursor.chamadas[-1]
        self.assertIsNone(parametros)
        self.assertIn("LIKE '%a%'", consulta_final)
        self.assertIn("LIMIT 6", consulta_final)

    def test_fecha_a_conexao_mesmo_com_erro(self):
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError("boom na consulta")
        conexao = ConexaoFalsa(cursor)
        with patch.object(execucao_sql, "_conexao_postgres", return_value=conexao):
            resultado = execucao_sql.executar_sql("SELECT 1", backend="postgres")
        self.assertEqual(resultado.erro, "boom na consulta")
        self.assertTrue(conexao.fechada)

    def test_erro_do_banco_volta_no_campo_erro(self):
        with patch.object(execucao_sql, "_conexao_postgres", side_effect=RuntimeError("sem conexão")):
            resultado = execucao_sql.executar_sql("SELECT 1", backend="postgres")
        self.assertEqual(resultado.erro, "sem conexão")
        self.assertTrue(resultado.vazio)
        self.assertEqual(resultado.colunas, [])


class BackendTest(unittest.TestCase):
    def test_backend_desconhecido_vira_erro_amigavel(self):
        resultado = execucao_sql.executar_sql("SELECT 1", backend="trino")
        self.assertIn("Backend desconhecido", resultado.erro)

    def test_backend_duckdb_e_acionado_quando_pedido(self):
        with patch.object(execucao_sql, "_executar_duckdb", return_value=execucao_sql.ResultadoSQL()) as mock:
            execucao_sql.executar_sql("SELECT 1", backend="duckdb")
        mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
