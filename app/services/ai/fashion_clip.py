"""
Wrapper do FashionCLIP para classificação zero-shot, 100% self-hosted.

O modelo (`patrickjohncyh/fashion-clip`, publicado no HuggingFace) é um CLIP
afinado em dados de moda. Usamos zero-shot: computamos a similaridade entre a
imagem e uma lista de descrições em inglês e aplicamos softmax para obter, para
cada rótulo, uma probabilidade (o "score de confiança").

Carregamento:
  - O modelo é carregado UMA ÚNICA VEZ (lazy singleton) e reutilizado entre
    requisições — nunca recarregado a cada chamada.
  - A PRIMEIRA execução baixa os pesos (~600 MB) do HuggingFace, o que exige
    internet e leva algum tempo. A partir daí tudo roda localmente, sem internet
    (os pesos ficam no cache do HuggingFace, ex.: ~/.cache/huggingface).
  - A inferência roda em CPU. É suficiente para uma imagem por vez, mas pode ter
    alguns segundos de latência.

Nenhuma API paga é usada aqui: transformers + torch rodam localmente.
"""

from __future__ import annotations

import io
import logging
import threading
from typing import TYPE_CHECKING

from app.services.ai.labels import Candidate

if TYPE_CHECKING:  # imports pesados só para tipagem, não em runtime
    from PIL import Image

logger = logging.getLogger("miranda.ai.fashion_clip")

MODEL_NAME = "patrickjohncyh/fashion-clip"

# Estado do singleton, protegido por lock para o caso de duas requisições
# tentarem inicializar o modelo ao mesmo tempo.
_lock = threading.Lock()
_model = None
_processor = None
_load_failed = False


class ModelUnavailableError(RuntimeError):
    """O modelo FashionCLIP não pôde ser carregado (falha de rede, memória...)."""


def _load() -> tuple[object, object]:
    """
    Carrega (uma vez) o modelo e o processador do FashionCLIP.

    Raises:
        ModelUnavailableError: se o carregamento falhar (a orquestração trata
            isso como degradação graciosa, não como erro fatal).
    """
    global _model, _processor, _load_failed

    if _model is not None and _processor is not None:
        return _model, _processor
    if _load_failed:
        raise ModelUnavailableError("Carregamento do FashionCLIP já falhou anteriormente.")

    with _lock:
        # Recheca dentro do lock (outra thread pode ter carregado nesse meio-tempo).
        if _model is not None and _processor is not None:
            return _model, _processor
        if _load_failed:
            raise ModelUnavailableError("Carregamento do FashionCLIP já falhou anteriormente.")

        try:
            import torch  # noqa: F401  (garante que o backend está disponível)
            from transformers import CLIPModel, CLIPProcessor

            logger.info("Carregando FashionCLIP (%s) — primeira vez pode baixar os pesos…", MODEL_NAME)
            model = CLIPModel.from_pretrained(MODEL_NAME)
            processor = CLIPProcessor.from_pretrained(MODEL_NAME)
            model.eval()
            _model, _processor = model, processor
            logger.info("FashionCLIP carregado e pronto (rodando em CPU).")
            return _model, _processor
        except Exception as exc:  # noqa: BLE001 — qualquer falha vira indisponibilidade
            _load_failed = True
            logger.warning("Falha ao carregar o FashionCLIP: %s", exc)
            raise ModelUnavailableError(str(exc)) from exc


def is_available() -> bool:
    """Retorna True se o modelo pôde ser carregado (tenta carregar de forma preguiçosa)."""
    try:
        _load()
        return True
    except ModelUnavailableError:
        return False


def _to_pil(image: "str | bytes | Image.Image") -> "Image.Image":
    from PIL import Image

    from app.services.image_validation import apply_pillow_limits

    # Teto de pixels também aqui: este caminho é alcançável fora do HTTP
    # (scripts, testes), onde a validação da rota não roda.
    apply_pillow_limits()

    if isinstance(image, Image.Image):
        pil = image
    elif isinstance(image, (bytes, bytearray)):
        pil = Image.open(io.BytesIO(bytes(image)))
    elif isinstance(image, str):
        pil = Image.open(image)
    else:
        raise TypeError(f"Tipo de imagem não suportado: {type(image)!r}")
    # FashionCLIP espera RGB; achatamos qualquer transparência sobre branco.
    if pil.mode in ("RGBA", "LA", "P"):
        from PIL import Image as _Image

        background = _Image.new("RGB", pil.size, (255, 255, 255))
        pil = pil.convert("RGBA")
        background.paste(pil, mask=pil.split()[-1])
        pil = background
    else:
        pil = pil.convert("RGB")
    return pil


def _prompt_scores(pil: "Image.Image", prompts: list[str]) -> list[float]:
    """
    Similaridade (logit) entre a imagem e cada prompt, sem softmax ainda.

    Retorna os logits crus de similaridade imagem↔texto do CLIP.
    """
    import torch

    model, processor = _load()
    inputs = processor(text=prompts, images=pil, return_tensors="pt", padding=True)
    with torch.no_grad():
        outputs = model(**inputs)
    # logits_per_image: (1, n_prompts)
    logits = outputs.logits_per_image[0]
    return [float(x) for x in logits]


def classify(
    image: "str | bytes | Image.Image",
    candidates: list[Candidate],
) -> list[tuple[str, float]]:
    """
    Classifica a imagem entre os `candidates` via zero-shot.

    Para cada candidato, usa o MAIOR logit entre seus prompts como o score do
    candidato; aplica softmax sobre os candidatos e retorna
    `[(value, probability), ...]` ordenado do mais provável ao menos provável.
    """
    import torch

    pil = _to_pil(image)

    # Achata todos os prompts numa lista só (uma passada pelo modelo) e depois
    # reagrupa por candidato, pegando o máximo logit de cada grupo.
    flat_prompts: list[str] = []
    spans: list[tuple[int, int]] = []
    for cand in candidates:
        start = len(flat_prompts)
        flat_prompts.extend(cand.prompts)
        spans.append((start, len(flat_prompts)))

    logits = _prompt_scores(pil, flat_prompts)
    per_candidate = torch.tensor(
        [max(logits[start:end]) for (start, end) in spans],
        dtype=torch.float32,
    )
    probs = torch.softmax(per_candidate, dim=0)

    scored = [(cand.value, float(p)) for cand, p in zip(candidates, probs)]
    scored.sort(key=lambda t: t[1], reverse=True)
    return scored


def clothing_probability(image: "str | bytes | Image.Image") -> float:
    """
    Probabilidade de a imagem ser uma peça de roupa (portão de validação).

    Compara a massa de similaridade entre os prompts "é roupa" e "não é roupa"
    via softmax sobre todos eles juntos, somando a probabilidade do grupo "roupa".
    """
    import torch

    from app.services.ai.labels import CLOTHING_PROMPTS, NOT_CLOTHING_PROMPTS

    pil = _to_pil(image)
    n_clothing = len(CLOTHING_PROMPTS)
    all_prompts = CLOTHING_PROMPTS + NOT_CLOTHING_PROMPTS

    logits = torch.tensor(_prompt_scores(pil, all_prompts), dtype=torch.float32)
    probs = torch.softmax(logits, dim=0)
    return float(probs[:n_clothing].sum())
