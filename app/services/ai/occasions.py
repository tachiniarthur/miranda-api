"""
Perfis de ocasião — o que "reunião" ou "shopping" significam para a composição.

Este módulo é a tradução de um evento humano ("jantar romântico") em parâmetros
que o motor determinístico de `look_generation.py` sabe aplicar. Sem LLM: cada
ocasião é uma linha de uma tabela auditável e ajustável.

── Por que um PERFIL e não só um alvo de formalidade ────────────────────────
Se ocasião fosse apenas um alvo na escala `esporte(0) · casual(1) ·
smart_casual(2) · social(3)`, metade das opções colapsaria: shopping, dia a dia
e faculdade seriam todas `casual` e produziriam exatamente o mesmo look. A
granularidade seria cosmética — o usuário escolheria coisas diferentes e
receberia a mesma resposta.

Por isso cada ocasião tem CINCO dimensões independentes, e cada par de ocasiões
difere em ao menos uma delas:

  1. `formality_target` / `formality_tolerance` — onde o look deve cair na
     escala e quanto de desvio ainda conta como "no registro".
  2. `comfort_bias` — o quanto a ocasião envolve ficar de pé/andar. Penaliza
     peça pesada quando o clima não exige (shopping e viagem, não reunião).
  3. `layering_bias` — o quanto a ocasião pede sobreposição INDEPENDENTE do
     clima (blazer de reunião, casaco de avião, camada de sala com ar).
  4. `color_discipline` — "neutro" (nenhuma cor forte: entrevista, reunião),
     "livre" (no máximo uma, o padrão) ou "destaque" (no máximo uma, e ter uma
     é premiado: jantar romântico, evento formal).
  5. `category_bonus` / `forbidden_categories` — o que a ocasião favorece
     (vestido num jantar, blazer numa entrevista) e o que ela simplesmente não
     admite (blazer na academia).

── Proibição vs. registro ───────────────────────────────────────────────────
As duas restrições têm forças diferentes de propósito:

  · `forbidden_categories` é INVIOLÁVEL. Nunca é relaxada, nem em guarda-roupa
    pobre — é preferível não montar um look de academia a sugerir um blazer.
    A rede de segurança em `_structure_is_valid` reconfere isso na saída da API.
  · `formality_target` é uma PREFERÊNCIA FORTE. Se o guarda-roupa não tem o
    registro pedido, a composição relaxa e devolve o melhor possível junto de
    uma nota explicando o que faltou — a mesma degradação graciosa que o
    pré-filtro térmico já pratica.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.models.enums import Ocasiao

# ── Escala ordinal de formalidade ────────────────────────────────────────────
# Compartilhada com look_generation (importada de lá para não duplicar a fonte
# da verdade). Repetida aqui só como referência de leitura:
#   esporte(0) · casual(1) · smart_casual(2) · social(3)
FORMALITY_SCALE_SPAN = 3.0  # distância máxima possível entre dois registros

# Disciplinas de cor aceitas em `OccasionProfile.color_discipline`.
COLOR_NEUTRAL = "neutro"      # nenhuma família de cor forte
COLOR_FREE = "livre"          # no máximo uma (padrão do sistema)
COLOR_STATEMENT = "destaque"  # no máximo uma, e ter uma é premiado


@dataclass(frozen=True)
class OccasionProfile:
    """Tradução de uma ocasião em parâmetros de composição."""

    key: str
    label: str

    # Onde o look deve cair na escala de formalidade. Fracionário quando a
    # ocasião fica legitimamente ENTRE dois registros (jantar romântico vive
    # entre smart casual e social).
    formality_target: float
    # Desvio ainda considerado "no registro". Abaixo disso a peça é preferida;
    # acima, ela é filtrada (e só volta na relaxação, com nota).
    formality_tolerance: float

    # 0..1 — quanto a ocasião envolve ficar de pé, andar, carregar coisas.
    comfort_bias: float
    # 0..1 — quanto pede sobreposição mesmo com clima ameno.
    layering_bias: float

    color_discipline: str

    # Trecho usado nos templates de justificativa ("Para {phrase}, ...").
    phrase: str

    category_bonus: dict[str, float] = field(default_factory=dict)
    forbidden_categories: frozenset[str] = frozenset()
    # Ocasiões em que um acessório extra atrapalha (mãos ocupadas, bagagem).
    wants_accessory: bool = True


# ─────────────────────────────────────────────────────────────────────────────
# A tabela. Ajuste estes números com base no uso real — é o ponto de calibração
# da tela de look do dia.
# ─────────────────────────────────────────────────────────────────────────────
OCCASION_PROFILES: dict[str, OccasionProfile] = {
    Ocasiao.TRABALHO.value: OccasionProfile(
        key=Ocasiao.TRABALHO.value,
        label="Trabalho",
        formality_target=2.0,          # smart casual: escritório de expediente
        formality_tolerance=1.0,
        comfort_bias=0.2,              # sentada a maior parte do dia
        layering_bias=0.4,             # blazer quando o clima ajuda, não sempre
        color_discipline=COLOR_FREE,
        phrase="o trabalho",
        category_bonus={"blazer": 0.6},
    ),
    Ocasiao.REUNIAO.value: OccasionProfile(
        key=Ocasiao.REUNIAO.value,
        label="Reunião",
        formality_target=2.7,          # entre smart casual e social, puxando p/ social
        formality_tolerance=1.0,
        comfort_bias=0.0,
        layering_bias=0.8,             # sobreposição quase obrigatória
        color_discipline=COLOR_NEUTRAL,  # nada compete com o que se vai dizer
        phrase="uma reunião",
        category_bonus={"blazer": 1.0, "camisa": 0.3},
    ),
    Ocasiao.ENTREVISTA.value: OccasionProfile(
        key=Ocasiao.ENTREVISTA.value,
        label="Entrevista",
        formality_target=3.0,          # social pleno
        formality_tolerance=0.8,       # a mais estrita da tabela
        comfort_bias=0.0,
        layering_bias=0.9,
        color_discipline=COLOR_NEUTRAL,
        phrase="uma entrevista",
        category_bonus={"blazer": 1.0, "camisa": 0.4},
        wants_accessory=False,         # nada de ruído visual
    ),
    Ocasiao.DIA_A_DIA.value: OccasionProfile(
        key=Ocasiao.DIA_A_DIA.value,
        label="Dia a dia",
        formality_target=1.0,          # casual
        formality_tolerance=1.2,       # tolerante: é o registro mais elástico
        comfort_bias=0.5,
        layering_bias=0.2,
        color_discipline=COLOR_FREE,
        phrase="o dia a dia",
    ),
    Ocasiao.SHOPPING.value: OccasionProfile(
        key=Ocasiao.SHOPPING.value,
        label="Shopping",
        formality_target=1.0,          # mesmo alvo do dia a dia...
        formality_tolerance=1.0,
        comfort_bias=0.9,              # ...mas horas em pé: leveza pesa muito
        layering_bias=0.2,             # ambiente climatizado, camada atrapalha
        color_discipline=COLOR_FREE,
        phrase="uma tarde de shopping",
        wants_accessory=False,         # mãos ocupadas com sacolas
    ),
    Ocasiao.FACULDADE.value: OccasionProfile(
        key=Ocasiao.FACULDADE.value,
        label="Faculdade",
        formality_target=1.2,
        formality_tolerance=1.2,
        comfort_bias=0.7,              # dia longo, muita caminhada entre salas
        layering_bias=0.6,             # sala gelada, rua quente: camada resolve
        color_discipline=COLOR_FREE,
        phrase="um dia de faculdade",
        category_bonus={"malha": 0.3},
    ),
    Ocasiao.RESTAURANTE.value: OccasionProfile(
        key=Ocasiao.RESTAURANTE.value,
        label="Restaurante",
        formality_target=2.0,          # smart casual: almoço ou jantar sem cerimônia
        formality_tolerance=1.0,
        comfort_bias=0.2,
        layering_bias=0.3,
        color_discipline=COLOR_FREE,
        phrase="um restaurante",
    ),
    Ocasiao.JANTAR_ROMANTICO.value: OccasionProfile(
        key=Ocasiao.JANTAR_ROMANTICO.value,
        label="Jantar romântico",
        formality_target=2.5,          # entre smart casual e social
        formality_tolerance=1.0,
        comfort_bias=0.0,
        layering_bias=0.3,
        color_discipline=COLOR_STATEMENT,  # aqui um ponto de cor é bem-vindo
        phrase="um jantar romântico",
        category_bonus={"vestido": 0.8},
    ),
    Ocasiao.COM_AMIGAS.value: OccasionProfile(
        key=Ocasiao.COM_AMIGAS.value,
        label="Com amigas",
        formality_target=1.5,          # entre casual e smart casual
        formality_tolerance=1.2,
        comfort_bias=0.4,
        layering_bias=0.2,
        color_discipline=COLOR_STATEMENT,
        phrase="sair com as amigas",
        category_bonus={"acessorio": 0.4},
    ),
    Ocasiao.EVENTO_FORMAL.value: OccasionProfile(
        key=Ocasiao.EVENTO_FORMAL.value,
        label="Evento formal",
        formality_target=3.0,          # social, como a entrevista...
        formality_tolerance=0.8,
        comfort_bias=0.0,
        layering_bias=0.5,
        color_discipline=COLOR_STATEMENT,  # ...mas aqui a cor é permitida
        phrase="um evento formal",
        category_bonus={"vestido": 0.8, "blazer": 0.4},
    ),
    Ocasiao.VIAGEM.value: OccasionProfile(
        key=Ocasiao.VIAGEM.value,
        label="Viagem",
        formality_target=1.0,
        formality_tolerance=1.5,       # a mais elástica: o dia muda de cenário
        comfort_bias=1.0,              # horas sentada, depois horas andando
        layering_bias=0.9,             # avião/rodoviária gelados
        color_discipline=COLOR_NEUTRAL,  # neutro viaja melhor (tudo combina)
        phrase="uma viagem",
        wants_accessory=False,
    ),
    Ocasiao.ESPORTE.value: OccasionProfile(
        key=Ocasiao.ESPORTE.value,
        label="Esporte",
        formality_target=0.0,
        formality_tolerance=1.0,
        comfort_bias=1.0,
        layering_bias=0.1,
        color_discipline=COLOR_FREE,
        phrase="treinar",
        # Inviolável: nenhuma dessas peças vai para a academia, nem que o
        # guarda-roupa não tenha mais nada.
        forbidden_categories=frozenset({"blazer", "vestido", "saia"}),
        wants_accessory=False,
    ),
}

# Ocasião assumida quando a requisição não informa nenhuma (compatibilidade com
# chamadas antigas e com o script de seed).
DEFAULT_OCCASION = Ocasiao.DIA_A_DIA.value


def get_profile(ocasiao: Optional[str]) -> OccasionProfile:
    """
    Devolve o perfil da ocasião. Desconhecida ou ausente cai em `dia_a_dia` —
    o registro mais elástico, que degrada para o comportamento anterior à
    existência de ocasiões.
    """
    if not ocasiao:
        return OCCASION_PROFILES[DEFAULT_OCCASION]
    return OCCASION_PROFILES.get(ocasiao.strip().lower(), OCCASION_PROFILES[DEFAULT_OCCASION])
