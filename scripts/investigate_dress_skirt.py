#!/usr/bin/env python3
"""
Investiga a confusão entre `vestido` e `saia` no zero-shot do FashionCLIP.

Não é um teste: é o instrumento para responder UMA pergunta — o erro relatado
foi ambiguidade daquela imagem, ou confusão sistemática entre as duas classes?

A diferença é observável nos números:
  · SISTEMÁTICA — `saia` vence em várias imagens de vestido, e a margem entre
    as duas é estreita em quase todas. O problema está nos prompts.
  · AMBIGUIDADE — o erro é isolado e as outras imagens separam com folga. O
    problema está naquela foto.

Uso:
    PYTHONPATH=. .venv/bin/python scripts/investigate_dress_skirt.py

Imprime, para cada imagem de test-images/: o vencedor geral, p(vestido),
p(saia) e a margem entre as duas.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.ai.fashion_clip import classify  # noqa: E402
from app.services.ai.labels import CATEGORY_CANDIDATES  # noqa: E402

TEST_IMAGES = Path(__file__).resolve().parent.parent / "test-images"


def main() -> None:
    imagens = sorted(
        TEST_IMAGES.glob("*.jpg"), key=lambda p: int(p.stem) if p.stem.isdigit() else 0
    )
    if not imagens:
        sys.exit(f"Nenhuma imagem em {TEST_IMAGES}")

    print(f"{'imagem':<10} {'vencedor':<12} {'p(vest)':>8} {'p(saia)':>8} {'margem':>8}")
    print("─" * 50)

    confusoes = []
    for caminho in imagens:
        scored = dict(classify(str(caminho), CATEGORY_CANDIDATES))
        vencedor = max(scored, key=scored.get)
        pv, ps = scored.get("vestido", 0.0), scored.get("saia", 0.0)
        margem = abs(pv - ps)
        print(f"{caminho.name:<10} {vencedor:<12} {pv:>8.3f} {ps:>8.3f} {margem:>8.3f}")
        if {vencedor} & {"vestido", "saia"} and margem < 0.15:
            confusoes.append((caminho.name, vencedor, pv, ps, margem))

    print("\n── Casos de margem estreita (< 0.15) entre vestido e saia ──")
    if not confusoes:
        print("  nenhum — as duas classes separam com folga em todas as imagens")
    for nome, vencedor, pv, ps, margem in confusoes:
        print(
            f"  {nome}: venceu {vencedor} · vestido={pv:.3f} saia={ps:.3f} "
            f"margem={margem:.3f}"
        )

    print(
        "\nLeitura: muitos casos de margem estreita = confusão SISTEMÁTICA "
        "(mexa nos prompts).\nPoucos ou nenhum = ambiguidade da imagem "
        "específica (documente e siga)."
    )


if __name__ == "__main__":
    main()
