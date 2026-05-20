"""Entrypoint del Cloud Run Job: procesa todos los CVs de un job_id en paralelo.

Se invoca como `python -m src.app.worker`. Lee `JOB_ID` del environment.
Cada CV se procesa con `cv_processor.process_one_cv`, que ya es idempotente vía
`storage.exists(result_uri)` — re-ejecuciones del Job (retry o resume manual)
no re-procesan CVs ya completos.
"""
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

from . import storage
from .cv_processor import process_one_cv
from .config import GCS_BUCKET, MAX_CONCURRENT_LLM, SUPPORTED_FORMATS

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    force=True,
)
logger = logging.getLogger(__name__)


async def _process_one(job_id: str, cv_name: str, sem: asyncio.Semaphore) -> None:
    async with sem:
        try:
            await asyncio.to_thread(process_one_cv, job_id, cv_name)
        except Exception:
            # `process_one_cv` ya persistió el error como errors/*.json antes de relanzar.
            # No tumbamos el Job entero: seguimos con los demás CVs.
            logger.exception("CV falló: %s/%s", job_id, cv_name)


async def run_job(job_id: str) -> None:
    if not GCS_BUCKET:
        raise RuntimeError("GCS_BUCKET no configurado")

    prefix = f"jobs/{job_id}/cvs/"
    cv_names = [
        n
        for n in storage.list_names(GCS_BUCKET, prefix)
        if n.lower().endswith(SUPPORTED_FORMATS)
    ]
    if not cv_names:
        logger.warning("Job %s no tiene CVs en gs://%s/%s — nada que hacer", job_id, GCS_BUCKET, prefix)
        return

    logger.info(
        "Job %s arrancando: %d CVs, concurrencia=%d",
        job_id, len(cv_names), MAX_CONCURRENT_LLM,
    )

    sem = asyncio.Semaphore(MAX_CONCURRENT_LLM)
    await asyncio.gather(*(_process_one(job_id, n, sem) for n in cv_names))

    logger.info("Job %s terminado", job_id)


def main() -> None:
    job_id = os.environ.get("JOB_ID")
    if not job_id:
        logger.error("JOB_ID no está seteado en el environment")
        sys.exit(2)
    asyncio.run(run_job(job_id))


if __name__ == "__main__":
    main()
