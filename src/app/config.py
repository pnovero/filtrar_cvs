"""Configuración del motor de CV screening."""
import os

# --- GCP ---
GCS_BUCKET = os.getenv("GCS_BUCKET", "")
GCP_PROJECT = os.getenv("GCP_PROJECT", "")

# Cloud Run Job que procesa un job_id por execution. El service dispara una
# execution con `JOB_ID` override; el worker (src/app/worker.py) lo lee del env.
JOB_NAME = os.getenv("JOB_NAME", "cv-filter-batch")
JOB_REGION = os.getenv("JOB_REGION", "us-central1")

# Modo local: storage usa filesystem, el dispatcher invoca el worker en proceso (sin GCP)
LOCAL_MODE = os.getenv("LOCAL_MODE", "0") == "1"
LOCAL_BUCKET_DIR = os.getenv("LOCAL_BUCKET_DIR", "./.local_bucket")

# --- LLM ---
# Cada tier mapea a una **cadena de modelos** (primary + fallbacks) en formato LiteLLM.
# El primero es el modelo principal; el resto se usan automáticamente si el primary
# tira RateLimitError o errores transitorios (vía `fallbacks=` de LiteLLM).
# Invariante: si el primary soporta input multimodal (imágenes), todos los fallbacks
# de ese tier también deben soportarlo — el check de VISION_CAPABLE_TIERS es por tier.
MODEL_TIERS: dict[str, list[str]] = {
    "cheap":    ["gemini/gemini-2.5-flash-lite", "anthropic/claude-haiku-4-5"],
    "balanced": ["openai/gpt-5-nano",            "gemini/gemini-2.5-flash-lite"],
    "accurate": ["anthropic/claude-haiku-4-5",   "openai/gpt-5-nano"],
}
VISION_CAPABLE_TIERS = {"cheap", "balanced", "accurate"}
VISION_FALLBACK_TIER = "cheap"
DEFAULT_MODEL_TIER = os.getenv("DEFAULT_MODEL_TIER", "cheap")

# Concurrencia de llamadas LLM dentro de UN Job execution (asyncio.Semaphore).
MAX_CONCURRENT_LLM = int(os.getenv("MAX_CONCURRENT_LLM", "20"))

# --- Formatos soportados ---
TEXT_FORMATS = (".pdf", ".docx", ".txt", ".md")
IMAGE_FORMATS = (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp")
SUPPORTED_FORMATS = TEXT_FORMATS + IMAGE_FORMATS

# Truncado del texto del CV antes de enviarlo al LLM
MAX_CV_CHARS = int(os.getenv("MAX_CV_CHARS", "30000"))
