"""
Validação manual da qualidade da composição, contra a API REAL e o banco real.

CUSTA DINHEIRO: uma chamada paga por cenário. Não é um teste automatizado e não
roda em suíte — é o instrumento para olhar os looks e julgar se a Miranda está
com bom gosto.

Uso:
    PYTHONPATH=. .venv/bin/python scripts/validate_look_live.py [email-do-usuario]

Sem argumento, escolhe o usuário com mais peças cadastradas.
"""

from __future__ import annotations

import logging
import sys

from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.clothing_item import ClothingItem
from app.models.user import User
from app.services.ai import claude_client
from app.services.ai.look_generation import generate_daily_look
from app.services.look_service import _item_to_payload

# O log de custo do claude_client é o ponto do exercício — deixe-o visível.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

CENARIOS = [
    (
        "Dia quente e ensolarado — dia a dia",
        {"temperatura_min": 23.0, "temperatura_max": 33.0, "condicoes": ["sol"]},
        "dia_a_dia",
    ),
    (
        "Dia frio, com chuva e vento — trabalho",
        {"temperatura_min": 7.0, "temperatura_max": 14.0,
         "condicoes": ["chuva", "frio", "vento"]},
        "trabalho",
    ),
]


def _pick_user(db, email: str | None) -> User:
    if email:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            sys.exit(f"Usuário '{email}' não encontrado.")
        return user

    row = db.execute(
        select(User, func.count(ClothingItem.id).label("n"))
        .join(ClothingItem, ClothingItem.user_id == User.id)
        .group_by(User.id)
        .order_by(func.count(ClothingItem.id).desc())
        .limit(1)
    ).first()
    if row is None:
        sys.exit("Nenhum usuário com peças cadastradas.")
    return row[0]


def main() -> None:
    if not settings.ANTHROPIC_API_KEY:
        sys.exit("ANTHROPIC_API_KEY não configurada no .env.")

    email = sys.argv[1] if len(sys.argv) > 1 else None
    db = SessionLocal()
    try:
        user = _pick_user(db, email)
        items = db.scalars(
            select(ClothingItem).where(ClothingItem.user_id == user.id)
        ).all()
        payload = [_item_to_payload(i) for i in items]
        by_id = {p["id"]: p for p in payload}

        print(f"\nUsuário: {user.email}  ·  {len(payload)} peças")
        print(f"Modelo:  {settings.ANTHROPIC_MODEL}  ·  effort={settings.ANTHROPIC_EFFORT}")

        for titulo, weather, ocasiao in CENARIOS:
            print("\n" + "═" * 72)
            print(f"  {titulo}")
            print(f"  {weather['temperatura_min']}–{weather['temperatura_max']}°C, "
                  f"{', '.join(weather['condicoes'])}")
            print("═" * 72)

            result = generate_daily_look(payload, weather, ocasiao=ocasiao)

            if result["note"]:
                print(f"\n  [nota] {result['note']}")
            for look in result["looks"]:
                print(f"\n  Look {look['label']}")
                for entry in look["items"]:
                    peca = by_id[entry["item_id"]]
                    cor = peca.get("cor_primaria") or "—"
                    print(f"    · {entry['role']:<16} {peca['name']}  ({cor})")
                print(f"    “{look['commentary']}”")
    finally:
        db.close()

    print("\nOs tokens e o custo de cada chamada saíram nas linhas INFO acima.")
    print(f"Preços do modelo: {claude_client.MODEL_PRICES_USD_PER_MTOK.get(settings.ANTHROPIC_MODEL)}")


if __name__ == "__main__":
    main()
