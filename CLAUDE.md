# CLAUDE.md — guía operativa del repo `filtrar_cvs`

Documento dirigido a Claude Code (y a humanos que entren al repo) para contextualizar la arquitectura, las convenciones y los "no hacer" del proyecto.

## Qué es esto

`filtrar_cvs` es el **motor de screening de CVs** de un SaaS interno. Se compone de dos deploys en Cloud Run:

1. **Service `cv-filter-api`** (FastAPI request-response): recibe `POST /jobs` desde otro servicio del backend con `{job_id, job_description, weights?, model_tier?}`, escribe `job.json` al bucket y dispara un Cloud Run Job execution. Responde 202 al toque. Expone `GET /jobs/{id}` para status (derivado de listar el bucket).
2. **Job `cv-filter-batch`** (Cloud Run Job): un execution por `job_id`. Lee `JOB_ID` del env, lista los CVs del bucket y los procesa en paralelo (asyncio + Semaphore). Cada CV: extracción texto/imagen → LLM (con fallback chain) → escribe `results/{cv}.json` o `errors/{cv}.json`. Si el execution crashea, Cloud Run reintenta automáticamente.

El consumidor del API es siempre otro servicio del backend, nunca un cliente externo. La autenticación entre servicios es vía SA token de GCP.

## Arquitectura

```
Servicio interno
      │
      │ POST /jobs  {job_id, job_description, weights?, model_tier?}
      ▼
Cloud Run Service `cv-filter-api` (FastAPI, src/app/main.py)
   ├── POST /jobs           valida + escribe job.json + dispara Job execution → 202
   ├── GET  /jobs/{id}      status: lista cvs/, results/, errors/ y suma cost_usd
   └── GET  /healthz
        │
        │ jobs_dispatch.run_job_execution(job_id)
        │   → run_v2.JobsClient().run_job(overrides={env: JOB_ID=<id>})
        ▼
Cloud Run Job `cv-filter-batch` (src/app/worker.py)
   │ async runner: Semaphore(MAX_CONCURRENT_LLM) sobre process_one_cv
   │ retry built-in del execution (max-retries=3)
   │
   └──► GCS gs://$GCS_BUCKET/jobs/{job_id}/{cvs,results,errors,job.json}
```

### Layout del bucket

```
gs://$GCS_BUCKET/jobs/{job_id}/
├── cvs/{filename}              ← subido por el servicio upstream ANTES de POST /jobs
├── job.json                    ← {job_description, weights, model_tier, total_cvs, created_at}
├── results/{filename}.json     ← presencia = éxito; incluye output_llm, score, cost_usd, model, latency_s
└── errors/{filename}.json      ← presencia = fallo; incluye error + traceback
```

El estado del job se deriva listando estos prefijos. No usar Firestore ni ninguna otra base.

## Variables de entorno

| Var | Dónde se setea | Para qué |
|---|---|---|
| `GCS_BUCKET` | Service + Job env | Bucket donde viven los jobs |
| `GCP_PROJECT` | Service env | Project id para construir el job path al disparar el Cloud Run Job |
| `JOB_NAME` | Service env (default `cv-filter-batch`) | Nombre del Cloud Run Job que se dispara desde `/jobs` |
| `JOB_REGION` | Service env (default `us-central1`) | Región del Cloud Run Job |
| `JOB_ID` | Job env (inyectado por el Service al disparar la execution) | Identifica el job_id que el worker debe procesar |
| `MAX_CONCURRENT_LLM` | Job env (default `20`) | Semaphore: llamadas LLM concurrentes dentro de UN execution |
| `DEFAULT_MODEL_TIER` | Service + Job env (default `cheap`) | Tier por defecto si el request no lo pasa |
| `OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY` | Service + Job env | Claves de los providers usados (necesarias para los tiers de fallback también) |
| `LOCAL_MODE=1` | Solo dev | Stubea GCS (filesystem) y dispatch (corre el worker en proceso) |
| `LOCAL_BUCKET_DIR` | Solo dev (default `./.local_bucket`) | Raíz del "bucket" local |
| `MAX_CV_CHARS` | Opcional (default 30000) | Truncado del texto del CV |

## Stack y decisiones clave

