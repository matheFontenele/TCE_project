"""Página do SQL Explorer: editor, catálogo de tabelas e resultados.

Toda a execução passa por ``src.modules.execucao_sql``; esta camada é só
interface (Streamlit). O catálogo lê ``information_schema`` e usa os rótulos
amigáveis de ``endpoints.json`` (``colunas_texto``) quando existirem.
"""
from __future__ import annotations

import streamlit as st

from src.modules.data_utils import carregar_configuracoes
from src.modules.execucao_sql import (
    backend_atual,
    executar_sql,
    listar_catalogo,
    teto_linhas,
)

EXEMPLOS = {
    "Contar liquidações": "SELECT count(*) AS total FROM liquidacoes",
    "Total por município": (
        "SELECT municipio_referencia, count(*) AS n, sum(valor_liquidado) AS total\n"
        "FROM liquidacoes\n"
        "GROUP BY 1\n"
        "ORDER BY total DESC"
    ),
    "Empenhos de um município": (
        "SELECT numero_empenho, nome_negociante, valor_empenhado\n"
        "FROM notas_empenho\n"
        "WHERE municipio_referencia = 'FORTALEZA'\n"
        "ORDER BY valor_empenhado DESC"
    ),
}


def _rotulos_amigaveis() -> dict[str, str]:
    """Mapa coluna -> rótulo, a partir de ``colunas_texto`` do endpoints.json."""
    rotulos: dict[str, str] = {}
    for config in carregar_configuracoes().values():
        for coluna, rotulo in (config.get("colunas_texto") or {}).items():
            rotulos.setdefault(coluna, rotulo)
    return rotulos


def _renderizar_catalogo() -> None:
    with st.expander("📚 Catálogo de tabelas e colunas", expanded=False):
        resultado = listar_catalogo()
        if resultado.erro:
            st.warning(f"Não foi possível carregar o catálogo: {resultado.erro}")
            return
        if resultado.vazio:
            st.info("Nenhuma tabela publicada ainda. Rode o refresh das tabelas de consulta.")
            return

        rotulos = _rotulos_amigaveis()
        tabelas: dict[str, list[tuple[str, str]]] = {}
        for nome_tabela, coluna, tipo in resultado.linhas:
            tabelas.setdefault(nome_tabela, []).append((coluna, tipo))

        st.caption(f"{len(tabelas)} tabelas disponíveis.")
        for nome_tabela in sorted(tabelas):
            with st.expander(f"🗂️ {nome_tabela} ({len(tabelas[nome_tabela])} colunas)"):
                linhas = [
                    {"Coluna": coluna, "Rótulo": rotulos.get(coluna, ""), "Tipo": tipo}
                    for coluna, tipo in tabelas[nome_tabela]
                ]
                st.dataframe(linhas, use_container_width=True, hide_index=True)


def _renderizar_resultado(resultado) -> None:
    if resultado.erro:
        st.error(f"❌ {resultado.erro}")
        return

    if resultado.vazio:
        st.info(f"Consulta executada em {resultado.tempo_s:.2f}s — nenhuma linha retornada.")
        return

    if resultado.truncado:
        st.warning(
            f"Resultado limitado a {resultado.linhas_exibidas:,} linhas. "
            "Refine a consulta (WHERE, GROUP BY ou LIMIT menor) para ver o restante."
        )

    st.caption(
        f"✅ {resultado.linhas_exibidas:,} linhas em {resultado.tempo_s:.2f}s "
        f"(backend: {backend_atual()})"
    )
    df = resultado.como_dataframe()
    st.dataframe(df, use_container_width=True, hide_index=True)

    csv = df.to_csv(index=False, sep=";", encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        "📥 Exportar resultado (CSV)",
        data=csv,
        file_name="consulta_tce.csv",
        mime="text/csv",
        use_container_width=True,
    )


def render_sql_explorer_page() -> None:
    st.header("🧮 SQL Explorer")
    st.caption(
        "Consulte as tabelas publicadas a partir dos parquets. Somente leitura: "
        "apenas SELECT é permitido."
    )

    if "sql_texto" not in st.session_state:
        st.session_state.sql_texto = EXEMPLOS["Contar liquidações"]

    exemplo = st.selectbox("Consultas de exemplo", ["— escolha —", *EXEMPLOS])
    if exemplo != "— escolha —":
        st.session_state.sql_texto = EXEMPLOS[exemplo]

    sql = st.text_area(
        "Consulta SQL",
        key="sql_texto",
        height=200,
        placeholder="SELECT * FROM liquidacoes LIMIT 100",
    )

    col_limite, col_backend = st.columns([2, 1])
    limite = col_limite.number_input(
        "Limite de linhas",
        min_value=1,
        max_value=teto_linhas(),
        value=min(100, teto_linhas()),
        step=50,
    )
    col_backend.caption(f"Backend: **{backend_atual()}**")

    executar = st.button("▶️ Executar", type="primary", use_container_width=True)

    if executar:
        if not sql.strip():
            st.warning("Digite uma consulta antes de executar.")
        else:
            with st.spinner("Executando consulta..."):
                resultado = executar_sql(sql, limite_linhas=int(limite))
            _renderizar_resultado(resultado)

    st.divider()
    _renderizar_catalogo()
