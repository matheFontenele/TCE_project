import tempfile
import unittest
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from src.quality.parquet_validator import validar_pasta_parquets


class ParquetValidatorTest(unittest.TestCase):
    def test_validates_core_contract_and_file_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notas_fiscais_2020_01_015.parquet"
            pq.write_table(pa.table({
                "codigo_municipio": ["015", "015"],
                "exercicio_orcamento": [202000, 202000],
                "numero_empenho": ["0001", "0002"],
                "numero_nota_fiscal": ["10", "11"],
            }), path)

            resultado = validar_pasta_parquets(tmp)

            self.assertTrue(resultado["sucesso_estrutural"])
            self.assertEqual(resultado["arquivos_encontrados"], 1)
            self.assertEqual(resultado["arquivos_validos"], 1)
            self.assertEqual(resultado["linhas_verificadas"], 2)
            self.assertEqual(resultado["total_issues"], 0)

    def test_reports_missing_columns_and_key_mismatches(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notas_fiscais_2020_01_015.parquet"
            pq.write_table(pa.table({
                "codigo_municipio": ["016", "015"],
                "exercicio_orcamento": [202100, 202000],
                "numero_empenho": ["", "0002"],
            }), path)

            resultado = validar_pasta_parquets(tmp)
            codigos = {issue["codigo"] for issue in resultado["issues"]}

            self.assertFalse(resultado["sucesso_estrutural"])
            self.assertIn("COLUNAS_OBRIGATORIAS_AUSENTES", codigos)
            self.assertIn("MUNICIPIO_DIVERGENTE", codigos)
            self.assertIn("EXERCICIO_DIVERGENTE", codigos)
            self.assertIn("CHAVE_VAZIA", codigos)

    def test_empty_marker_is_valid_no_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notas_pagamentos_2008_01_015.parquet"
            pq.write_table(pa.table({}), path)

            resultado = validar_pasta_parquets(tmp)

            self.assertTrue(resultado["sucesso_estrutural"])
            self.assertEqual(resultado["marcadores_sem_dados"], 1)
            self.assertEqual(resultado["total_issues"], 0)

    def test_reports_corrupt_parquet(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notas_empenho_2020_01_015.parquet"
            path.write_bytes(b"not a parquet")

            resultado = validar_pasta_parquets(tmp)

            self.assertFalse(resultado["sucesso_estrutural"])
            self.assertEqual(resultado["issues"][0]["codigo"], "PARQUET_ILEGIVEL")

    def test_reports_incompatible_key_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notas_empenho_2020_01_015.parquet"
            pq.write_table(pa.table({
                "codigo_municipio": [15],
                "exercicio_orcamento": [202000],
                "numero_empenho": ["0001"],
            }), path)

            resultado = validar_pasta_parquets(tmp)

            self.assertFalse(resultado["sucesso_estrutural"])
            self.assertIn(
                "TIPO_INCOMPATIVEL",
                {issue["codigo"] for issue in resultado["issues"]},
            )


if __name__ == "__main__":
    unittest.main()