- **LLM via LiteLLM**, no LangChain. Provider se elige con `model_tier` → `MODEL_TIERS` en `src/app/config.py`. Cada tier es una **lista** `[primary, ...fallbacks]`. LiteLLM convierte la `Outputllm` Pydantic a JSON Schema y devuelve `cost_usd` por llamada.
- **Fallback chain por tier.** Si el primary tira `RateLimitError` u otro error transitorio, LiteLLM salta automáticamente al siguiente modelo de la cadena (vía `fallbacks=` + `num_retries=2`). `LLMUsage.model` refleja el modelo que efectivamente respondió.
- **Prompt caching habilitado.** El system message lleva `cache_control: ephemeral`. LiteLLM lo traduce: explícito en Anthropic, automático en OpenAI (>1024 tokens), implicit caching en Gemini. Para batches contra una misma JD, el input efectivo por llamada baja drásticamente. El cache es server-side (per API key), funciona igual con un Job de muchos CVs concurrentes.
- **Sin Tesseract.** Imágenes van directo al LLM multimodal (`gemini/gemini-2.5-flash-lite` por defecto). Más barato y más exacto que OCR + LLM textual, y la imagen Docker pesa ~200 MB menos.
- **Vision fallback por tier.** Si un CV imagen llega con un tier que no soporta input multimodal (ver `VISION_CAPABLE_TIERS` en `config.py`), el worker cae automáticamente a `VISION_FALLBACK_TIER` (default `cheap`). **Invariante**: todos los modelos de un tier vision-capable deben soportar multimodal; los fallback chains se arman respetando esto.
- **Idempotencia**. `process_one_cv` chequea `storage.exists(result_uri)` al inicio y sale si ya está procesado. Esto cubre dos escenarios: retry automático del Cloud Run Job execution si crashea, y re-disparo manual del Job para "resume from where it left off".
- **Una execution = un job_id.** El worker procesa todos los CVs del job_id en paralelo dentro de un solo container, con `asyncio.gather` y un Semaphore que limita la concurrencia de llamadas LLM. Errores per-CV se persisten como `errors/*.json` y se absorben (el Job no crashea por un CV malo).
- **Estado en GCS, no en Firestore.** Cada worker escribe su propio objeto → no hay race conditions; el listing de GCS es fuertemente consistente.
- **Auth**: Cloud Run con `--no-allow-unauthenticated`. El servicio upstream invoca al Service con SA token. El Service dispara el Job con su SA (necesita `roles/run.developer`). Service y Job corren como la misma SA dedicada (`$SERVICE_NAME-worker`) con `roles/storage.objectAdmin` sobre el bucket — mínimo privilegio.

## Mapa de archivos

| Archivo | Responsabilidad |
|---|---|
| `src/app/main.py` | Endpoints FastAPI (`/jobs`, `/jobs/{id}`, `/healthz`) — Service |
| `src/app/jobs_dispatch.py` | Dispara Cloud Run Job execution con `JOB_ID` override. En `LOCAL_MODE` invoca el worker en proceso. |
| `src/app/worker.py` | Entrypoint del Job: `run_job(job_id)` async con Semaphore sobre `process_one_cv` |
| `src/app/cv_processor.py` | `process_one_cv(job_id, cv_name)`: orquesta extracción + LLM + escritura. Idempotente. También contiene `calcular_score_ponderado`. |
| `src/app/llm.py` | `call_llm(messages, response_model, model_tier)` con LiteLLM + fallbacks, devuelve `LLMUsage` |
| `src/app/prompt.py` | Builders de mensajes (system + user texto / user imagen multimodal) |
| `src/app/text_extraction.py` | `cargar_contenido_texto`: extrae texto de PDF/DOCX/TXT/MD |
| `src/app/storage.py` | Wrapper GCS (con fallback filesystem en `LOCAL_MODE`) |
| `src/app/pydantic_models.py` | `Outputllm`, `FlagsEvaluacion`, `Weights`, `AnalisisCVOutput`, `Contacto` |
| `src/app/config.py` | Env vars, `MODEL_TIERS` (fallback chains), formatos soportados |
| `Dockerfile` | Imagen Cloud Run (slim, sin Tesseract). Misma imagen para Service y Job — el Job override el `--command` al deployarlo. |
| `Makefile` | `install` · `dev` · `test` · `build` · `deploy` (Service + Job) · `logs` · `logs-job` · `clean` |
| `scripts/test_api.py` | Sube `cvs/` al bucket, llama POST /jobs, polling, descarga results |

