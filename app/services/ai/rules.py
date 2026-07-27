"""
Regras determinísticas para peso térmico, serve-chuva e estações.

Estes campos NÃO usam IA: são derivados da categoria já identificada, por uma
tabela fixa. A filosofia é conservadora — só atribuímos um valor quando ele é
conclusivo pela natureza da categoria. Quando depende do material (um blazer
pode ser de linho leve ou de lã pesada; um casaco pode ser corta-vento
impermeável ou lã), o campo fica NULO e o usuário preenche à mão.

Estações são derivadas do peso térmico quando ele é conhecido; se o peso ficar
nulo, estações também fica nulo.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import ClothingCategory, Estacao, PesoTermico


@dataclass
class RuleResult:
    """Resultado das regras, com o motivo quando um campo fica nulo."""

    peso_termico: str | None = None
    serve_chuva: bool | None = None
    estacoes: list[str] | None = None
    peso_termico_reason: str | None = None
    serve_chuva_reason: str | None = None
    estacoes_reason: str | None = None


# Peso térmico conclusivo por categoria. Ausência da chave = material-dependente
# (fica nulo). Cachecol e casaco são peças de aquecimento/proteção por natureza.
_PESO_POR_CATEGORIA: dict[str, str] = {
    ClothingCategory.CACHECOL.value: PesoTermico.PESADO.value,
    ClothingCategory.CASACO.value: PesoTermico.PESADO.value,
}

# Serve-chuva conclusivo por categoria. Praticamente sempre depende do material
# (mesmo um casaco pode ou não ser impermeável), então este mapa fica vazio de
# propósito: todas as categorias caem em "depende do material" → nulo. Mantido
# como tabela para fácil extensão futura (ex.: uma categoria "bota impermeável").
_SERVE_CHUVA_POR_CATEGORIA: dict[str, bool] = {}

# Peso térmico → estações sugeridas.
_ESTACOES_POR_PESO: dict[str, list[str]] = {
    PesoTermico.LEVE.value: [Estacao.VERAO.value, Estacao.MEIA_ESTACAO.value],
    PesoTermico.MEDIO.value: [Estacao.MEIA_ESTACAO.value],
    PesoTermico.PESADO.value: [Estacao.INVERNO.value, Estacao.MEIA_ESTACAO.value],
}


def apply_rules(category: str | None) -> RuleResult:
    """
    Aplica as regras determinísticas a partir da categoria (pode ser None).

    Args:
        category: valor de `ClothingCategory` já identificado, ou None quando a
            categoria não pôde ser determinada com confiança.
    """
    result = RuleResult()

    if category is None:
        result.peso_termico_reason = "categoria_indeterminada"
        result.serve_chuva_reason = "categoria_indeterminada"
        result.estacoes_reason = "categoria_indeterminada"
        return result

    # ── Peso térmico ─────────────────────────────────────────────────────
    peso = _PESO_POR_CATEGORIA.get(category)
    if peso is not None:
        result.peso_termico = peso
    else:
        result.peso_termico_reason = "peso_depende_do_material"

    # ── Serve chuva ──────────────────────────────────────────────────────
    serve = _SERVE_CHUVA_POR_CATEGORIA.get(category)
    if serve is not None:
        result.serve_chuva = serve
    else:
        result.serve_chuva_reason = "serve_chuva_depende_do_material"

    # ── Estações (derivadas do peso, quando conhecido) ───────────────────
    if result.peso_termico is not None:
        result.estacoes = list(_ESTACOES_POR_PESO.get(result.peso_termico, []))
        if not result.estacoes:
            result.estacoes = None
            result.estacoes_reason = "peso_sem_mapa_de_estacoes"
    else:
        result.estacoes_reason = "peso_termico_indeterminado"

    return result
