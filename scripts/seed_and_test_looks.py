"""
Semeia um guarda-roupa de demonstração para o usuário de teste e exercita a
geração de look em dois cenários de clima, verificando a persistência em
looks_history.

Idempotente: remove qualquer peça semeada anteriormente (image_path começando
com 'seed_') antes de inserir de novo. NÃO toca nas peças reais do usuário.

Uso: .venv/bin/python scripts/seed_and_test_looks.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.clothing_item import ClothingItem
from app.models.look_history import LookHistory
from app.models.user import User
from app.schemas.look import GenerateLookRequest
from app.services import look_service

TEST_EMAIL = "teste@gmail.com"

STORAGE_DIR = Path(settings.STORAGE_DIR)
TEST_IMAGES = Path(__file__).resolve().parent.parent / "test-images"

# (categoria, nome, cor_primaria, formalidade, peso_termico, serve_chuva)
DEMO = [
    ("calca", "Calça de alfaiataria preta", "preto", "social", "medio", False),
    ("calca", "Calça jeans escura", "azul-marinho", "casual", "medio", True),
    ("calca", "Calça de moletom cinza", "cinza", "esporte", "medio", None),
    ("saia", "Saia de linho bege", "bege", "smart_casual", "leve", None),
    ("camisa", "Camisa de seda off-white", "off-white", "social", "leve", False),
    ("camisa", "Camisa oxford azul", "azul", "smart_casual", "leve", None),
    ("malha", "Malha canelada cinza", "cinza", "casual", "medio", None),
    ("malha", "Suéter de tricô bege", "bege", "casual", "pesado", None),
    ("blazer", "Blazer de alfaiataria preto", "preto", "social", "medio", None),
    ("casaco", "Casaco de lã grafite", "grafite", "social", "pesado", True),
    ("casaco", "Parka verde-oliva", "verde-oliva", "casual", "pesado", True),
    ("vestido", "Vestido midi off-white", "off-white", "social", "leve", None),
    ("calcado", "Bota de couro marrom", "marrom", "smart_casual", None, True),
    ("calcado", "Tênis branco", "branco", "casual", None, False),
    ("cachecol", "Cachecol de lã cinza", "cinza", None, "pesado", None),
]


def seed(db, user_id):
    # Remove peças semeadas anteriormente (idempotência), sem tocar nas reais.
    old = db.scalars(
        select(ClothingItem).where(
            ClothingItem.user_id == user_id,
            ClothingItem.image_path.like("seed_%"),
        )
    ).all()
    for it in old:
        db.delete(it)
    db.commit()

    src_images = sorted(TEST_IMAGES.glob("*.jpg"))
    for i, (cat, name, cor, formal, peso, chuva) in enumerate(DEMO):
        image_path = f"seed_{i:02d}.jpg"
        # Copia uma imagem de teste para o storage para que a image_url resolva.
        if src_images:
            shutil.copyfile(src_images[i % len(src_images)], STORAGE_DIR / image_path)
        db.add(
            ClothingItem(
                user_id=user_id,
                name=name,
                category=cat,
                image_path=image_path,
                cor_primaria=cor,
                formalidade=formal,
                peso_termico=peso,
                serve_chuva=chuva,
            )
        )
    db.commit()
    print(f"Semeadas {len(DEMO)} peças de demonstração.")


def run_scenario(db, user_id, label, tmin, tmax, cond):
    print(f"\n{'='*66}\n{label}  ({tmin}°–{tmax}°, {cond})\n{'='*66}")
    payload = GenerateLookRequest(
        temperatura_min=tmin, temperatura_max=tmax, condicao_climatica=cond
    )
    resp = look_service.generate_look(db, user_id=user_id, payload=payload)
    if resp.note:
        print(f"NOTA: {resp.note}")
    if not resp.looks:
        print("(sem looks)")
    for lk in resp.looks:
        print(f"\n  Look {lk.label}")
        for p in lk.items:
            print(f"    · {p.role:<14} {p.name}  [{p.cor_primaria or '—'}]")
        print(f"    » {lk.commentary}")
    return resp


def main():
    db = SessionLocal()
    try:
        user = db.scalars(select(User).where(User.email == TEST_EMAIL)).first()
        if user is None:
            print(f"Usuário {TEST_EMAIL} não encontrado.")
            return
        seed(db, user.id)

        before = db.scalars(
            select(LookHistory).where(LookHistory.user_id == user.id)
        ).all()
        print(f"\nlooks_history antes: {len(before)} registro(s).")

        run_scenario(db, user.id, "CENÁRIO A — quente e ensolarado", 24, 33, "sol")
        run_scenario(db, user.id, "CENÁRIO B — frio e chuvoso", 6, 12, "chuva")

        after = db.scalars(
            select(LookHistory)
            .where(LookHistory.user_id == user.id)
            .order_by(LookHistory.created_at.desc())
        ).all()
        print(f"\n{'='*66}\nlooks_history depois: {len(after)} registro(s).")
        for h in after[:2]:
            n_looks = len((h.itens_sugeridos or {}).get("looks", []))
            print(
                f"  • {h.condicao_climatica} {h.temperatura_min}°–{h.temperatura_max}° "
                f"→ {n_looks} look(s) | justificativa: "
                f"{(h.justificativa or '')[:60].replace(chr(10), ' ')}…"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
