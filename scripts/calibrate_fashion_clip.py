#!/usr/bin/env python3
"""
Script de calibração do FashionCLIP.

Recebe o caminho de uma pasta com imagens de roupa, roda a análise completa em
cada uma e imprime, por imagem e por campo, o rótulo escolhido e o score de
confiança (FashionCLIP), além do resultado das regras determinísticas e do
k-means de cor.

Objetivo: passar 20–30 fotos reais do guarda-roupa e observar em quais campos o
modelo acerta com confiança alta e em quais fica em dúvida, para ajustar os
limiares por campo (FASHION_CLIP_THRESHOLD_CATEGORIA / _ESTAMPA / _FORMALIDADE)
com base em dado real.

Uso:
    # a partir de miranda-api/, com a venv ativa:
    python scripts/calibrate_fashion_clip.py /caminho/para/pasta/de/imagens

Observações:
  - A primeira execução baixa os pesos do FashionCLIP (~600 MB) do HuggingFace.
  - A inferência roda em CPU: alguns segundos por imagem.
  - Este script NÃO aplica o limiar de confiança: mostra o score bruto de todos
    os campos justamente para você decidir onde cortar.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Garante que `app` seja importável ao rodar o script diretamente.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.services.ai import color_extraction, fashion_clip, labels, rules  # noqa: E402
from app.services.ai.clothing_analysis import CLOTHING_GATE_THRESHOLD  # noqa: E402

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _fmt(scored: list[tuple[str, float]], threshold: float, top_n: int = 3) -> str:
    lines = []
    for i, (value, score) in enumerate(scored[:top_n]):
        mark = "✓" if (i == 0 and score >= threshold) else " "
        lines.append(f"      {mark} {value:<16} {score:6.3f}")
    return "\n".join(lines)


def analyze_one(
    path: Path,
    thr_categoria: float,
    thr_estampa: float,
    thr_formalidade: float,
) -> None:
    print(f"\n━━━ {path.name} ━━━")
    data = path.read_bytes()

    # Portão de validação
    try:
        p_clothing = fashion_clip.clothing_probability(data)
    except Exception as exc:  # noqa: BLE001
        print(f"  [ERRO] FashionCLIP indisponível: {exc}")
        return
    gate = "APROVA" if p_clothing >= CLOTHING_GATE_THRESHOLD else "REPROVA"
    print(f"  portão de validação : p_roupa={p_clothing:.3f}  [{gate}] (limiar {CLOTHING_GATE_THRESHOLD})")

    # Campos do FashionCLIP — cada um com seu próprio limiar
    print(f"  categoria    (limiar {thr_categoria}):")
    print(_fmt(fashion_clip.classify(data, labels.CATEGORY_CANDIDATES), thr_categoria))
    print(f"  formalidade  (limiar {thr_formalidade}):")
    print(_fmt(fashion_clip.classify(data, labels.FORMALIDADE_CANDIDATES), thr_formalidade))
    print(f"  estampa      (limiar {thr_estampa}):")
    print(_fmt(fashion_clip.classify(data, labels.ESTAMPA_CANDIDATES), thr_estampa))

    # Cor (k-means)
    colors = color_extraction.extract_colors(data)
    print(
        f"  cor primária   : {colors.primary or '—'}"
        + (f"  (motivo: {colors.primary_reason})" if not colors.primary else "")
    )
    print(
        f"  cor secundária : {colors.secondary or '—'}"
        + (f"  (motivo: {colors.secondary_reason})" if not colors.secondary else "")
    )

    # Regras determinísticas (usam a categoria vencedora, seja qual for o score)
    top_cat = fashion_clip.classify(data, labels.CATEGORY_CANDIDATES)[0][0]
    rule_res = rules.apply_rules(top_cat)
    print(f"  regras (a partir de categoria='{top_cat}'):")
    print(f"      peso_termico : {rule_res.peso_termico or '—'}")
    print(f"      serve_chuva  : {rule_res.serve_chuva if rule_res.serve_chuva is not None else '—'}")
    print(f"      estacoes     : {rule_res.estacoes or '—'}")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1

    folder = Path(sys.argv[1])
    if not folder.is_dir():
        print(f"[ERRO] Não é uma pasta: {folder}")
        return 1

    images = sorted(p for p in folder.iterdir() if p.suffix.lower() in _IMAGE_EXTS)
    if not images:
        print(f"[ERRO] Nenhuma imagem ({', '.join(sorted(_IMAGE_EXTS))}) em {folder}")
        return 1

    thr_categoria = settings.FASHION_CLIP_THRESHOLD_CATEGORIA
    thr_estampa = settings.FASHION_CLIP_THRESHOLD_ESTAMPA
    thr_formalidade = settings.FASHION_CLIP_THRESHOLD_FORMALIDADE
    print(f"Analisando {len(images)} imagem(ns) de {folder}")
    print("Limiares de confiança atuais (por campo):")
    print(f"  categoria   (FASHION_CLIP_THRESHOLD_CATEGORIA)   : {thr_categoria}")
    print(f"  estampa     (FASHION_CLIP_THRESHOLD_ESTAMPA)     : {thr_estampa}")
    print(f"  formalidade (FASHION_CLIP_THRESHOLD_FORMALIDADE) : {thr_formalidade}")
    print("Dica: '✓' marca o campo que seria preenchido automaticamente com o limiar do campo.")

    for path in images:
        try:
            analyze_one(path, thr_categoria, thr_estampa, thr_formalidade)
        except Exception as exc:  # noqa: BLE001
            print(f"  [ERRO] Falha ao analisar {path.name}: {exc}")

    print("\nPronto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
