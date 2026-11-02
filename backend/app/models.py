"""Modelos privados de MongoDB y validaciones reutilizables de actividades."""

import re
from datetime import UTC, datetime
from typing import Annotated, Literal, Self

from bson import ObjectId
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    PlainSerializer,
    WithJsonSchema,
    model_validator,
)


def object_id(value: object) -> ObjectId:
    if isinstance(value, ObjectId):
        return value
    if isinstance(value, str) and ObjectId.is_valid(value):
        return ObjectId(value)
    raise ValueError("El identificador debe tener 24 caracteres hexadecimales")


MongoId = Annotated[
    ObjectId,
    BeforeValidator(object_id),
    PlainSerializer(str, return_type=str, when_used="json"),
    WithJsonSchema({"type": "string", "pattern": "^[0-9a-fA-F]{24}$"}),
]


def utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("La fecha debe incluir zona horaria")
    return value.astimezone(UTC)


UTCDate = Annotated[datetime, AfterValidator(utc_datetime)]
MCER = Literal["A1", "A2", "B1", "B2", "C1", "C2"]
Objective = Literal["gramatica", "vocabulario", "comprension_lectora"]
Presentation = Literal["frases_independientes", "texto_compartido"]
LocalId = Annotated[str, Field(min_length=1, max_length=64)]
GapId = Annotated[str, Field(pattern=r"^h[1-9][0-9]*$")]
NonEmpty = Annotated[str, Field(min_length=1, max_length=10000)]


class Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=True,
        populate_by_name=True,
        hide_input_in_errors=True,
    )


class Document(Model):
    id: MongoId = Field(default_factory=ObjectId, alias="_id")


class AcademicProfile(Model):
    asignatura_id: MongoId
    nivel_id: MongoId


class UserDocument(Document):
    email: EmailStr
    nombre: str | None = None
    password_hash: str
    perfil_academico: AcademicProfile
    nivel_mcer: MCER | None = None
    creado_en: UTCDate
    actualizado_en: UTCDate


class Topic(Model):
    tema_id: MongoId
    nombre: NonEmpty
    descripcion: str | None = None


class Level(Model):
    nivel_id: MongoId
    nombre_curso: NonEmpty
    temas: list[Topic] = Field(default_factory=list, max_length=100)


class SubjectDocument(Document):
    codigo: str = Field(min_length=1, max_length=64)
    nombre: NonEmpty
    niveles: list[Level] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_embedded_ids(self) -> Self:
        validate_catalog([self])
        return self


def validate_catalog(subjects: list[SubjectDocument]) -> None:
    """También detecta colisiones entre asignaturas y entre niveles y temas."""
    ids: set[ObjectId] = set()
    for subject in subjects:
        values = [subject.id]
        for level in subject.niveles:
            values.append(level.nivel_id)
            values.extend(topic.tema_id for topic in level.temas)
        for value in values:
            if value in ids:
                raise ValueError("Los IDs del catálogo deben ser únicos globalmente")
            ids.add(value)


def validate_academic_reference(
    subject: SubjectDocument, level_id: ObjectId, topic_id: ObjectId | None = None
) -> None:
    for level in subject.niveles:
        if level.nivel_id == level_id:
            if topic_id is None or any(topic.tema_id == topic_id for topic in level.temas):
                return
    raise ValueError("El nivel y el tema deben pertenecer a la asignatura indicada")


class PromptDocument(Document):
    asignatura_id: MongoId
    nombre: NonEmpty
    tipo: Literal["generacion", "evaluacion"]
    version: int = Field(gt=0, strict=True)
    variables_requeridas: list[str] = Field(default_factory=list)
    activo: bool = False
    contenido_prompt: NonEmpty
    creado_en: UTCDate


class BaseText(Model):
    contenido: NonEmpty
    origen: Literal["manual", "generado"]

    @model_validator(mode="after")
    def meaningful_content(self) -> Self:
        if not self.contenido.strip():
            raise ValueError("El texto compartido no puede estar vacío")
        return self


class Option(Model):
    opcion_id: LocalId
    texto: NonEmpty


class ChoiceSolution(Model):
    opcion_id: LocalId


class BlankSolution(Model):
    respuestas_aceptadas: list[NonEmpty] = Field(min_length=1, max_length=20)
    sensible_mayusculas: bool

    @model_validator(mode="after")
    def meaningful_answers(self) -> Self:
        if any(not answer.strip() for answer in self.respuestas_aceptadas):
            raise ValueError("Las respuestas aceptadas no pueden estar vacías")
        return self


class ChoiceQuestion(Model):
    pregunta_id: LocalId
    formato: Literal["multiple_choice"]
    enunciado: NonEmpty
    opciones: list[Option] = Field(min_length=2, max_length=8)
    hueco_id: GapId | None = None
    solucion: ChoiceSolution

    @model_validator(mode="after")
    def coherent_options(self) -> Self:
        ids = [option.opcion_id for option in self.opciones]
        if len(ids) != len(set(ids)) or self.solucion.opcion_id not in ids:
            raise ValueError("Las opciones deben ser únicas e incluir la solución")
        return self


class BlankQuestion(Model):
    pregunta_id: LocalId
    formato: Literal["fill_blanks"]
    enunciado: NonEmpty
    opciones: None = None
    hueco_id: GapId | None = None
    solucion: BlankSolution


