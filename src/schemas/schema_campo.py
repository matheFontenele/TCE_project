from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Campo:
    nome: str
    tipo: str
    posicao_ini: Optional[int] = None
    tamanho: Optional[int] = None
    indice: Optional[int] = None
    valor_fixo: Optional[str] = None
    valores_permitidos: Optional[list] = None
    formato_data: Optional[str] = None
    obrigatorio: bool = True

    def __post_init__(self):
        eh_posicional = self.posicao_ini is not None and self.tamanho is not None
        eh_delimitado = self.indice is not None
        if eh_posicional == eh_delimitado:
            raise ValueError(
                f"Campo '{self.nome}': defina (posicao_ini + tamanho) para posicional "
                f"OU indice para delimitado — nunca os dois nem nenhum."
            )