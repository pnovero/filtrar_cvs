# Comandos de operación. `make help` para ver el listado.
# Override de defaults: `make deploy PROJECT_ID=foo BUCKET=bar`

PROJECT_ID    ?= $(shell gcloud config get-value project 2>/dev/null)
SERVICE_NAME  ?= cv-filter-api
JOB_NAME      ?= cv-filter-batch
REGION        ?= us-central1
BUCKET        ?= filtrar-cvs
CONCURRENCY   ?= 10
WORKER_SA      = $(SERVICE_NAME)-worker@$(PROJECT_ID).iam.gserviceaccount.com
IMAGE          = gcr.io/$(PROJECT_ID)/$(SERVICE_NAME):latest

# Env vars de runtime para Service y Job. OPENAI/GEMINI/ANTHROPIC sólo si están seteadas en el shell.
RUNTIME_ENV    = GCS_BUCKET=$(BUCKET),GCP_PROJECT=$(PROJECT_ID),JOB_NAME=$(JOB_NAME),JOB_REGION=$(REGION)
LLM_ENV        = OPENAI_API_KEY=$$OPENAI_API_KEY,GEMINI_API_KEY=$$GEMINI_API_KEY,ANTHROPIC_API_KEY=$$ANTHROPIC_API_KEY

.PHONY: help install lock dev test build deploy deploy-service deploy-job logs logs-job clean

help:  ## Muestra este listado
	@awk 'BEGIN{FS=":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install:  ## Instala dependencias con uv
	uv sync

lock:  ## Regenera uv.lock desde pyproject.toml
	uv lock

dev:  ## Levanta la API local en LOCAL_MODE (sin GCP)
	LOCAL_MODE=1 GCS_BUCKET=test uvicorn src.app.main:app --reload

test:  ## End-to-end: sube cvs/, llama POST /jobs, polling, descarga results
	python scripts/test_api.py

build:  ## Build & push de la imagen a GCR via Cloud Build (compartida entre service y job)
	@test -n "$(PROJECT_ID)" || (echo "PROJECT_ID requerido"; exit 1)
	gcloud builds submit --tag $(IMAGE) --project $(PROJECT_ID)

deploy: build deploy-service deploy-job  ## Pipeline completo: bucket + SA + IAM + Service + Job

deploy-service:  ## Deploy del Cloud Run Service (ingesta + status)
	@test -n "$(PROJECT_ID)" || (echo "PROJECT_ID requerido"; exit 1)
	# Bucket (idempotente)
	@gcloud storage buckets describe gs://$(BUCKET) --project $(PROJECT_ID) >/dev/null 2>&1 \
	  || gcloud storage buckets create gs://$(BUCKET) --location=$(REGION) --project $(PROJECT_ID)
	# Service account compartida (runtime de Service y Job)
	@gcloud iam service-accounts describe $(WORKER_SA) --project $(PROJECT_ID) >/dev/null 2>&1 \
	  || gcloud iam service-accounts create $(SERVICE_NAME)-worker --project $(PROJECT_ID)
	# IAM: bucket read/write
	gcloud storage buckets add-iam-policy-binding gs://$(BUCKET) \
	  --member="serviceAccount:$(WORKER_SA)" --role="roles/storage.objectAdmin"
	# IAM: el Service tiene que poder disparar executions del Job
	gcloud projects add-iam-policy-binding $(PROJECT_ID) \
	  --member="serviceAccount:$(WORKER_SA)" --role="roles/run.developer" \
	  --condition=None
	# Cloud Run Service (corre como WORKER_SA, request-response chiquito)
	gcloud run deploy $(SERVICE_NAME) \
	  --image $(IMAGE) --region $(REGION) --project $(PROJECT_ID) \
	  --service-account $(WORKER_SA) \
	  --no-allow-unauthenticated --memory 512Mi --cpu 1 --timeout 60 \
	  --concurrency $(CONCURRENCY) \
	  --set-env-vars "$(RUNTIME_ENV)" \
	  --set-env-vars "$(LLM_ENV)"
	@URL=$$(gcloud run services describe $(SERVICE_NAME) --region $(REGION) --project $(PROJECT_ID) --format='value(status.url)') && \
	  echo "Service deployed: $$URL"

deploy-job:  ## Deploy del Cloud Run Job (procesamiento batch por job_id)
	@test -n "$(PROJECT_ID)" || (echo "PROJECT_ID requerido"; exit 1)
	# El Job comparte la misma imagen pero override del entrypoint al worker.
	# `gcloud run jobs deploy` es create-or-update idempotente.
	gcloud run jobs deploy $(JOB_NAME) \
	  --image $(IMAGE) --region $(REGION) --project $(PROJECT_ID) \
	  --service-account $(WORKER_SA) \
	  --command python --args "-m,src.app.worker" \
	  --memory 1Gi --cpu 1 --task-timeout 3600 --max-retries 3 \
	  --set-env-vars "$(RUNTIME_ENV)" \
	  --set-env-vars "$(LLM_ENV)"
	@echo "Job deployed: projects/$(PROJECT_ID)/locations/$(REGION)/jobs/$(JOB_NAME)"

logs:  ## Últimos 100 logs del Service
	gcloud run services logs read $(SERVICE_NAME) --region $(REGION) --project $(PROJECT_ID) --limit 100

logs-job:  ## Últimos 100 logs del Job (último execution)
	gcloud run jobs executions list --job $(JOB_NAME) --region $(REGION) --project $(PROJECT_ID) --limit 1 --format='value(name)' | \
	  xargs -I{} gcloud run jobs executions logs read {} --region $(REGION) --project $(PROJECT_ID) --limit 100

clean:  ## Borra artefactos locales
	rm -rf .local_bucket
	find . -type d -name __pycache__ -exec rm -rf {} +
