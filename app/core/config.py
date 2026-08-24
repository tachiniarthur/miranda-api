"""
Configuração central da aplicação.

Todas as variáveis sensíveis (string de conexão do banco, chave secreta do JWT,
etc.) são lidas de variáveis de ambiente / do arquivo `.env` na raiz de
`miranda-api`. Nunca coloque segredos reais diretamente no código — use o
arquivo `.env` (que está no .gitignore) baseado no `.env.example`.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Raiz do projeto backend (…/miranda-api). Usada para resolver caminhos
# relativos como a pasta de storage local, independente de onde o processo
# for iniciado.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Comprimento mínimo aceito para a chave de assinatura do JWT. 32 caracteres é
# o piso para que a chave tenha entropia comparável ao próprio HMAC-SHA256 —
# abaixo disso a assinatura é atacável por força bruta offline a partir de
# qualquer token capturado.
_MIN_JWT_SECRET_LENGTH = 32

# Valor de exemplo publicado no .env.example. Como o arquivo é versionado, esta
# chave é pública: subir com ela é o mesmo que não ter chave nenhuma, porque
# qualquer pessoa consegue forjar um JWT de qualquer usuário.
_JWT_SECRET_PLACEHOLDER = "troque-esta-chave-por-uma-aleatoria-e-secreta"


class Settings(BaseSettings):
    # ── Banco de dados ────────────────────────────────────────────────
    # Ex.: postgresql+psycopg2://miranda_user:senha@localhost:5432/miranda
    DATABASE_URL: str

    # ── Autenticação / JWT ────────────────────────────────────────────
    # Chave usada para assinar os tokens JWT. DEVE ser longa e aleatória em
    # produção. Gere uma com:  python -c "import secrets; print(secrets.token_urlsafe(48))"
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    # Claims `iss` (quem emitiu) e `aud` (para quem vale) gravadas em todo token
    # e EXIGIDAS na decodificação. Hoje há um só emissor e um só consumidor, mas
    # sem elas um token assinado com a mesma chave para outro fim (outro serviço
    # da mesma stack, um ambiente de staging que compartilhou a chave por engano)
    # seria aceito aqui sem questionamento.
    JWT_ISSUER: str = "miranda-api"
    JWT_AUDIENCE: str = "miranda-app"
    # Duração do token de acesso, em minutos (padrão: 12 horas).
    #
    # Era de 7 dias. Sem refresh token, o prazo do access token é a ÚNICA coisa
    # que limita o estrago de um vazamento — e uma semana é tempo demais para
    # um token que vive no localStorage do navegador. 12 horas cobre um dia de
    # uso sem pedir login a cada visita e reduz a janela de exposição em 14x.
    #
    # A troca de senha invalida os tokens imediatamente, independente do prazo
    # (ver `users.token_version`); o prazo cobre o caso em que o vazamento
    # passa despercebido e ninguém troca senha nenhuma.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 12
    # Duração do token de redefinição de senha, em minutos.
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 30

    # ── Servidor ──────────────────────────────────────────────────────
    # Somente localhost por padrão. "0.0.0.0" publica a API em TODAS as
    # interfaces de rede — inclusive o Wi-Fi — e deve ser uma escolha
    # deliberada (ex.: testar o app num celular físico), nunca o padrão.
    HOST: str = "127.0.0.1"
    PORT: int = 8000

    # ── Headers de segurança ──────────────────────────────────────────
    # HSTS instrui o navegador a só falar HTTPS com este host, por
    # `SECURITY_HSTS_MAX_AGE` segundos. Fica DESLIGADO por padrão porque em
    # desenvolvimento a API roda em http://localhost: enviar o header ali faria
    # o navegador travar o próprio localhost em HTTPS, quebrando o ambiente de
    # um jeito que se resolve limpando o estado HSTS do navegador. Ligue apenas
    # quando a API estiver de fato atrás de HTTPS.
    SECURITY_HSTS_ENABLED: bool = False
    SECURITY_HSTS_MAX_AGE: int = 31536000  # 1 ano

    # ── CORS ──────────────────────────────────────────────────────────
    # Origens permitidas para o frontend (Next.js em desenvolvimento).
    # Aceita uma lista separada por vírgula na variável de ambiente.
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"
    # Enviar credenciais (cookies / header Authorization) em requisições
    # cross-origin. Ver o guard `_reject_cors_wildcard_with_credentials`.
    CORS_ALLOW_CREDENTIALS: bool = True

    # ── IA (análise de peça, self-hosted) ─────────────────────────────
    # Limiares de confiança (score softmax) do FashionCLIP, UM POR CAMPO:
    # o rótulo vencedor só preenche o campo se o score for >= o limiar do campo;
    # abaixo disso o campo fica nulo, para o usuário preencher à mão. Os padrões
    # vêm da calibração com fotos reais (scripts/calibrate_fashion_clip.py):
    # categoria e estampa têm distribuição de confiança decisiva (topo perto de
    # 1.0), então toleram um limiar alto; a formalidade é estruturalmente mais
    # difusa (a probabilidade se divide entre casual/smart_casual/social), então
    # um limiar alto a deixaria quase sempre nula — por isso o corte mais baixo.
    FASHION_CLIP_THRESHOLD_CATEGORIA: float = 0.80
    FASHION_CLIP_THRESHOLD_ESTAMPA: float = 0.85
    FASHION_CLIP_THRESHOLD_FORMALIDADE: float = 0.60

    # ── IA (composição de look, API paga da Anthropic) ────────────────
    # Diferente da análise de peça — que é self-hosted e gratuita —, a
    # composição do look do dia chama a API do Claude e CUSTA DINHEIRO por
    # geração. Ver README, seção 12.
    #
    # A chave fica vazia por padrão de propósito: a aplicação DEVE subir sem
    # ela. Sem chave, a geração de look devolve uma nota explicando que não
    # foi possível gerar agora — o resto da API (guarda-roupa, autenticação,
    # análise de peça) continua funcionando normalmente. Derrubar o boot aqui
    # trocaria uma funcionalidade indisponível por um serviço fora do ar.
    ANTHROPIC_API_KEY: str = ""

    # Identificador do modelo, isolado numa variável para poder ser trocado
    # sem tocar em código. Preços (US$ por milhão de tokens) em
    # `services/ai/claude_client.py`; ao trocar de modelo, atualize-os lá.
    ANTHROPIC_MODEL: str = "claude-opus-5"

    # Teto de tokens de saída. O JSON de 3 looks fica na casa dos 600 tokens;
    # 4000 dá folga para o raciocínio adaptativo do modelo sem desperdiçar.
    ANTHROPIC_MAX_OUTPUT_TOKENS: int = 4000

    # Profundidade de raciocínio: low | medium | high | xhigh | max.
    #
    # Ocupa o lugar do antigo `temperature`, que os modelos atuais REJEITAM com
    # HTTP 400 ("temperature is deprecated for this model"). O objetivo aqui é
    # consistência e bom senso, não criatividade dispersiva — "medium" entrega
    # isso sem pagar o preço de "high" numa tarefa de escopo pequeno.
    ANTHROPIC_EFFORT: str = "medium"

    # Tentativas TOTAIS por geração (não tentativas adicionais). Cobre falha de
    # rede, rate limit e resposta impossível de interpretar. Poucas de
    # propósito: o usuário está esperando na tela.
    ANTHROPIC_MAX_ATTEMPTS: int = 3

    # Timeout por tentativa, em segundos.
    ANTHROPIC_TIMEOUT_SECONDS: float = 60.0

    # Cache de prompt no manual de estilo (o bloco do system).
    #
    # DESLIGADO por padrão, e isso é uma decisão de custo medida, não um
    # esquecimento. Gravar o cache tem ágio de 25% sobre o prefixo; ler dele
    # custa 10%. Como a janela do `ephemeral` é de 5 minutos, o cache só rende
    # se as gerações acontecerem a poucos minutos umas das outras. No volume
    # atual do produto elas são esparsas, então quase toda chamada seria uma
    # GRAVAÇÃO — e sairia ~9% mais cara do que sem cache nenhum.
    #
    # Ligue quando o produto tiver usuários de verdade gerando looks de forma
    # contínua. O prefixo é o mesmo para todo mundo, então é o volume TOTAL que
    # conta, não o de cada pessoa. Ver README, seção 12.
    ENABLE_PROMPT_CACHE: bool = False

    # ── Envio de e-mail ───────────────────────────────────────────────
    # Três backends, trocados por variável — nenhum código muda ao hospedar:
    #   console — só registra no log. Padrão, e o que roda nos testes.
    #   smtp    — Mailpit local (docker compose up -d). Captura tudo numa caixa
    #             web em http://localhost:8025 e NÃO envia nada para fora.
    #   resend  — serviço transacional real. Exige RESEND_API_KEY e, para
    #             enviar a endereços que não sejam o seu, um domínio verificado.
    #
    # O padrão é `console` de propósito: a aplicação precisa subir e funcionar
    # numa máquina sem Docker e sem conta em serviço nenhum.
    EMAIL_BACKEND: str = "console"
    EMAIL_FROM: str = "Miranda <nao-responda@miranda.local>"

    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 1025

    RESEND_API_KEY: str = ""

    # Base das URLs que vão DENTRO dos e-mails (o frontend, não a API). É o que
    # o usuário clica, então precisa ser o endereço público do app.
    APP_BASE_URL: str = "http://localhost:3000"

    # ── Verificação de e-mail ─────────────────────────────────────────
    # Exigir e-mail verificado para entrar.
    #
    # DESLIGADO por padrão, e isso é deliberado. Ligar isto torna a aplicação
    # inutilizável se o servidor de e-mail não estiver de pé — uma trava que
    # depende de um contêiner rodando é uma trava que prende o dono do projeto.
    # Além disso, as contas criadas ANTES desta feature não têm como ter
    # verificado nada e ficariam trancadas para fora.
    #
    # Ligue quando houver entrega de e-mail confiável (EMAIL_BACKEND=resend com
    # domínio verificado) e depois de verificar as contas que já existem.
    #
    # Com a flag ligada, só o LOGIN é recusado (403). O cadastro não autentica
    # ninguém, então segue respondendo igual — e o usuário tem o link no e-mail
    # e a rota de reenvio para sair desse estado.
    REQUIRE_VERIFIED_EMAIL: bool = False

    EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS: int = 24

    # ── Storage local ─────────────────────────────────────────────────
    # Pasta física onde as imagens das peças são gravadas.
    STORAGE_DIR: str = str(BASE_DIR / "storage" / "clothing_images")
    # URL pública base da própria API — usada para montar as URLs absolutas
    # das imagens retornadas ao frontend. As imagens NÃO são mais servidas por
    # um mount estático público: cada uma passa pela rota autenticada
    # /api/wardrobe/items/{id}/image, que confere a posse da peça.
    PUBLIC_BASE_URL: str = "http://localhost:8000"

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def _reject_weak_jwt_secret(cls, v: str) -> str:
        """
        Recusa a inicialização se a chave do JWT for fraca ou for o placeholder.

        Falhar no boot é intencional: uma chave fraca não degrada o serviço de
        forma visível — ela apenas torna todos os tokens forjáveis em silêncio.
        Melhor a aplicação não subir do que subir insegura.
        """
        v = v.strip()
        if v == _JWT_SECRET_PLACEHOLDER:
            raise ValueError(
                "JWT_SECRET_KEY ainda é o valor de exemplo do .env.example, que é "
                "público. Gere uma chave própria com: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        if len(v) < _MIN_JWT_SECRET_LENGTH:
            raise ValueError(
                f"JWT_SECRET_KEY tem {len(v)} caracteres; o mínimo é "
                f"{_MIN_JWT_SECRET_LENGTH}. Gere uma chave forte com: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return v

    @model_validator(mode="after")
    def _reject_cors_wildcard_with_credentials(self) -> "Settings":
        """
        Recusa a combinação `CORS_ORIGINS=*` com `CORS_ALLOW_CREDENTIALS=true`.

        A combinação é proibida pela especificação de CORS, mas o Starlette a
        aceita e a implementa ECOANDO a origem de quem pediu no
        `Access-Control-Allow-Origin` — o que, na prática, libera qualquer site
        da internet a chamar esta API com as credenciais da vítima e ler a
        resposta. Como o navegador não reclama (o header vem bem formado), o
        erro passaria despercebido; por isso falhamos aqui, no boot.
        """
        if "*" in self.cors_origins_list and self.CORS_ALLOW_CREDENTIALS:
            raise ValueError(
                "CORS_ORIGINS=* com CORS_ALLOW_CREDENTIALS=true é inseguro: "
                "libera qualquer origem a chamar a API com as credenciais do "
                "usuário. Liste as origens explicitamente (ex.: "
                "http://localhost:3000) ou defina CORS_ALLOW_CREDENTIALS=false."
            )
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    """Retorna a instância única de configuração (cacheada)."""
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
