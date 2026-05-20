"""Wrapper sobre GCS con fallback a filesystem cuando LOCAL_MODE=1.

Las URIs siempre tienen forma `gs://bucket/path/objeto`. En modo local se traducen
a `{LOCAL_BUCKET_DIR}/bucket/path/objeto` para poder probar el flujo end-to-end
sin GCP.
"""
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterable

from .config import LOCAL_BUCKET_DIR, LOCAL_MODE

if not LOCAL_MODE:
    from google.cloud import storage as gcs_client

    _client = gcs_client.Client()
else:
    _client = None


def _parse_gs_uri(gs_uri: str) -> tuple[str, str]:
    if not gs_uri.startswith("gs://"):
        raise ValueError(f"URI inválida (esperaba gs://...): {gs_uri}")
    rest = gs_uri[len("gs://"):]
    bucket, _, blob = rest.partition("/")
    if not bucket or not blob:
        raise ValueError(f"URI gs:// incompleta: {gs_uri}")
    return bucket, blob


def _local_path(bucket: str, blob: str) -> Path:
    return Path(LOCAL_BUCKET_DIR) / bucket / blob


def download_to_temp(gs_uri: str) -> Path:
    """Descarga el objeto a un archivo temporal y devuelve la ruta. El caller debe borrarlo."""
    bucket, blob = _parse_gs_uri(gs_uri)
    suffix = os.path.splitext(blob)[1]
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)

    if LOCAL_MODE:
        shutil.copy(_local_path(bucket, blob), tmp_path)
    else:
        _client.bucket(bucket).blob(blob).download_to_filename(tmp_path)

    return Path(tmp_path)


def download_bytes(gs_uri: str) -> bytes:
    bucket, blob = _parse_gs_uri(gs_uri)
    if LOCAL_MODE:
        return _local_path(bucket, blob).read_bytes()
    return _client.bucket(bucket).blob(blob).download_as_bytes()


def upload_json(gs_uri: str, obj: dict) -> None:
    bucket, blob = _parse_gs_uri(gs_uri)
    payload = json.dumps(obj, ensure_ascii=False, indent=2)
    if LOCAL_MODE:
        path = _local_path(bucket, blob)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
        return
    _client.bucket(bucket).blob(blob).upload_from_string(
        payload, content_type="application/json"
    )


def read_json(gs_uri: str) -> dict:
    bucket, blob = _parse_gs_uri(gs_uri)
    if LOCAL_MODE:
        return json.loads(_local_path(bucket, blob).read_text(encoding="utf-8"))
    raw = _client.bucket(bucket).blob(blob).download_as_text()
    return json.loads(raw)


def exists(gs_uri: str) -> bool:
    bucket, blob = _parse_gs_uri(gs_uri)
    if LOCAL_MODE:
        return _local_path(bucket, blob).exists()
    return _client.bucket(bucket).blob(blob).exists()


def list_names(bucket: str, prefix: str) -> list[str]:
    """Lista los nombres (basename) de los objetos bajo `prefix`. No incluye sub-prefijos."""
    if LOCAL_MODE:
        base = Path(LOCAL_BUCKET_DIR) / bucket / prefix
        if not base.exists():
            return []
        return sorted(p.name for p in base.iterdir() if p.is_file())

    blobs: Iterable = _client.list_blobs(bucket, prefix=prefix)
    names = []
    for b in blobs:
        rel = b.name[len(prefix):] if b.name.startswith(prefix) else b.name
        # Saltar "subdirectorios" (objetos que tienen / en el resto del path)
        if "/" in rel.strip("/"):
            continue
        if rel:
            names.append(rel.lstrip("/"))
    return sorted(names)


def gs_uri(bucket: str, *parts: str) -> str:
    return "gs://" + bucket + "/" + "/".join(p.strip("/") for p in parts)
