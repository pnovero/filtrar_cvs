"""Extracción de texto de archivos CV (PDF, DOCX, TXT, MD)."""
import os

from docx import Document
from pypdf import PdfReader

from . import storage


def cargar_contenido_texto(path_or_uri: str) -> str:
    """Extrae texto de PDF/DOCX/TXT/MD. Acepta paths locales o `gs://...`.

    Las imágenes NO pasan por esta función — el worker las pasa como bytes al LLM
    multimodal directamente.
    """
    if path_or_uri.startswith("gs://"):
        local_path = storage.download_to_temp(path_or_uri)
        try:
            return _extract_text(str(local_path))
        finally:
            try:
                os.unlink(local_path)
            except OSError:
                pass
    return _extract_text(path_or_uri)


def _extract_text(file_path: str) -> str:
    ext = file_path.lower()

    if ext.endswith(".pdf"):
        reader = PdfReader(file_path)
        chunks = []
        for page in reader.pages:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n".join(chunks)

    if ext.endswith(".docx"):
        doc = Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    if ext.endswith((".txt", ".md")):
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    raise ValueError(f"Formato de texto no soportado: {file_path}")
