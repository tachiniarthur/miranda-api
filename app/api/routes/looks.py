"""
Rotas do "look do dia".

A composição usa a API do Claude (services/ai/look_generation), precedida de um
pré-filtro determinístico e gratuito por clima e ocasião. A rota chama a
geração, persiste o registro e devolve os looks.

Nunca devolve erro por falta de peças nem por indisponibilidade da API: nesses
casos a resposta vem com `looks` vazio e uma `note` explicativa, em HTTP 200.

É a única rota do sistema que gasta dinheiro, e por isso a única com dois tetos:
o rate limit por usuário aqui e a quota diária persistida em `look_service`.
"""

# NOTA: este módulo deliberadamente NÃO usa `from __future__ import annotations`.
# O decorator @limiter.limit do slowapi embrulha o endpoint com functools.wraps,
# que não copia `__globals__`. Com as anotações adiadas (em string), o FastAPI
# tentaria resolvê-las contra os globals do slowapi — onde os schemas deste
# módulo não existem — e passaria a tratar `payload` como query param, quebrando
# o corpo das requisições. É a mesma armadilha já documentada em
# app/api/routes/auth.py e app/api/routes/wardrobe.py.

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter, user_or_ip_key
from app.models.user import User
from app.schemas.look import GenerateLookRequest, GenerateLookResponse
from app.services import look_service

router = APIRouter(prefix="/looks", tags=["looks"])


@router.post("/generate", response_model=GenerateLookResponse)
# O teto vai num lambda, e não na string direta, para ser lido a cada
# requisição: como string, o decorator congelaria o valor no import e mudar a
# configuração exigiria reiniciar o processo.
@limiter.limit(lambda: settings.LOOK_RATE_LIMIT, key_func=user_or_ip_key)
def generate_look(
    request: Request,
    response: Response,
    payload: GenerateLookRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> GenerateLookResponse:
    """
    Gera o look do dia a partir do clima informado e do guarda-roupa do usuário.

    Dois tetos, de propósito. O `@limiter.limit` acima é a barreira barata, que
    rejeita antes de tocar o banco — mas mora no Redis, que pode cair para
    memória sem avisar. O teto real é a quota diária de `look_service`, contada
    em `looks_history`: não depende de Redis nenhum e sobrevive a reinício.

    `request` e `response` não são usados no corpo, mas o slowapi os exige na
    assinatura para montar os cabeçalhos de 429.
    """
    return look_service.generate_look(db, user_id=current_user.id, payload=payload)
