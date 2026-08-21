"""
Lista local de senhas triviais recusadas no cadastro e na troca de senha.

O `min_length=8` dos schemas garante comprimento, não imprevisibilidade:
"12345678" e "senha123" têm 8 caracteres e estão no topo de qualquer dicionário
de ataque. Como as rotas de auth têm rate limit (5 tentativas por 15 minutos),
o ataque realista não é força bruta cega e sim o punhado de palpites óbvios —
que é exatamente o que esta lista fecha.

Deliberadamente local e curta: um serviço externo (ex.: Have I Been Pwned)
acrescentaria uma dependência de rede num caminho de cadastro que hoje é
totalmente self-hosted. A lista cobre o essencial em português e inglês; vale
crescer conforme necessário, não vale virar um arquivo de milhões de linhas
carregado em memória.

A comparação é feita em minúsculas e sem espaços nas pontas, então "Senha123" e
" senha123 " também são recusadas.
"""

from __future__ import annotations

COMMON_PASSWORDS: frozenset[str] = frozenset(
    {
        # Sequências numéricas
        "12345678",
        "123456789",
        "1234567890",
        "123123123",
        "11111111",
        "00000000",
        "87654321",
        "12341234",
        # Português
        "senha123",
        "senha1234",
        "senha12345",
        "minhasenha",
        "senhasenha",
        "brasil123",
        "flamengo",
        "corinthians",
        "palmeiras",
        "gremio123",
        "teste123",
        "mudar123",
        "alterar123",
        "administrador",
        "principal",
        "pessoal123",
        # Inglês / universais
        "password",
        "password1",
        "password123",
        "passw0rd",
        "qwertyui",
        "qwerty123",
        "iloveyou",
        "sunshine",
        "princess",
        "football",
        "baseball",
        "superman",
        "trustno1",
        "welcome1",
        "welcome123",
        "admin123",
        "administrator",
        "letmein123",
        "monkey123",
        "dragon123",
        "abc12345",
        "abcd1234",
        "asdfghjk",
        "zxcvbnm123",
        "changeme",
        "changeme123",
        "secret123",
        "default123",
        # Relacionadas ao próprio produto
        "miranda123",
        "mirandaapi",
        "miranda2024",
        "miranda2025",
    }
)

# Mensagem única, usada pelos schemas de cadastro e de troca de senha.
COMMON_PASSWORD_MESSAGE = (
    "Esta senha é conhecida demais e está entre as primeiras que um atacante "
    "tenta. Escolha outra — de preferência uma frase com palavras que só você "
    "associaria."
)


def is_common_password(password: str) -> bool:
    """Diz se a senha está na lista de senhas triviais."""
    return password.strip().lower() in COMMON_PASSWORDS
