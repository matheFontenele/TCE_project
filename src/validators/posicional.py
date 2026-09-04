"""
Motor genérico de validação de arquivos posicionais (largura fixa) do TCE-CE.

Todos os endpoints do TCE-CE (veículos, RPPS, investimentos, etc.) seguem o
mesmo padrão: cada linha do arquivo é composta por campos de tamanho fixo,
posicionados em colunas específicas. Este módulo define uma estrutura de
schema reutilizável e um validador que roda em cima de qualquer schema
descrito nesse formato — sem precisar escrever um validador novo para cada
tipo de documento.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional


@dataclass
class CampoSchema:
    """Descreve um único campo dentro do layout posicional de um documento do TCE."""

    nome: str
    posicao_ini: int  # 1-indexed, conforme a numeração usada no manual do TCE
    tamanho: int
    tipo: str  # "char" | "numerico" | "data"
    valor_fixo: Optional[str] = None
    valores_permitidos: Optional[list] = None
    formato_data: Optional[str] = None  # ex: "%Y%m%d" ou "%Y%m"
    obrigatorio: bool = True
    ref_tabela: Optional[str] = None  # nome de uma tabela de referência (município, órgão, etc.)

    @property
    def posicao_fim(self) -> int:
        return self.posicao_ini + self.tamanho - 1


@dataclass
class RegistroValidado:
    """Resultado da validação de uma linha (um registro) do arquivo."""

    linha: int
    conteudo_bruto: str
    campos: dict = field(default_factory=dict)
    erros: list = field(default_factory=list)

    @property
    def valido(self) -> bool:
        return len(self.erros) == 0


def extrair_campo(linha: str, campo: CampoSchema) -> str:
    """Extrai o trecho da linha correspondente à posição do campo no schema."""
    ini = campo.posicao_ini - 1
    fim = ini + campo.tamanho
    return linha[ini:fim]


def validar_campo(valor: str, campo: CampoSchema) -> list[str]:
    """Aplica todas as regras de um CampoSchema sobre o valor bruto extraído."""
    erros: list[str] = []
    valor_stripped = valor.strip()

    # 1. Tamanho exato
    if len(valor) != campo.tamanho:
        erros.append(
            f"Campo '{campo.nome}': tamanho incorreto "
            f"(esperado {campo.tamanho}, encontrado {len(valor)})."
        )

    # 2. Obrigatoriedade
    if campo.obrigatorio and not valor_stripped:
        erros.append(f"Campo '{campo.nome}': valor obrigatório está vazio.")
        return erros  # sem valor, as regras abaixo não fazem sentido

    # 3. Valor fixo (ex: tipo de documento sempre "993")
    if campo.valor_fixo is not None and valor_stripped != campo.valor_fixo:
        erros.append(
            f"Campo '{campo.nome}': esperado valor fixo '{campo.valor_fixo}', "
            f"encontrado '{valor_stripped}'."
        )

    # 4. Tipo numérico
    if campo.tipo == "numerico" and not re.fullmatch(r"\d+", valor_stripped or ""):
        erros.append(
            f"Campo '{campo.nome}': deveria ser numérico, encontrado '{valor_stripped}'."
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

    # 6. Lista de valores permitidos (ex: motivo_baixa só pode ser 1-6)
    if campo.valores_permitidos is not None:
        try:
            valor_comparavel = int(valor_stripped) if campo.tipo == "numerico" else valor_stripped
        except ValueError:
            valor_comparavel = valor_stripped
        if valor_comparavel not in campo.valores_permitidos:
            erros.append(
                f"Campo '{campo.nome}': valor '{valor_stripped}' fora da lista "
                f"permitida {campo.valores_permitidos}."
            )

    return erros


def validar_linha(
    linha: str,
    numero_linha: int,
    schema: list[CampoSchema],
    regras_customizadas: Optional[list[Callable[[dict], list[str]]]] = None,
) -> RegistroValidado:
    """Valida uma linha inteira do arquivo contra o schema e as regras de negócio."""
    registro = RegistroValidado(linha=numero_linha, conteudo_bruto=linha)

    for campo in schema:
        valor_bruto = extrair_campo(linha, campo)
        registro.campos[campo.nome] = valor_bruto.strip()
        registro.erros.extend(validar_campo(valor_bruto, campo))

    # Regras que dependem de mais de um campo (ex: data_baixa >= data_inclusao)
    if regras_customizadas:
        for regra in regras_customizadas:
            registro.erros.extend(regra(registro.campos))

    return registro


def validar_arquivo(
    conteudo: str,
    schema: list[CampoSchema],
    regras_customizadas: Optional[list[Callable[[dict], list[str]]]] = None,
) -> list[RegistroValidado]:
    """Valida o conteúdo completo de um arquivo posicional, linha a linha."""
    linhas = [l for l in conteudo.splitlines() if l.strip()]
    return [
        validar_linha(linha, i + 1, schema, regras_customizadas)
        for i, linha in enumerate(linhas)
    ]