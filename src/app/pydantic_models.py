from typing import Optional, List, Literal

from pydantic import BaseModel, Field, EmailStr


class Weights(BaseModel):
    """Pesos para el cálculo del score ponderado.
    
    Un weight por cada campo en FlagsEvaluacion. Los valores deben sumar 1.0.
    Todos los campos tienen valores por defecto usando Pydantic defaults.
    """
    experiencia_relevante_weight: float = Field(
        default=0.65,
        description="Peso para experiencia relevante (0.0 a 1.0)",
        ge=0.0,
        le=1.0
    )
    habilidades_tecnicas_weight: float = Field(
        default=0.0,
        description="Peso para habilidades técnicas (0.0 a 1.0)",
        ge=0.0,
        le=1.0
    )
    idiomas_requeridos_weight: float = Field(
        default=0.0,
        description="Peso para idiomas requeridos (0.0 a 1.0)",
        ge=0.0,
        le=1.0
    )
    referencias_weight: float = Field(
        default=0.15,
        description="Peso para referencias (0.0 a 1.0)",
        ge=0.0,
        le=1.0
    )
    disponibilidad_full_time_weight: float = Field(
        default=0.0,
        description="Peso para disponibilidad full time (0.0 a 1.0)",
        ge=0.0,
        le=1.0
    )
    disponibilidad_part_time_weight: float = Field(
        default=0.0,
        description="Peso para disponibilidad part time (0.0 a 1.0)",
        ge=0.0,
        le=1.0
    )
    alta_rotacion_laboral_weight: float = Field(
        default=0.0,
        description="Peso para alta rotación laboral (0.0 a 1.0)",
        ge=0.0,
        le=1.0
    )
    estudios_secundarios_completos_weight: float = Field(
        default=0.0,
        description="Peso para estudios secundarios completos (0.0 a 1.0)",
        ge=0.0,
        le=1.0
    )
    estudios_terciarios_completos_weight: float = Field(
        default=0.0,
        description="Peso para estudios terciarios completos (0.0 a 1.0)",
        ge=0.0,
        le=1.0
    )
    estudios_terciarios_en_curso_weight: float = Field(
        default=0.0,
        description="Peso para estudios terciarios en curso (0.0 a 1.0)",
        ge=0.0,
        le=1.0
    )
    cumple_edad_weight: float = Field(
        default=0.10,
        description="Peso para cumple edad (0.0 a 1.0)",
        ge=0.0,
        le=1.0
    )
    cumple_ubicacion_weight: float = Field(
        default=0.10,
        description="Peso para cumple ubicación (0.0 a 1.0)",
        ge=0.0,
        le=1.0
    )
    
    def to_dict(self) -> dict:
        """Convierte el modelo a diccionario, mapeando nombres con _weight a nombres sin _weight."""
        result = {}
        for field_name, value in self.model_dump().items():
            # Remover _weight del nombre del campo
            if field_name.endswith("_weight"):
                original_name = field_name[:-7]  # Remover "_weight"
                result[original_name] = value
        return result


class Contacto(BaseModel):
    """Estructura para almacenar los datos de contacto."""
    nombre: Optional[str] = Field(default=None, description="Nombre y apellido del candidato. Null si no se puede extraer.")
    email: Optional[EmailStr] = Field(default=None, description="Dirección de correo electrónico principal. Null si no se encuentra.")
    telefono: Optional[str] = Field(default=None, description="Número de teléfono (con código de área/país si está disponible). Null si no se encuentra.")
    ubicacion: Optional[str] = Field(default=None, description="Ubicación geográfica del candidato si está disponible. Null si no se encuentra.")
    links: Optional[List[str]] = Field(default=None, description="Lista de URLs relevantes (LinkedIn, portafolio, GitHub, etc.). Null si no se encuentran.")
    edad: Optional[int] = Field(default=None, description="Edad del candidato si está disponible. Null si no se encuentra.")
    

class FlagsEvaluacion(BaseModel):
    """Flags booleanos que indican si el CV cumple con cada característica evaluada.
    """

    experiencia_relevante: Optional[int] = Field(
        default=None, 
        description="Puntaje de 1 a 5 que evalúa qué tan bien coinciden el tipo y cantidad de experiencia del candidato con la JD. "
                   "Donde 1 es la coincidencia más baja y 5 es la coincidencia más alta.",
        ge=1,
        le=5
    )

    habilidades_tecnicas: Optional[bool] = Field(
        default=None, 
        description="True si el candidato posee las habilidades técnicas requeridas en la JD. "
    )

    idiomas_requeridos: Optional[bool] = Field(
        default=None, 
        description="True si el candidato cumple con los requisitos de idiomas especificados en la JD. "
    )

    referencias: Optional[bool] = Field(
        default=None, 
        description="True si entrega contactos de referencias de experiencias previas."
                    "False en caso contrario. Null si no se menciona.")

    disponibilidad_full_time: Optional[bool] = Field(
        default=None, 
        description="True si el candidato está disponible para trabajar a tiempo completo."
                    "False en caso contrario. Null si no se menciona.")

    disponibilidad_part_time: Optional[bool] = Field(
        default=None,
        description="True si el candidato está disponible para trabajar a tiempo parcial."
                    "False en caso contrario. Null si no se menciona.")

    alta_rotacion_laboral: Optional[bool] = Field(
        default=None,
        description="True si el candidato muestra 2 o más trabajos con duración menor a 9 meses."
                    "False en caso contrario. Null si no se puede determinar.")

    estudios_secundarios_completos: Optional[bool] = Field(
        default=None, 
        description="True si el candidato ha completado estudios secundarios."
                    "False en caso contrario. Null si no se puede determinar.")

    estudios_terciarios_completos: Optional[bool] = Field(
        default=None,
        description="True si el candidato ha completado estudios terciarios o universitarios."
                    "False en caso contrario. Null si no se puede determinar.")

    estudios_terciarios_en_curso: Optional[bool] = Field(
        default=None,
        description="True si el candidato está cursando estudios terciarios o universitarios."
                    "False en caso contrario. Null si no se puede determinar.")

    cumple_edad: Optional[bool] = Field(
        default=None,
        description="True si el candidato cumple con el requisito de edad especificado en la JD."
                    "False en caso contrario. Null si no se puede determinar o no hay requisito de edad.")

    cumple_ubicacion: Optional[bool] = Field(
        default=None,
        description="True si el candidato cumple con el requisito de ubicación especificado en la JD."
                    "False en caso contrario. Null si no se puede determinar o no hay requisito de ubicación.")
    
class Outputllm(BaseModel):
    """Schema de salida para el LLM."""
    #flags_evaluacion: FlagsEvaluacion
    score_llm: int = Field(description="Score de match entre el currículum y la job description.", ge=0, le=100)
    datos_contacto: Contacto

class AnalisisCVOutput(BaseModel):
    """Schema de salida principal que combina todo."""
    output_llm: Outputllm = Field(description="Resultado del análisis del LLM.")
    nombre_archivo_cv: str = Field(description="Nombre original del archivo del CV analizado.")
    score_final: int = Field(description="Puntuación total calculada basada en flags y pesos (0 a 100).", ge=0, le=100)