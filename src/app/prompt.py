"""Construcción de mensajes para el LLM (formato OpenAI-compat que LiteLLM normaliza)."""
import base64
from typing import Dict, List

from .config import MAX_CV_CHARS
from .pydantic_models import FlagsEvaluacion


def obtener_info_flags() -> List[Dict[str, str]]:
    return [
        {"nombre": name, "descripcion": info.description}
        for name, info in FlagsEvaluacion.model_fields.items()
    ]


def build_system_message(job_description: str) -> dict:
    """System message con `cache_control` para que LiteLLM active prompt caching donde el provider lo soporte.

    Para una misma JD ejecutada contra N CVs, el prompt sistema se cachea y el
    input efectivo por llamada baja drásticamente (Anthropic/Gemini: explícito
    via cache_control; OpenAI: automático sobre prefijos largos).
    """
    content = f"""
Eres un experto en preselección de CV (AI CV Screener).
Tu tarea es analizar objetivamente el Currículum Vitae (CV) contra la Descripción de Puesto (JD)
y devolver los datos de contacto del candidato y un score de match.

<DESCRIPCIÓN DEL PUESTO (JD)>
{job_description}
</DESCRIPCIÓN DEL PUESTO (JD)>

<INSTRUCCIONES>
1- Lee cuidadosamente la Descripción del Puesto (JD).
2- Extrae los datos de contacto del candidato.
3- Asigna un score_llm de 0 a 100 que refleje qué tan bien el perfil del candidato matchea con la JD.
   - 0-20: no cumple los requisitos básicos.
   - 21-50: cumple algunos requisitos pero le faltan aspectos clave.
   - 51-75: buen match general, con algunas brechas menores.
   - 76-100: excelente match, cumple la mayoría o todos los requisitos.
4- Devuelve únicamente el JSON solicitado, sin explicaciones adicionales ni texto fuera del formato.
</INSTRUCCIONES>
""".strip()

    return {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }
        ],
    }


def _truncate(text: str, max_chars: int = MAX_CV_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    keep_start = int(max_chars * 0.6)
    keep_end = max_chars - keep_start
    return text[:keep_start] + "\n\n...[TRUNCADO]...\n\n" + text[-keep_end:]


def build_text_user_message(cv_text: str) -> dict:
    return {"role": "user", "content": _truncate(cv_text)}


def build_image_user_message(image_bytes: bytes, mime: str) -> dict:
    """Mensaje multimodal con la imagen en data URL. LiteLLM lo traduce a cada proveedor."""
    b64 = base64.b64encode(image_bytes).decode("ascii")
    return {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "Analiza este CV (imagen). Extraé datos de contacto y asigná el score de match.",
            },
            {
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            },
        ],
    }
