"""Persistencia del banco individual y contratos de generación y progreso."""

import re
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, Field, StrictBool, model_validator

from app.models import (
    MCER,
    AIModel,
    BankQuestionOrigin,
    BlankSolution,
    ChoiceQuestion,
    Document,
    Model,
    MongoId,
    Question,
    UTCDate,
)

PositiveInt = Annotated[int, Field(gt=0, strict=True)]
Counter = Annotated[int, Field(ge=0, strict=True)]
TopicCode = Annotated[str, Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", max_length=64)]


def meaningful(value: str) -> str:
    if not value.strip():
        raise ValueError("El texto no puede estar vacío")
    return value


AnswerText = Annotated[str, Field(min_length=1, max_length=200), AfterValidator(meaningful)]
Modality = Literal["afirmativa", "negativa", "interrogativa"]
MODALITIES = ("afirmativa", "negativa", "interrogativa")
MODALITY_WEIGHTS = {("present-simple", "A1"): (70, 20, 10)}


def modality_distribution(quantity, codigo_tema, nivel_mcer):
    """Política explícita por tema/nivel; sin proporción universal de respaldo."""
    weights = MODALITY_WEIGHTS.get((codigo_tema, nivel_mcer))
    if weights is None:
        raise ValueError("No hay distribución de modalidades configurada para ese tema y nivel")
    if (
        len(weights) != 3
        or any(type(w) is not int or w < 0 for w in weights)
        or sum(weights) != 100
    ):
        raise ValueError("Los pesos de modalidades deben ser tres enteros no negativos y sumar 100")
    counts = [quantity * weight // 100 for weight in weights]
    order = sorted(range(3), key=lambda i: (-(quantity * weights[i] % 100), i))
    for i in order[: quantity - sum(counts)]:
        counts[i] += 1
    return dict(zip(MODALITIES, counts, strict=True))


def modality_counts(questions):
    return {mode: sum(q.modalidad == mode for q in questions) for mode in MODALITIES}


class ContentKey(Model):
    codigo_tema: TopicCode
    nivel_mcer: MCER
    version: PositiveInt


class ContentReference(ContentKey):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class BankSolution(BlankSolution):
    respuestas_aceptadas: list[AnswerText] = Field(min_length=1, max_length=20)
    sensible_mayusculas: StrictBool


class BaseBankQuestion(Model):
    """Contenido común; sin IDs, secuencias ni formato de presentación del test."""

    enunciado: str = Field(min_length=1, max_length=1000)
    respuesta_principal: AnswerText
    solucion: BankSolution
    distractores: list[AnswerText] = Field(default_factory=list, max_length=2)
    modalidad: Modality | None = None

    @model_validator(mode="after")
    def coherent_question(self) -> Self:
        if re.findall(r"_+", self.enunciado) != ["___"]:
            raise ValueError("El enunciado debe contener exactamente un hueco ___")

        def normalize(value):
            value = value.strip()
            return value if self.solucion.sensible_mayusculas else value.casefold()

        accepted = [normalize(a) for a in self.solucion.respuestas_aceptadas]
        distractors = [normalize(a) for a in self.distractores]
        if normalize(self.respuesta_principal) not in accepted:
            raise ValueError("La respuesta principal debe estar entre las aceptadas")
        if len(accepted) != len(set(accepted)):
            raise ValueError("Las alternativas aceptadas no pueden repetirse")
        if len(distractors) not in (0, 2):
            raise ValueError("Indica cero o dos distractores")
        if len(distractors) != len(set(distractors)) or set(distractors) & set(accepted):
            raise ValueError("Los distractores deben ser distintos y no ser respuestas aceptadas")
        return self


class GeneratedBankQuestion(BaseBankQuestion):
    distractores: list[AnswerText] = Field(min_length=2, max_length=2)


class GenerationOutput(Model):
    """Única salida admitida del LLM; los metadatos los asignará el backend."""

    preguntas: list[GeneratedBankQuestion] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def no_duplicate_questions(self) -> Self:
        sentences = [" ".join(q.enunciado.split()).casefold() for q in self.preguntas]
        if len(sentences) != len(set(sentences)):
            raise ValueError("El lote contiene enunciados repetidos")
        return self


class ModalBankQuestion(GeneratedBankQuestion):
    modalidad: Modality

    @model_validator(mode="after")
    def modality_structure(self):
        if re.search(
            r"\b(?:affirmative|negative|interrogative|afirmativa|negativa|interrogativa)\b"
            r"|[\(\[]\s*(?:question|pregunta)\s*[\)\]]",
            self.enunciado,
            re.IGNORECASE,
        ):
            raise ValueError("El enunciado no debe contener etiquetas de modalidad")
        answers = [a.strip().casefold() for a in self.solucion.respuestas_aceptadas]
        if self.modalidad == "interrogativa":
            if not re.match(
                r"^(?:(?:What|Where|When|How often)\s+)?___(?:\s|$)",
                self.enunciado.lstrip(),
                re.IGNORECASE,
            ) or not self.enunciado.rstrip().endswith("?"):
                raise ValueError(
                    "La interrogativa requiere hueco auxiliar tras partícula A1 opcional y signo ?"
                )
            if any(a not in ("do", "does") for a in answers):
                raise ValueError("La interrogativa admite solo el auxiliar Do/Does")
        elif self.modalidad == "negativa":
            if self.enunciado.rstrip().endswith("?") or any(
                re.fullmatch(r"(?:do not|does not|don't|doesn't) [a-z]+", a) is None
                for a in answers
            ):
                raise ValueError(
                    "La negativa requiere auxiliar negativo y verbo base en la solución"
                )
            for answer in answers:
                expanded = answer.replace("doesn't ", "does not ").replace("don't ", "do not ")
                contracted = expanded.replace("does not ", "doesn't ").replace("do not ", "don't ")
                if expanded not in answers or contracted not in answers:
                    raise ValueError("Declarar la variante contraída y la no contraída")
        elif self.enunciado.rstrip().endswith("?") or any(" " in a or "'" in a for a in answers):
            raise ValueError("La afirmativa requiere una forma verbal simple")
        return self


class ModalGenerationOutput(GenerationOutput):
    preguntas: list[ModalBankQuestion] = Field(min_length=1, max_length=50)


class GenerationReference(Model):
    generacion_id: MongoId
    modelo: AIModel
    prompt_id: MongoId
    prompt_version: PositiveInt
    ficha: ContentReference


class GenerationError(Model):
    codigo: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    mensaje: str = Field(min_length=1, max_length=500)


class GenerationRoundMetrics(Model):
    ronda: PositiveInt
    solicitadas: Counter
    recibidas: Counter | None = None
    duplicadas: Counter = 0
    rechazadas_contrato: Counter = 0
    descartadas_cuota: Counter = 0
    candidatas_acumuladas: Counter = 0
    id_llamada: str | None = Field(default=None, max_length=200)
    stop_reason: str | None = Field(default=None, max_length=200)
    tokens_entrada: Counter | None = None
    tokens_salida: Counter | None = None
    coste_estimado_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    error_codigo: str | None = Field(default=None, max_length=64)


class GenerationMetrics(GenerationReference):
    """Acumulado del trabajo; None significa dato desconocido, nunca coste cero."""

    creado_en: UTCDate
    latencia_ms: Counter
    tokens_entrada: Counter | None = None
    tokens_salida: Counter | None = None
    coste_estimado_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False, strict=True)
    preguntas_solicitadas: Annotated[int, Field(ge=1, le=50, strict=True)]
    preguntas_recibidas: Counter
    preguntas_validas: Counter
    preguntas_rechazadas_contrato: Counter = 0
    preguntas_descartadas_duplicadas: Counter = 0
    preguntas_descartadas_cuota: Counter = 0
    preguntas_recibidas_completas: StrictBool | None = None
    tokens_entrada_conocidos: Counter | None = None
    tokens_salida_conocidos: Counter | None = None
    rondas_consumidas: Counter = 0
    rondas: list[GenerationRoundMetrics] = Field(default_factory=list, max_length=5)
    errores: list[GenerationError] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def coherent_counts(self) -> Self:
        if self.preguntas_validas > self.preguntas_recibidas:
            raise ValueError("Los recuentos de generación son incoherentes")
        return self


class BankRefillJob(Model):
    generacion_id: MongoId
    estado: Literal["pendiente", "en_curso", "error", "completada"] = "pendiente"
    cantidad: Annotated[int, Field(ge=1, le=50, strict=True)]
    intentos: Counter = 0
    rondas_consumidas: Counter = 0
    creado_en: UTCDate
    token: MongoId | None = None
    lease_hasta: UTCDate | None = None
    reintento_en: UTCDate | None = None
    error_codigo: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$", max_length=64)

    @model_validator(mode="after")
    def running_requires_lease(self) -> Self:
        if self.estado == "en_curso" and (self.token is None or self.lease_hasta is None):
            raise ValueError("Una reposición en curso requiere token y vencimiento")
        return self


class BankMetadataDocument(Document):
    tema_id: MongoId
    codigo_tema: TopicCode
    nivel_mcer_objetivo: MCER
    ficha: ContentReference
    publicado_hasta: Counter = 0
    ultima_generacion: GenerationMetrics | None = None
    reposicion: BankRefillJob | None = None

    @model_validator(mode="after")
    def matching_content(self) -> Self:
        if (self.codigo_tema, self.nivel_mcer_objetivo) != (
            self.ficha.codigo_tema,
            self.ficha.nivel_mcer,
        ):
            raise ValueError("La ficha no corresponde al ámbito del banco")
        return self


class AcademicBankBinding(Model):
    """Asignación curricular explícita; no equivale a evaluar el MCER del alumno."""

    asignatura_id: MongoId
    nivel_id: MongoId
    tema_id: MongoId
    banco_id: MongoId


class QuestionValidation(Model):
    estructural: Literal["validada"]
    # Automática no equivale a revisión lingüística humana ni habilita multiple_choice.
    pedagogica: Literal["pendiente", "revisada", "rechazada", "automatica"]
    apta_multiple_choice: StrictBool = False


class BankQuestionDocument(BaseBankQuestion, Document):
    banco_id: MongoId
    secuencia: PositiveInt
    version: PositiveInt
    origen: Literal["manual", "llm"]
    validacion: QuestionValidation
    generacion: GenerationReference | None = None

    @model_validator(mode="after")
    def coherent_provenance(self) -> Self:
        if (self.origen == "llm") != (self.generacion is not None):
            raise ValueError("Solo las preguntas LLM requieren trazabilidad de generación")
        if self.validacion.apta_multiple_choice and (
            len(self.distractores) != 2 or self.validacion.pedagogica != "revisada"
        ):
            raise ValueError("Opción múltiple requiere dos distractores y revisión pedagógica")
        return self


class BankProgressDocument(Document):
    usuario_id: MongoId
    banco_id: MongoId
    ultima_secuencia_reservada: Counter = 0
    version: PositiveInt = 1


class PrivateQuestionSnapshot(Model):
    """Snapshot privado futuro. No forma parte de PublicTest ni de las rutas actuales."""

    origen: BankQuestionOrigin
    pregunta: Question

    @model_validator(mode="after")
    def matching_ids(self) -> Self:
        if not (
            self.pregunta.pregunta_id
            == self.origen.pregunta_id
            == str(self.origen.pregunta_banco_id)
        ):
            raise ValueError("El snapshot debe conservar el identificador original")
        if isinstance(self.pregunta, ChoiceQuestion) and len(self.pregunta.opciones) != 3:
            raise ValueError("El nuevo contrato requiere tres opciones")
        return self
