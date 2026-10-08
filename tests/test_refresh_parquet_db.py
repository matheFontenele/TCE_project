import unittest
from unittest.mock import patch

from scripts import refresh_parquet_db


class CursorSimulado:
    def __init__(self):
        self.chamadas = []
        self.description = None
        self._resultado = None
        self._descricoes_lote = iter([[('id',), ('nova',)], [('id',)]])

    def execute(self, consulta, parametros=None):
        self.chamadas.append((consulta, parametros))
        numero = len(self.chamadas)
        if numero == 3:  # SELECT * FROM staging LIMIT 0
            self.description = [('id',), ('nova',)]
        elif numero in (4, 6):  # SELECT * FROM read_parquet(...) LIMIT 0
            self.description = next(self._descricoes_lote)
        elif numero == 8:  # contagem final
            self._resultado = [(3,)]

    def fetchall(self):
        return self._resultado

    def fetchone(self):
        return self._resultado[0]


class RefreshParquetDbTest(unittest.TestCase):
    def test_construir_insere_por_lotes_e_alinha_coluna_ausente(self):
        cursor = CursorSimulado()
        with patch.object(refresh_parquet_db, 'TAMANHO_LOTE', 2):
            linhas = refresh_parquet_db.construir(
                cursor, 'notas_empenho', ['a.parquet', 'b.parquet', 'c.parquet']
            )

        self.assertEqual(linhas, 3)
        # DROP, CTAS vazio com schema global, descrição de destino, dois pares
        # SELECT LIMIT 0/INSERT e a contagem final.
        self.assertEqual(len(cursor.chamadas), 8)
        consultas_schema_lote = [cursor.chamadas[i][0] for i in (3, 5)]
        self.assertTrue(all('LIMIT 0' in repr(c) for c in consultas_schema_lote))
        self.assertTrue(all('DESCRIBE' not in repr(c) for c in consultas_schema_lote))
        inserts = [c for c, _ in cursor.chamadas if 'INSERT INTO' in repr(c)]
        self.assertEqual(len(inserts), 2)
        # A expressão de INSERT é SQL composta. Sua composição inclui NULL para
        # a coluna global ausente no segundo lote.
        self.assertTrue(any('NULL' in repr(c) for c in inserts))

    def test_tamanho_lote_nao_positivo_falha_antes_de_apagar_staging(self):
        cursor = CursorSimulado()
        with patch.object(refresh_parquet_db, 'TAMANHO_LOTE', 0):
            with self.assertRaises(ValueError):
                refresh_parquet_db.construir(cursor, 'notas_empenho', ['a.parquet'])
        self.assertEqual(cursor.chamadas, [])


if __name__ == '__main__':
    unittest.main()
