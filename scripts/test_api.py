"""Prueba end-to-end contra la API.

Sube `cvs/` local al bucket bajo `jobs/{job_id}/cvs/`, llama POST /jobs,
hace polling hasta pending=0, descarga results/* y muestra resumen.

Funciona contra Cloud Run o contra `uvicorn` local con LOCAL_MODE=1
(en cuyo caso el "bucket" es un directorio en disco).
"""
import json
import os
import sys
import time
import uuid
from pathlib import Path

import requests

API_URL = os.getenv("API_URL", "http://localhost:8000")
BUCKET = os.environ["GCS_BUCKET"]
LOCAL_MODE = os.getenv("LOCAL_MODE", "0") == "1"
LOCAL_BUCKET_DIR = Path(os.getenv("LOCAL_BUCKET_DIR", "./.local_bucket"))

CVS_DIR = Path("cvs")
JD_FILE = Path("job_description.txt")
SUPPORTED = (".pdf", ".docx", ".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".webp", ".txt", ".md")


def _upload_local(src: Path, bucket: str, blob_path: str) -> None:
    dst = LOCAL_BUCKET_DIR / bucket / blob_path
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())


def _upload_gcs(src: Path, bucket: str, blob_path: str) -> None:
    from google.cloud import storage as gcs

    gcs.Client().bucket(bucket).blob(blob_path).upload_from_filename(str(src))


def upload(src: Path, bucket: str, blob_path: str) -> None:
    (_upload_local if LOCAL_MODE else _upload_gcs)(src, bucket, blob_path)


def download_results(bucket: str, prefix: str) -> list[dict]:
    if LOCAL_MODE:
        rdir = LOCAL_BUCKET_DIR / bucket / prefix
        return [json.loads(p.read_text("utf-8")) for p in sorted(rdir.glob("*.json"))]
    from google.cloud import storage as gcs

    client = gcs.Client()
    return [
        json.loads(b.download_as_text())
        for b in sorted(client.list_blobs(bucket, prefix=prefix), key=lambda b: b.name)
    ]


def main():
    job_id = sys.argv[1] if len(sys.argv) > 1 else f"test-{uuid.uuid4().hex[:8]}"
    print(f"job_id={job_id}  bucket={BUCKET}  api={API_URL}  local_mode={LOCAL_MODE}")

    cvs = [p for p in CVS_DIR.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED]
    if not cvs:
        sys.exit(f"No hay CVs en {CVS_DIR}")

    print(f"Subiendo {len(cvs)} CVs...")
    for cv in cvs:
        upload(cv, BUCKET, f"jobs/{job_id}/cvs/{cv.name}")

    jd = JD_FILE.read_text(encoding="utf-8")
    r = requests.post(
        f"{API_URL}/jobs",
        json={"job_id": job_id, "job_description": jd},
        timeout=60,
    )
    r.raise_for_status()
    print("Aceptado:", r.json())

    while True:
        s = requests.get(f"{API_URL}/jobs/{job_id}", timeout=30).json()
        print(
            f"  total={s['total']} done={s['completed']} fail={s['failed']} "
            f"pending={s['pending']} cost=${s['cost_usd']:.6f}"
        )
        if s["pending"] == 0:
            break
        time.sleep(3)

    results = download_results(BUCKET, f"jobs/{job_id}/results/")
    print(f"\n{len(results)} resultados:")
    for r in sorted(results, key=lambda x: -x["score_final"]):
        c = r["output_llm"]["datos_contacto"]
        print(f"  {r['score_final']:3d}  {c.get('nombre','?'):40s}  {r['nombre_archivo_cv']}")


if __name__ == "__main__":
    main()
