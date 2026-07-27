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
