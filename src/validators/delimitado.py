from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional


@dataclass
class CampoSchemaDelimitado:
    """Descreve um único campo dentro de um arquivo delimitado (CSV) do TCE."""

    nome: str
    indice: int  # 0-indexed, posição da coluna no CSV
    tipo: str  # "char" | "numerico" | "decimal" | "data"
    valor_fixo: Optional[str] = None
    valores_permitidos: Optional[list] = None
    formato_data: Optional[str] = None  # ex: "%Y%m%d" ou "%Y%m"
    obrigatorio: bool = True


@dataclass
class RegistroValidadoDelimitado:
    """Resultado da validação de uma linha (um registro) do arquivo."""

    linha: int
    campos: dict = field(default_factory=dict)
    erros: list = field(default_factory=list)

    @property
    def valido(self) -> bool:
        return len(self.erros) == 0


def validar_campo(valor: str, campo: CampoSchemaDelimitado) -> list[str]:
    """Aplica todas as regras de um CampoSchemaDelimitado sobre o valor extraído."""
    erros: list[str] = []
    valor_stripped = (valor or "").strip()

    # 1. Obrigatoriedade
    if campo.obrigatorio and not valor_stripped:
        erros.append(f"Campo '{campo.nome}': valor obrigatório está vazio.")
        return erros

    # 2. Valor fixo (ex: tipo de documento sempre "994")
    if campo.valor_fixo is not None and valor_stripped != campo.valor_fixo:
        erros.append(
            f"Campo '{campo.nome}': esperado valor fixo '{campo.valor_fixo}', "
            f"encontrado '{valor_stripped}'."
        )

    # 3. Tipo numérico (inteiro)
    if campo.tipo == "numerico" and not re.fullmatch(r"\d+", valor_stripped or ""):
        erros.append(
            f"Campo '{campo.nome}': deveria ser numérico, encontrado '{valor_stripped}'."
        )

    # 4. Tipo decimal (ex: 179.00, 06.33)
    if campo.tipo == "decimal":
        try:
            float(valor_stripped)
        except ValueError:
            erros.append(
                f"Campo '{campo.nome}': deveria ser um número decimal, "
                f"encontrado '{valor_stripped}'."
            )

    # 5. Tipo data
    if campo.tipo == "data" and campo.formato_data:
        try:
            datetime.strptime(valor_stripped, campo.formato_data)
        except ValueError:
            erros.append(
                f"Campo '{campo.nome}': data inválida '{valor_stripped}' "
                f"(formato esperado {campo.formato_data})."
            )

    # 6. Lista de valores permitidos
    if campo.valores_permitidos is not None and valor_stripped not in campo.valores_permitidos:
        erros.append(
            f"Campo '{campo.nome}': valor '{valor_stripped}' fora da lista "
            f"permitida {campo.valores_permitidos}."
        )

    return erros


def validar_linha(
    row: list[str],
    numero_linha: int,
    schema: list[CampoSchemaDelimitado],
    regras_customizadas: Optional[list[Callable[[dict], list[str]]]] = None,
) -> RegistroValidadoDelimitado:
    registro = RegistroValidadoDelimitado(linha=numero_linha)

    for campo in schema:
        valor_bruto = row[campo.indice] if campo.indice < len(row) else ""
        registro.campos[campo.nome] = valor_bruto.strip()
        registro.erros.extend(validar_campo(valor_bruto, campo))

    if len(row) != len(schema):
        registro.erros.append(
            f"Número de colunas incorreto: esperado {len(schema)}, encontrado {len(row)}."
        )

    if regras_customizadas:
        for regra in regras_customizadas:
            registro.erros.extend(regra(registro.campos))

    return registro


def validar_arquivo(
    conteudo: str,
    schema: list[CampoSchemaDelimitado],
    regras_customizadas: Optional[list[Callable[[dict], list[str]]]] = None,
    delimitador: str = ",",
) -> list[RegistroValidadoDelimitado]:
    leitor = csv.reader(io.StringIO(conteudo), delimiter=delimitador, quotechar='"')
    registros = []
    for i, row in enumerate(leitor):
        if not row or not any(campo.strip() for campo in row):
            continue  # ignora linhas vazias
        registros.append(validar_linha(row, i + 1, schema, regras_customizadas))
    return registros