# filtrar_cvs

Motor de screening de CVs para SaaS interno. FastAPI sobre Cloud Run que procesa CVs en lote desde un bucket de GCS, los analiza con un LLM provider-agnóstico (LiteLLM) y escribe un JSON por CV de vuelta al bucket.

> Detalles operativos en [CLAUDE.md](./CLAUDE.md). Comandos en `make help`.

## Flujo

```
servicio interno ──POST /jobs──► Cloud Run Service ──run_job──► Cloud Run Job
                                                                     │ (1 execution = 1 job_id)
                                                                     │ async parallel sobre N CVs
                                                                     ▼
                                          gs://$GCS_BUCKET/jobs/{id}/results/*.json
```

1. El servicio upstream sube CVs a `gs://$GCS_BUCKET/jobs/{job_id}/cvs/`.
2. Llama `POST /jobs` con `{job_id, job_description, weights?, model_tier?}`.
3. El Service escribe `job.json` y dispara una execution del Cloud Run Job `cv-filter-batch` con `JOB_ID` como env override.
4. El Job lista los CVs del bucket y los procesa en paralelo (asyncio + Semaphore). Cada CV: extrae texto/imagen → llama LiteLLM (con fallback chain por tier) → escribe `results/{cv}.json` o `errors/{cv}.json`.
5. `GET /jobs/{id}` devuelve `{total, completed, failed, pending, cost_usd}` derivado de listar el bucket.

Si el Job crashea, Cloud Run reintenta el execution automáticamente. La idempotencia (`storage.exists(result_uri)` en `process_one_cv`) evita re-procesar CVs ya completos.

## Endpoints

| Método | Path | Descripción |
|---|---|---|
| `GET`  | `/healthz` | Liveness |
| `POST` | `/jobs` | Crea un job. 202 + `{job_id, total_cvs, status_url}` |
| `GET`  | `/jobs/{job_id}` | Estado del job |

## Quick start

```bash
make install           # uv sync
export GEMINI_API_KEY=...
make dev               # API local en LOCAL_MODE (sin GCP)
# en otra terminal:
make test              # end-to-end contra http://localhost:8000
```

En `LOCAL_MODE` el dispatcher invoca al worker en el mismo proceso (sin Cloud Run Jobs API), así podés validar el flujo completo sin GCP.

Deploy a Cloud Run (Service + Job):

```bash
export GEMINI_API_KEY=...
make deploy PROJECT_ID=mi-proyecto
```

`make help` lista todos los targets.

## Estructura

```
src/app/
├── main.py              endpoints FastAPI (/jobs, /jobs/{id}, /healthz)
├── jobs_dispatch.py     dispara Cloud Run Job execution (LOCAL_MODE: en proceso)
├── worker.py            entrypoint del Job: async runner sobre process_one_cv
├── cv_processor.py      process_one_cv: orquesta un CV end-to-end + calcular_score_ponderado
├── llm.py               call_llm: LiteLLM + fallback chain + telemetría
├── prompt.py            builders de mensajes (texto + imagen) + obtener_info_flags
├── text_extraction.py   extracción de texto (PDF, DOCX, TXT, MD)
├── storage.py           wrapper GCS (LOCAL_MODE → filesystem)
├── pydantic_models.py   Outputllm / FlagsEvaluacion / Weights / Contacto
└── config.py            env vars, MODEL_TIERS, formatos
scripts/test_api.py      end-to-end contra la API
Makefile                 install · dev · test · build · deploy · logs · logs-job · clean
Dockerfile               imagen Cloud Run (slim, sin Tesseract). Misma imagen para Service y Job.
```



LEER 

1. pydantic_models.py — es el vocabulario del sistema. Entiende FlagsEvaluacion, Weights, Outputllm y AnalisisCVOutput y el resto se acomoda solo.

2. config.py — dos minutos, pero te da el mapa de las variables de entorno y los MODEL_TIERS que aparecen en todo el resto.

3. cv_processor.py — es el corazón. La función process_one_cv hace todo: lee el job, clasifica el CV, llama al LLM y escribe el resultado. Una vez que la entendés, el sistema está    
entendido en un 70%.

3. cv_processor.py — es el corazón. La función process_one_cv hace todo: lee el job, clasifica el CV, llama al LLM y escribe el resultado. Una vez que la entendés, el sistema está    
entendido en un 70%.

4. prompt.py + llm.py — los dos módulos que cv_processor delega. prompt.py arma los mensajes (vale la pena ver el cache_control). llm.py muestra cómo LiteLLM maneja los fallbacks.    

5. worker.py — muy corto; solo muestra cómo se paraleliza con asyncio.gather + Semaphore.

6. main.py — último, porque es la capa HTTP. Con todo lo anterior claro, los tres endpoints se leen en cinco minutos.

storage.py y jobs_dispatch.py los podés leer en cualquier momento; son independientes y autocontenidos.