Question = Annotated[ChoiceQuestion | BlankQuestion, Field(discriminator="formato")]


def validate_activity(
    presentation: Presentation, text: BaseText | None, questions: list[Question]
) -> None:
    ids = [question.pregunta_id for question in questions]
    if len(ids) != len(set(ids)):
        raise ValueError("Los IDs de pregunta deben ser únicos")
    gaps = [question.hueco_id for question in questions if question.hueco_id is not None]
    if len(gaps) != len(set(gaps)):
        raise ValueError("Los IDs de hueco deben ser únicos")
    if presentation == "frases_independientes":
        if text is not None or gaps:
            raise ValueError("Las frases independientes no tienen texto compartido ni hueco_id")
        return
    if text is None:
        raise ValueError("La presentación compartida requiere texto_base")
    markers = re.findall(r"\[(h[1-9][0-9]*)\]", text.contenido)
    if len(markers) != len(set(markers)) or set(markers) != set(gaps):
        raise ValueError("Los marcadores del texto deben coincidir exactamente con los huecos")
    if any(
        question.formato == "fill_blanks" and question.hueco_id is None for question in questions
    ):
        raise ValueError("Cada pregunta de huecos en un texto compartido necesita hueco_id")


class SolvedExample(Model):
    ejemplo_id: MongoId
    objetivo: Objective
    presentacion: Presentation
    texto_base: BaseText | None = None
    preguntas: list[Question] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def coherent_activity(self) -> Self:
        validate_activity(self.presentacion, self.texto_base, self.preguntas)
        return self


class ExerciseBankDocument(Document):
    asignatura_id: MongoId
    nivel_id: MongoId
    tema_id: MongoId
    ejemplos_resueltos: list[SolvedExample] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_examples(self) -> Self:
        ids = [example.ejemplo_id for example in self.ejemplos_resueltos]
        if len(ids) != len(set(ids)):
            raise ValueError("Los IDs de ejemplo deben ser únicos")
        return self


class StudentAnswer(Model):
    pregunta_id: LocalId
    respuesta_dada: str = Field(max_length=10000)
    es_correcta: bool
    feedback: str | None = None


class Statistics(Model):
    aciertos: int = Field(ge=0, strict=True)
    total_preguntas: int = Field(gt=0, strict=True)

    @model_validator(mode="after")
    def valid_score(self) -> Self:
        if self.aciertos > self.total_preguntas:
            raise ValueError("Los aciertos no pueden superar el total")
        return self


class TestDates(Model):
    generacion: UTCDate
    resolucion: UTCDate | None = None


class AIModel(Model):
    proveedor: NonEmpty
    modelo: NonEmpty


class TestDocument(Document):
    usuario_id: MongoId
    asignatura_id: MongoId
    nivel_id: MongoId
    tema_id: MongoId
    objetivo: Objective
    presentacion: Presentation
    nivel_mcer: MCER | None = None
    texto_base: BaseText | None = None
    # Null permite la próxima entrega de tests fijos sin intervención de IA.
    prompt_generacion_id: MongoId | None = None
    prompt_generacion_version: int | None = Field(default=None, gt=0, strict=True)
    prompt_evaluacion_id: MongoId | None = None
    prompt_evaluacion_version: int | None = Field(default=None, gt=0, strict=True)
    modelo_generacion: AIModel | None = None
    modelo_evaluacion: AIModel | None = None
    preguntas_generadas: list[Question] = Field(min_length=1, max_length=50)
    respuestas_alumno: list[StudentAnswer] = Field(default_factory=list, max_length=50)
    feedback_ia: str | None = None
    etiquetas_error: list[str] = Field(default_factory=list)
    estadisticas: Statistics | None = None
    estado: Literal["pendiente", "en_evaluacion", "completado", "error"] = "pendiente"
    fechas: TestDates

    @model_validator(mode="after")
    def coherent_test(self) -> Self:
        validate_activity(self.presentacion, self.texto_base, self.preguntas_generadas)
        for kind in ("generacion", "evaluacion"):
            reference = getattr(self, f"prompt_{kind}_id")
            version = getattr(self, f"prompt_{kind}_version")
            if (reference is None) != (version is None):
                raise ValueError("La referencia al prompt requiere su versión y viceversa")
        question_ids = {question.pregunta_id for question in self.preguntas_generadas}
        answer_ids = [answer.pregunta_id for answer in self.respuestas_alumno]
        if len(answer_ids) != len(set(answer_ids)) or not set(answer_ids) <= question_ids:
            raise ValueError("Las respuestas deben referirse a preguntas existentes sin duplicados")
        if self.estado == "completado":
            if self.estadisticas is None or self.fechas.resolucion is None:
                raise ValueError("Un test completado requiere estadísticas y fecha de resolución")
            if (
                set(answer_ids) != question_ids
                or self.estadisticas.total_preguntas != len(question_ids)
                or self.estadisticas.aciertos != sum(a.es_correcta for a in self.respuestas_alumno)
            ):
                raise ValueError("Las estadísticas deben coincidir con las respuestas del test")
        elif self.estadisticas is not None or self.fechas.resolucion is not None:
            raise ValueError("Un test sin completar no tiene estadísticas ni fecha de resolución")
        if self.fechas.resolucion and self.fechas.resolucion < self.fechas.generacion:
            raise ValueError("La resolución no puede ser anterior a la generación")
        return self
