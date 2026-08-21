"""
Enumerações de domínio de moda, compartilhadas entre os modelos ORM
(colunas Enum do Postgres) e os schemas Pydantic (validação de entrada).

Os *valores* dos enums são strings normalizadas (minúsculas, sem acento) para
serem seguros como valores de ENUM do Postgres e como parâmetros de query.
O frontend exibe rótulos amigáveis correspondentes.
"""

from enum import Enum


class ClothingCategory(str, Enum):
    BLAZER = "blazer"
    VESTIDO = "vestido"
    CALCA = "calca"
    CAMISA = "camisa"
    CASACO = "casaco"
    MALHA = "malha"
    SAIA = "saia"
    CALCADO = "calcado"
    CACHECOL = "cachecol"
    ACESSORIO = "acessorio"
    OUTROS = "outros"


class Formalidade(str, Enum):
    CASUAL = "casual"
    SMART_CASUAL = "smart_casual"
    SOCIAL = "social"
    ESPORTE = "esporte"


class PesoTermico(str, Enum):
    LEVE = "leve"
    MEDIO = "medio"
    PESADO = "pesado"


class Estacao(str, Enum):
    VERAO = "verao"
    MEIA_ESTACAO = "meia_estacao"
    INVERNO = "inverno"


class CondicaoClimatica(str, Enum):
    """
    Condições do dia. São COMBINÁVEIS (o usuário marca quantas quiser: sol com
    vento, chuva com frio, etc.), por isso a entrada da API é uma lista.

    `frio` é redundante com a temperatura informada e permanece na lista de
    propósito: é o único jeito de o usuário sinalizar "sensação térmica baixa"
    quando o termômetro não conta a história toda (vento gelado, umidade).
    """

    SOL = "sol"
    NUBLADO = "nublado"
    CHUVA = "chuva"
    VENTO = "vento"
    FRIO = "frio"


class Ocasiao(str, Enum):
    """
    Para o que a pessoa precisa do look. Diferente das condições climáticas, a
    ocasião é ÚNICA por geração — um look não serve a dois registros ao mesmo
    tempo, e deixar escolher várias só diluiria o alvo de formalidade.

    O perfil de cada ocasião (alvo de formalidade, viés de conforto, disciplina
    de cor, categorias proibidas) vive em `services/ai/occasions.py`.
    """

    TRABALHO = "trabalho"
    REUNIAO = "reuniao"
    ENTREVISTA = "entrevista"
    DIA_A_DIA = "dia_a_dia"
    SHOPPING = "shopping"
    FACULDADE = "faculdade"
    RESTAURANTE = "restaurante"
    JANTAR_ROMANTICO = "jantar_romantico"
    COM_AMIGAS = "com_amigas"
    EVENTO_FORMAL = "evento_formal"
    VIAGEM = "viagem"
    ESPORTE = "esporte"
