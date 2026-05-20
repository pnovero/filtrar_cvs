"""Dispara un Cloud Run Job execution por job_id.

En PROD: llama a la API de Cloud Run Jobs con `JOB_ID` como env override.
En LOCAL_MODE: spawnea `worker.run_job(job_id)` como background task del event loop,
así `make dev` + `make test` siguen funcionando sin GCP y `POST /jobs` responde 202
al toque (mimicando la semántica fire-and-forget de prod).
"""
import asyncio
import logging

from .config import GCP_PROJECT, JOB_NAME, JOB_REGION, LOCAL_MODE

logger = logging.getLogger(__name__)

_local_tasks: set[asyncio.Task] = set()  # hold refs para que el GC no las mate


async def run_job_execution(job_id: str) -> None:
    if LOCAL_MODE:
        from .worker import run_job

        logger.info("LOCAL_MODE: spawning worker.run_job(%s) en background", job_id)
        task = asyncio.create_task(run_job(job_id))
        _local_tasks.add(task)
        task.add_done_callback(_local_tasks.discard)
        return

    if not GCP_PROJECT:
        raise RuntimeError("GCP_PROJECT no configurado")

    from google.cloud import run_v2

    client = run_v2.JobsClient()
    name = client.job_path(GCP_PROJECT, JOB_REGION, JOB_NAME)
    request = run_v2.RunJobRequest(
        name=name,
        overrides=run_v2.RunJobRequest.Overrides(
            container_overrides=[
                run_v2.RunJobRequest.Overrides.ContainerOverride(
                    env=[run_v2.EnvVar(name="JOB_ID", value=job_id)]
                )
            ]
        ),
    )
    # Fire-and-forget: no esperamos a `operation.result()`. El status del Job
    # se observa listando GCS (resultados/errores) desde GET /jobs/{id}.
    client.run_job(request=request)
    logger.info("Cloud Run Job %s disparado para job_id=%s", name, job_id)
