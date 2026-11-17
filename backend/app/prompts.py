"""Lectura de Prompts y composición validada. Sin proveedor ni plantilla de respaldo."""

from string import Formatter
from typing import Literal

from pydantic import Field

from app.bank_contracts import ContentKey, GenerationOutput, PositiveInt, TopicCode
from app.content import EducationalSheet
from app.models import MCER, Model, PromptDocument, object_id

GENERATION_PROMPT_NAME = "generacion_banco"
LEGACY_GENERATION_VARIABLES = {"tema", "nivel_mcer", "cantidad", "ficha_educativa"}
GENERATION_VARIABLES = LEGACY_GENERATION_VARIABLES | {"idioma_objetivo"}
GENERATION_VARIABLES_BY_VERSION = {1: LEGACY_GENERATION_VARIABLES, 2: GENERATION_VARIABLES}


class GenerationParameters(Model):
    codigo_tema: TopicCode
    tema: str = Field(min_length=1, max_length=100)
    nivel_mcer: MCER
    cantidad: int = Field(ge=1, le=50, strict=True)
    ficha_version: PositiveInt = 1
    # Contrato interno. Etiqueta de la asignatura existente, resuelta por el backend.
    idioma_objetivo: Literal["Inglés"] = "Inglés"

    def content_key(self) -> ContentKey:
        return ContentKey(
            codigo_tema=self.codigo_tema, nivel_mcer=self.nivel_mcer, version=self.ficha_version
        )


def validate_generation_template(prompt: PromptDocument) -> None:
    if prompt.tipo != "generacion" or prompt.nombre != GENERATION_PROMPT_NAME:
        raise ValueError("La plantilla no pertenece a la familia de generación de banco")
    declared = prompt.variables_requeridas
    expected = GENERATION_VARIABLES_BY_VERSION.get(prompt.version)
    if expected is None:
        raise ValueError("Versión de plantilla de generación no soportada")
    if len(declared) != len(set(declared)) or set(declared) != expected:
        raise ValueError("Las variables declaradas no corresponden al contrato de generación")
    fields = set()
    for _, field, spec, conversion in Formatter().parse(prompt.contenido_prompt):
        if field is None:
            continue
        if field not in expected or spec or conversion:
            raise ValueError("La plantilla contiene una variable o formato no permitido")
        fields.add(field)
    if fields != expected:
        raise ValueError("La plantilla no utiliza todas las variables declaradas")


async def load_generation_prompt(database, subject_id) -> PromptDocument:
    subject_id = object_id(subject_id)
    documents = (
        await database.Prompts.find(
            {"asignatura_id": subject_id, "tipo": "generacion", "activo": True}
        )
        .limit(2)
        .to_list()
    )
    if len(documents) != 1:
        raise ValueError("Debe existir una única plantilla de generación activa por asignatura")
    prompt = PromptDocument.model_validate(documents[0])
    validate_generation_template(prompt)
    return prompt


def render_generation_prompt(
    prompt: PromptDocument, parameters: GenerationParameters, sheet: EducationalSheet
) -> str:
    validate_generation_template(prompt)
    if not prompt.activo:
        raise ValueError("La plantilla de generación no está activa")
    if parameters.content_key().model_dump() != sheet.referencia.model_dump(exclude={"sha256"}):
        raise ValueError("La ficha no corresponde a los parámetros de generación")
    # Una sola interpolación: llaves dentro de la ficha no se interpretan de nuevo.
    return prompt.contenido_prompt.format_map(
        {
            "tema": parameters.tema,
            "nivel_mcer": parameters.nivel_mcer,
            "cantidad": parameters.cantidad,
            "ficha_educativa": sheet.contenido,
            "idioma_objetivo": parameters.idioma_objetivo,
        }
    )


def validate_generation_output(
    payload: object, parameters: GenerationParameters
) -> GenerationOutput:
    output = GenerationOutput.model_validate(payload)
    if len(output.preguntas) != parameters.cantidad:
        raise ValueError("El lote no contiene la cantidad solicitada")
    return output
