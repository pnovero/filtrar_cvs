"""FastAPI: ingestor /jobs y status /jobs/{id}. El trabajo pesado lo hace un Cloud Run Job."""
import logging
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from . import jobs_dispatch, storage
from .config import (
    DEFAULT_MODEL_TIER,
    GCS_BUCKET,
    MODEL_TIERS,
    SUPPORTED_FORMATS,
)
from .pydantic_models import Weights

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="filtrar_cvs API",
    description="Motor de screening de CVs: ingesta + status. Trabajo pesado en Cloud Run Job.",
    version="0.3.0",
)


# ---------- Schemas ----------


class JobRequest(BaseModel):
    job_id: str = Field(..., description="ID único del job (también prefijo en GCS)")
    job_description: str
    weights: Optional[Weights] = None
    model_tier: str = Field(default=DEFAULT_MODEL_TIER)


class JobAccepted(BaseModel):
    job_id: str
    total_cvs: int
    status_url: str


class JobStatus(BaseModel):
    job_id: str
    total: int
    completed: int
    failed: int
    pending: int
    cost_usd: float
    results_uri: str


# ---------- Endpoints ----------


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/jobs", response_model=JobAccepted, status_code=202)
async def create_job(req: JobRequest):
    if not GCS_BUCKET:
        raise HTTPException(500, "GCS_BUCKET no configurado")
    if req.model_tier not in MODEL_TIERS:
        raise HTTPException(400, f"model_tier inválido: {req.model_tier}")

    weights_dict = (req.weights or Weights()).to_dict()
    total_w = sum(weights_dict.values())
    if abs(total_w - 1.0) > 0.01:
        raise HTTPException(400, f"weights deben sumar 1.0, suman {total_w}")

    prefix = f"jobs/{req.job_id}"
    cv_names = [
        n
        for n in storage.list_names(GCS_BUCKET, f"{prefix}/cvs/")
        if n.lower().endswith(SUPPORTED_FORMATS)
    ]
    if not cv_names:
        raise HTTPException(
            400, f"No se encontraron CVs soportados en gs://{GCS_BUCKET}/{prefix}/cvs/"
        )

    storage.upload_json(
        storage.gs_uri(GCS_BUCKET, prefix, "job.json"),
        {
            "job_id": req.job_id,
            "job_description": req.job_description,
            "weights": weights_dict,
            "model_tier": req.model_tier,
            "total_cvs": len(cv_names),
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    await jobs_dispatch.run_job_execution(req.job_id)
    logger.info("job %s aceptado: %d CVs", req.job_id, len(cv_names))

    return JobAccepted(
        job_id=req.job_id,
        total_cvs=len(cv_names),
        status_url=f"/jobs/{req.job_id}",
    )


@app.get("/jobs/{job_id}", response_model=JobStatus)
async def get_job_status(job_id: str):
    if not GCS_BUCKET:
        raise HTTPException(500, "GCS_BUCKET no configurado")

    prefix = f"jobs/{job_id}"
    cvs = storage.list_names(GCS_BUCKET, f"{prefix}/cvs/")
    results = storage.list_names(GCS_BUCKET, f"{prefix}/results/")
    errors = storage.list_names(GCS_BUCKET, f"{prefix}/errors/")

    total = len(cvs)
    completed = len(results)
    failed = len(errors)
    pending = max(0, total - completed - failed)

    cost = 0.0
    for r in results:
        try:
            data = storage.read_json(storage.gs_uri(GCS_BUCKET, prefix, "results", r))
            cost += float(data.get("cost_usd") or 0.0)
        except Exception:
            logger.warning("No pude leer cost_usd de %s/%s", prefix, r)

    return JobStatus(
        job_id=job_id,
        total=total,
        completed=completed,
        failed=failed,
        pending=pending,
        cost_usd=round(cost, 6),
        results_uri=f"gs://{GCS_BUCKET}/{prefix}/results/",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
