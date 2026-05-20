"""Procesamiento de un único CV: lee job.json, llama al LLM, escribe resultado/error."""
import logging
import mimetypes
import os
import traceback
from datetime import datetime, timezone
from typing import Dict

from . import storage
from .config import (
    GCS_BUCKET,
    IMAGE_FORMATS,
    TEXT_FORMATS,
    VISION_CAPABLE_TIERS,
    VISION_FALLBACK_TIER,
)
from .llm import call_llm
from .prompt import build_image_user_message, build_system_message, build_text_user_message
from .pydantic_models import AnalisisCVOutput, FlagsEvaluacion, Outputllm, Weights
from .text_extraction import cargar_contenido_texto

logger = logging.getLogger(__name__)


def _job_prefix(job_id: str) -> str:
    return f"jobs/{job_id}"


def _classify(cv_name: str) -> str:
    ext = os.path.splitext(cv_name)[1].lower()
    if ext in TEXT_FORMATS:
        return "text"
    if ext in IMAGE_FORMATS:
        return "image"
    raise ValueError(f"Extensión no soportada: {ext} ({cv_name})")


def calcular_score_ponderado(
    flags: FlagsEvaluacion, pesos: Dict[str, float] | None = None
) -> int:
    """Score 0-100 ponderado por `pesos`. Flags None no contribuyen."""
    if pesos is None:
        pesos = Weights().to_dict()

    total_peso = sum(pesos.values())
    if abs(total_peso - 1.0) > 0.01:
        raise ValueError(f"Los pesos deben sumar 1.0, suman {total_peso}")

    score = 0.0
    for campo, peso in pesos.items():
        if not hasattr(flags, campo):
            continue
        valor = getattr(flags, campo)
        if valor is None:
            continue
        if campo == "experiencia_relevante":
            score += peso * (valor / 5.0) * 100
        elif valor:
            score += peso * 100

    return int(round(score))


def process_one_cv(job_id: str, cv_name: str) -> None:
    """Procesa un CV: extrae texto/imagen, llama LLM, escribe resultado en GCS.

    Idempotente: el bucket es la fuente de verdad. Si el `result.json` ya existe,
    sale temprano — re-ejecuciones del Cloud Run Job (retry automático o resume
    manual) no re-procesan CVs ya completos.
    Cualquier excepción se persiste como error JSON y se relanza; el worker
    de arriba la absorbe y sigue con los demás CVs.
    """
    job_prefix = _job_prefix(job_id)
    result_uri = storage.gs_uri(GCS_BUCKET, job_prefix, "results", f"{cv_name}.json")

    if storage.exists(result_uri):
        logger.info("SKIP %s/%s ya procesado (idempotencia)", job_id, cv_name)
        return

    job_json = storage.read_json(storage.gs_uri(GCS_BUCKET, job_prefix, "job.json"))
    job_description = job_json["job_description"]
    requested_tier = job_json.get("model_tier", "cheap")

    cv_uri = storage.gs_uri(GCS_BUCKET, job_prefix, "cvs", cv_name)
    error_uri = storage.gs_uri(GCS_BUCKET, job_prefix, "errors", f"{cv_name}.json")

    try:
        kind = _classify(cv_name)
        if kind == "image" and requested_tier not in VISION_CAPABLE_TIERS:
            logger.info(
                "Tier %s no soporta imágenes — fallback a %s para %s",
                requested_tier, VISION_FALLBACK_TIER, cv_name,
            )
            tier = VISION_FALLBACK_TIER
        else:
            tier = requested_tier

        system_msg = build_system_message(job_description)
        if kind == "text":
            user_msg = build_text_user_message(cargar_contenido_texto(cv_uri))
        else:
            mime, _ = mimetypes.guess_type(cv_name)
            user_msg = build_image_user_message(storage.download_bytes(cv_uri), mime or "image/png")

        parsed, telemetry = call_llm([system_msg, user_msg], Outputllm, tier)
        score = parsed.score_llm

        result = AnalisisCVOutput(
            output_llm=parsed, nombre_archivo_cv=cv_name, score_final=score
        )
        storage.upload_json(
            result_uri,
            {
                **result.model_dump(),
                "model": telemetry.model,
                "model_tier": tier,
                "input_tokens": telemetry.input_tokens,
                "output_tokens": telemetry.output_tokens,
                "cost_usd": telemetry.cost_usd,
                "latency_s": telemetry.latency_s,
                "processed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        logger.info("OK %s/%s score=%d cost=$%.6f", job_id, cv_name, score, telemetry.cost_usd)

    except Exception as e:
        logger.exception("FAIL %s/%s", job_id, cv_name)
        storage.upload_json(
            error_uri,
            {
                "cv_name": cv_name,
                "error": str(e),
                "traceback": traceback.format_exc(),
                "failed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        raise
