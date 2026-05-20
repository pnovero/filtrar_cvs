"""Llamada al LLM via LiteLLM, provider-agnóstica con fallbacks y telemetría de costo."""
import logging
import time
from dataclasses import dataclass
from typing import Type, TypeVar

import litellm
from pydantic import BaseModel

from .config import DEFAULT_MODEL_TIER, MODEL_TIERS

logger = logging.getLogger(__name__)

# Drop the default JSON-schema validator y deja que Pydantic valide en client-side.
litellm.drop_params = True

T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMUsage:
    model: str  # modelo que efectivamente respondió (puede ser un fallback)
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_s: float


def _resolve_model_chain(model_tier: str) -> tuple[str, list[str]]:
    """Devuelve (primary, fallbacks) para el tier. Acepta tanto str como list[str] en MODEL_TIERS."""
    if model_tier not in MODEL_TIERS:
        raise ValueError(
            f"model_tier desconocido: {model_tier}. Disponibles: {list(MODEL_TIERS)}"
        )
    chain = MODEL_TIERS[model_tier]
    if isinstance(chain, str):
        return chain, []
    if not chain:
        raise ValueError(f"MODEL_TIERS['{model_tier}'] está vacío")
    return chain[0], list(chain[1:])


def call_llm(
    messages: list[dict],
    response_model: Type[T],
    model_tier: str = DEFAULT_MODEL_TIER,
) -> tuple[T, LLMUsage]:
    """Invoca al LLM y devuelve la respuesta parseada como Pydantic + telemetría.

    Si el primary del tier tira RateLimitError u otro error transitorio, LiteLLM
    salta automáticamente al siguiente modelo de la cadena (vía `fallbacks=`).
    `LLMUsage.model` refleja el modelo que efectivamente respondió.
    """
    primary, fallbacks = _resolve_model_chain(model_tier)

    t0 = time.perf_counter()
    response = litellm.completion(
        model=primary,
        messages=messages,
        response_format=response_model,
        temperature=0,
        fallbacks=fallbacks,
        num_retries=2,
    )
    latency = time.perf_counter() - t0

    raw = response.choices[0].message.content
    parsed = response_model.model_validate_json(raw)

    usage = response.usage
    cost = float(getattr(response, "_hidden_params", {}).get("response_cost") or 0.0)
    actual_model = getattr(response, "model", None) or primary

    telemetry = LLMUsage(
        model=actual_model,
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        cost_usd=cost,
        latency_s=round(latency, 3),
    )
    if actual_model != primary:
        logger.warning(
            "LLM fallback model=%s primary=%s tier=%s",
            actual_model, primary, model_tier,
        )
    logger.info(
        "LLM ok model=%s in=%d out=%d cost=$%.6f latency=%.2fs",
        telemetry.model,
        telemetry.input_tokens,
        telemetry.output_tokens,
        telemetry.cost_usd,
        telemetry.latency_s,
    )
    return parsed, telemetry