## Cómo extender

### Agregar un flag de evaluación

Tocar **un solo archivo**: `src/app/pydantic_models.py`. Agregar el campo a `FlagsEvaluacion` (con `Field(description=...)`) y opcionalmente a `Weights` si tiene que pesar en el score.

- `prompt.py` lee el campo via `obtener_info_flags()` → aparece automáticamente en el system message.
- `cv_processor.calcular_score_ponderado` lo cuenta automáticamente si está en `Weights`.

### Agregar un formato de archivo nuevo

1. `src/app/config.py`: agregar la extensión a `TEXT_FORMATS` o `IMAGE_FORMATS`.
2. Si es de texto: extender `_extract_text` en `src/app/text_extraction.py`.
3. Si es imagen: nada que tocar — `cv_processor.process_one_cv` ya levanta los bytes y se los pasa al LLM multimodal.

### Agregar un model tier

`src/app/config.py::MODEL_TIERS`: agregar entrada con una **lista** `[primary, ...fallbacks]` en formato LiteLLM. Asegurarse de que las API keys correspondientes están en el env de Cloud Run (Service y Job). Si el tier es vision-capable, todos los modelos de la lista deben soportar imágenes.

### Cambiar el peso default del scoring

`src/app/pydantic_models.py::Weights`. Los defaults de Pydantic se usan cuando el request no manda `weights`.

## Dev local

```bash
export GEMINI_API_KEY=...
make dev                          # API local en LOCAL_MODE (sin GCP)
# otra terminal:
make test                         # end-to-end contra http://localhost:8000
```

En `LOCAL_MODE`:
- `storage` lee/escribe en `./.local_bucket/test/...`
- `jobs_dispatch.run_job_execution` invoca `worker.run_job` en el mismo proceso (en lugar de llamar a la API de Cloud Run Jobs). El polling de `/jobs/{id}` ve los resultados aparecer en tiempo real.

## Deploy

```bash
export GEMINI_API_KEY=...         # tier por defecto ("cheap")
# OPENAI_API_KEY o ANTHROPIC_API_KEY solo si vas a usar tiers "balanced"/"accurate"
# (o si querés que el fallback chain pueda saltar a esos providers)
make deploy PROJECT_ID=mi-proyecto
```

`make deploy` ejecuta idempotentemente: build de imagen compartida, creación de bucket + SA del worker, deploy del **Service** (Cloud Run con `--no-allow-unauthenticated`), deploy del **Job** (Cloud Run Job con override `--command python --args -m,src.app.worker`), e IAM bindings (bucket: `roles/storage.objectAdmin`; project: `roles/run.developer` para que el Service pueda disparar el Job).

Variables overridables del Makefile: `PROJECT_ID`, `SERVICE_NAME`, `JOB_NAME`, `REGION`, `BUCKET`.

## No hacer

- **No reintroducir LangChain.** Todo se hace con LiteLLM + Pydantic. LangChain agrega peso y churn de versión.
- **No volver a meter Pub/Sub para fan-out per-CV.** El modelo es 1 `job_id` = 1 Cloud Run Job execution. Si en el futuro hay choques sostenidos de rate limits del provider con muchos jobs simultáneos, primero evaluá throttling de executions desde el Service; el fan-out es un cambio mayor que vale la pena sólo si lo anterior no alcanza.
- **No agregar Firestore** ni otra base. El estado vive en GCS y se reconstruye listando.
- **No volver a meter Tesseract.** Imágenes van al LLM multimodal.
- **No agregar lógica multi-tenant.** El bucket es uno y compartido por diseño — el aislamiento entre clientes lo maneja el servicio upstream que arma los `job_id`.
- **No abrir el endpoint públicamente.** `--no-allow-unauthenticated` es obligatorio. Los callers son SAs internas.
- **No cambiar `process_one_cv` para procesar lotes.** La unidad de procesamiento es el CV individual. Procesar lotes rompe el aislamiento de fallas y la idempotencia per-CV.
- **No leer/escribir el bucket fuera de `src/app/storage.py`.** Si necesitás algo nuevo, agregalo ahí.
- **No hacer que el worker falle por errores de un CV individual.** Los errores per-CV se persisten como `errors/*.json` y se absorben; el Job termina exit 0. Sólo errores de infraestructura (OOM, network) deben tumbar el Job para que Cloud Run reintente.
