import os
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

from src.quality.armazenamento import (
    BYTES_POR_GB,
    espaco_livre_bytes,
    verificar_espaco_livre,
)

Uso = namedtuple("Uso", ["total", "used", "free"])


def _uso(total_gb, livre_gb):
    return Uso(total=int(total_gb * BYTES_POR_GB),
               used=int((total_gb - livre_gb) * BYTES_POR_GB),
               free=int(livre_gb * BYTES_POR_GB))


class ArmazenamentoTest(unittest.TestCase):
    def test_livre_acima_do_minimo_retorna_resumo(self):
        with patch("src.quality.armazenamento.shutil.disk_usage",
                   return_value=_uso(100, 50)):
            info = verificar_espaco_livre("/qualquer", minimo_gb=20)

        self.assertAlmostEqual(info["livre_gb"], 50.0)
        self.assertAlmostEqual(info["total_gb"], 100.0)
        self.assertEqual(info["minimo_gb"], 20.0)
        self.assertEqual(info["caminho"], "/qualquer")

    def test_livre_abaixo_do_minimo_levanta_runtime_error(self):
        with patch("src.quality.armazenamento.shutil.disk_usage",
                   return_value=_uso(100, 5)):
            with self.assertRaises(RuntimeError) as erro:
                verificar_espaco_livre("/qualquer", minimo_gb=20)
        self.assertIn("5.0 GB", str(erro.exception))

    def test_minimo_zero_desativa_o_guarda(self):
        with patch("src.quality.armazenamento.shutil.disk_usage",
                   return_value=_uso(100, 0.001)):
            info = verificar_espaco_livre("/qualquer", minimo_gb=0)
        self.assertEqual(info["minimo_gb"], 0.0)

    def test_minimo_vem_da_variavel_de_ambiente(self):
        with patch.dict(os.environ, {"AIRFLOW_MIN_FREE_SPACE_GB": "42"}):
            with patch("src.quality.armazenamento.shutil.disk_usage",
                       return_value=_uso(100, 50)):
                info = verificar_espaco_livre("/qualquer")
        self.assertEqual(info["minimo_gb"], 42.0)

    def test_caminho_inexistente_usa_ancestral_existente(self):
        with tempfile.TemporaryDirectory() as tmp:
            alvo = Path(tmp) / "data" / "ainda_nao_criada"
            # Não deve levantar FileNotFoundError; mede o volume do ancestral.
            with patch("src.quality.armazenamento.shutil.disk_usage",
                       return_value=_uso(100, 50)) as disk_usage:
                espaco_livre_bytes(alvo)
            caminho_medido = disk_usage.call_args.args[0]
            self.assertEqual(Path(caminho_medido), Path(tmp))


if __name__ == "__main__":
    unittest.main()
