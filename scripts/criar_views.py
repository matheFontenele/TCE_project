import re
from collections import defaultdict
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
DB = ROOT / 'tce.duckdb'

if not DATA.is_dir():
    raise FileNotFoundError(f'Pasta de parquets não encontrada: {DATA}')

grupos = defaultdict(list)
for f in sorted(DATA.rglob('*.parquet')):
    if len(pq.read_schema(f).names) == 0:
        continue  # marcador vazio, ignora
    tabela = re.sub(r'_\d{4}(_\d{2})?_\d{3}$', '', f.stem)
    grupos[tabela].append(str(f))

con = duckdb.connect(str(DB))
try:
    for tabela, arquivos in sorted(grupos.items()):
        lista = ", ".join(
            "'" + arquivo.replace("'", "''") + "'"
            for arquivo in arquivos
        )
        identificador = tabela.replace('"', '""')
        con.execute(f'''
            CREATE OR REPLACE VIEW "{identificador}" AS
            SELECT * FROM read_parquet([{lista}], union_by_name = true, filename = true)
        ''')
        print(tabela, len(arquivos), 'arquivos')
finally:
    con.close()
