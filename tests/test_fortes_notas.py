import importlib.util
import sys
import types
import unittest

import pandas as pd


if importlib.util.find_spec('duckdb') is None:
    sys.modules['duckdb'] = types.ModuleType('duckdb')

from src.modules.rules.fortes_notas import parear_despesas_extra


class PareamentoDespesasExtraTest(unittest.TestCase):
    def test_pareia_ocorrencias_reais_sem_repetir_a_maior_data(self):
        notas = pd.DataFrame([
            {
                '_debug_mun': '061',
                'codigo_orgao': '06',
                'codigo_unidade_orcamentaria': '01',
                '_debug_fornecedor': 'DR SOFTWARE SERVICOS',
                '_debug_valor': '1720.00',
                '_debug_nota': '17991',
                'numero_empenho': '01100163',
                'data_emissao': '2025-11-02',
            },
            {
                '_debug_mun': '061',
                'codigo_orgao': '06',
                'codigo_unidade_orcamentaria': '01',
                '_debug_fornecedor': 'DR SOFTWARE SERVICOS',
                '_debug_valor': '1720.00',
                '_debug_nota': '18438',
                'numero_empenho': '15100013',
                'data_emissao': '2025-11-13',
            },
            {
                '_debug_mun': '061',
                'codigo_orgao': '06',
                'codigo_unidade_orcamentaria': '01',
                '_debug_fornecedor': 'DR SOFTWARE SERVICOS',
                '_debug_valor': '1720.00',
                '_debug_nota': '18675',
                'numero_empenho': '03110216',
                'data_emissao': '2025-12-04',
            },
            {
                '_debug_mun': '061',
                'codigo_orgao': '06',
                'codigo_unidade_orcamentaria': '01',
                '_debug_fornecedor': 'DR SOFTWARE SERVICOS',
                '_debug_valor': '1720.00',
                '_debug_nota': '18787',
                'numero_empenho': '01120041',
                'data_emissao': '2025-12-12',
            },
        ])
        despesas = pd.DataFrame([
            {
                'codigo_municipio': '061',
                'codigo_orgao': '06',
                'codigo_unidade_orcamentaria': '01',
                'nome_normalizado': 'DR SOFTWARE SERVICOS',
                'data_emissao_despesa_extra': '2026-01-16',
                'numero_documento_despesa_extra': '16010017',
                'valor_rounded_liquido': '1720.00',
                'valor_rounded_bruto': '1720.00',
            },
            {
                'codigo_municipio': '061',
                'codigo_orgao': '06',
                'codigo_unidade_orcamentaria': '01',
                'nome_normalizado': 'DR SOFTWARE SERVICOS',
                'data_emissao_despesa_extra': '2026-01-19',
                'numero_documento_despesa_extra': '19010027',
                'valor_rounded_liquido': '1720.00',
                'valor_rounded_bruto': '1720.00',
            },
        ])

        resultado = parear_despesas_extra(notas, despesas, 'data_emissao')
        datas_por_nota = resultado.set_index('_debug_nota')['_debug_extra_data'].to_dict()
        docs_por_nota = resultado.set_index('_debug_nota')['_debug_extra_documento'].to_dict()

        self.assertEqual(len(resultado), len(notas))
        self.assertEqual(datas_por_nota['18675'], '2026-01-16')
        self.assertEqual(datas_por_nota['18787'], '2026-01-19')
        self.assertEqual(docs_por_nota['18675'], '16010017')
        self.assertEqual(docs_por_nota['18787'], '19010027')
        self.assertEqual(datas_por_nota['17991'], '')
        self.assertEqual(datas_por_nota['18438'], '')


if __name__ == '__main__':
    unittest.main()
