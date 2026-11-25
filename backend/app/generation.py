"""Puerto inyectable. No incluye proveedor real ni implementación simulada."""

from collections import deque
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import Field, ValidationError

from app.bank_contracts import (
    MODALITIES,
    Counter,
    GeneratedBankQuestion,
    GenerationOutput,
    GenerationReference,
    ModalBankQuestion,
    modality_counts,
    modality_distribution,
)
from app.content import load_educational_sheet
from app.models import AIModel, Model, SubjectDocument
from app.prompts import (
    GenerationParameters,
    generation_output_schema,
    load_generation_prompt,
    render_generation_prompt,
    validate_generation_envelope,
)


@dataclass(frozen=True)
class GenerationRequest:
    generacion_id: str
    prompt: str
    parameters: GenerationParameters
    output_schema: dict
    ronda: int = 1
    validacion_parcial: bool = False


class GenerationResponse(Model):
    payload: dict
    tokens_entrada: Counter | None = None
    tokens_salida: Counter | None = None
    coste_estimado_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False, strict=True)
    id_llamada: str | None = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9._-]+$")
    modelo_utilizado: str | None = Field(default=None, max_length=200)
    proveedor_utilizado: str | None = Field(default=None, max_length=100)


class GenerationProvider(Protocol):
    model: AIModel

    async def generate(self, request: GenerationRequest) -> GenerationResponse: ...


def sentence_key(text):
    """Mismo enunciado pese a mayúsculas o diferencias de espacios."""
    return " ".join(text.split()).casefold()


def interleave_modalities(questions):
    """Turnos ponderados suaves; conserva objetos y orden dentro de cada modalidad.

    Los pesos son las cuotas del lote ya validado, nunca porcentajes globales.
    En empates se usa MODALITIES; no hay aleatoriedad ni modificación de preguntas.
    """
    queues = {mode: deque(q for q in questions if q.modalidad == mode) for mode in MODALITIES}
    weights = {mode: len(queue) for mode, queue in queues.items()}
    if sum(weights.values()) != len(questions):
        raise ValueError("El intercalado requiere una modalidad válida en cada pregunta")
    scores = dict.fromkeys(MODALITIES, 0)
    ordered = []
    for _ in questions:
        for mode in MODALITIES:
            scores[mode] += weights[mode]
        selected = max((mode for mode in MODALITIES if queues[mode]), key=scores.__getitem__)
        ordered.append(queues[selected].popleft())
        scores[selected] -= len(questions)
    return ordered


def question_contract_errors(error):
    issues = []
    for item in error.errors(include_input=False, include_context=False, include_url=False):
        field = ".".join(map(str, item["loc"]))
        if not field:
            # Los validadores cruzados de Pydantic sitúan el error en la raíz.
            field = next(
                (
                    name
                    for marker, name in (
                        ("distractores", "distractores"),
                        ("hueco", "enunciado"),
                        ("etiquetas", "enunciado"),
                        ("interrogativa", "enunciado"),
                        ("principal", "respuesta_principal"),
                        ("alternativas", "solucion.respuestas_aceptadas"),
                        ("variante", "solucion.respuestas_aceptadas"),
                    )
                    if marker in item["msg"]
                ),
                "$",
            )
        issues.append({"campo": field, "regla": item["type"], "motivo": item["msg"]})
    return issues


async def filter_bank_duplicates(database, bank_id, output, *, session=None):
    """Solo lectura; los distractores no definen la identidad de una pregunta.

    El worker pasa su sesión para filtrar de nuevo al publicar atómicamente.
    El preview solo obtiene candidatas respecto al estado actual del banco.
    """
    existing = await database.BancoEjercicios.find(
        {"banco_id": bank_id},
        {"enunciado": 1},
        session=session,
    ).to_list()
    used = {sentence_key(question["enunciado"]) for question in existing}
    return [
        question for question in output.preguntas if sentence_key(question.enunciado) not in used
    ]


async def prepare_generation_context(
    database, bank, model, quantity, generation_id, extra_exclusions=(), distribution=None
):
    """Solo lectura: mismo contexto para el worker y el ensayo manual sin publicar."""
    raw = await database.Asignaturas.find_one({"niveles.temas.tema_id": bank.tema_id})
    catalog = SubjectDocument.model_validate(raw)
    topic = next(t for level in catalog.niveles for t in level.temas if t.tema_id == bank.tema_id)
    parameters = GenerationParameters(
        codigo_tema=bank.codigo_tema,
        tema=topic.nombre,
        nivel_mcer=bank.nivel_mcer_objetivo,
        cantidad=quantity,
        ficha_version=bank.ficha.version,
    )
    sheet = load_educational_sheet(parameters.content_key())
    if sheet.referencia != bank.ficha:
        raise ValueError("La ficha instalada no coincide con la referencia del banco")
    prompt = await load_generation_prompt(database, catalog.id)
    if prompt.version >= 4:
        if bank.ficha.version != 2:
            raise ValueError("El prompt v4 requiere la ficha educativa v2")
        parameters = parameters.model_copy(
            update={
                "distribucion": distribution
                if distribution is not None
                else modality_distribution(quantity, bank.codigo_tema, bank.nivel_mcer_objetivo)
            }
        )
    existing = (
        await database.BancoEjercicios.find({"banco_id": bank.id}, {"enunciado": 1})
        .sort("secuencia", 1)
        .to_list()
    )
    exclusions = list(dict.fromkeys([q["enunciado"] for q in existing] + list(extra_exclusions)))
    text = render_generation_prompt(prompt, parameters, sheet, exclusions)
    trace = GenerationReference(
        generacion_id=generation_id,
        modelo=model,
        prompt_id=prompt.id,
        prompt_version=prompt.version,
        ficha=sheet.referencia,
    )
    return parameters, text, trace


@dataclass
class GenerationBatch:
    quantity: int
    distribution: dict | None = None
    trace: GenerationReference | None = None
    questions: list = field(default_factory=list)
    rounds: list = field(default_factory=list)
    responses: list = field(default_factory=list)

    @property
    def complete(self):
        return len(self.questions) == self.quantity and (
            self.trace is None
            or self.trace.prompt_version < 4
            or modality_counts(self.questions) == self.distribution
        )

    def total(self, name):
        values = [round_.get(name) for round_ in self.rounds]
        return sum(values) if values and all(value is not None for value in values) else None

    def known_total(self, name):
        values = [r[name] for r in self.rounds if r.get(name) is not None]
        return sum(values) if values else None

    @property
    def received(self):
        return sum(round_["recibidas"] or 0 for round_ in self.rounds)

    @property
    def rejected(self):
        return sum(r["rechazadas_contrato"] for r in self.rounds)

    @property
    def duplicate_count(self):
        return sum(r["duplicadas"] for r in self.rounds)


async def collect_generation_batch(
    database,
    bank,
    provider,
    quantity,
    generation_id,
    max_rounds=1,
    before_call=None,
    progress=None,
    round_offset=0,
):
    """Acumular solo candidatas nuevas. No abre transacciones ni escribe en MongoDB."""
    result = progress if progress is not None else GenerationBatch(quantity)
    for _ in range(max_rounds):
        remaining = quantity - len(result.questions)
        if remaining == 0:
            break
        parameters, text, trace = await prepare_generation_context(
            database,
            bank,
            provider.model,
            remaining,
            generation_id,
            [q.enunciado for q in result.questions],
            {
                mode: target - modality_counts(result.questions)[mode]
                for mode, target in result.distribution.items()
            }
            if result.distribution is not None
            else None,
        )
        if result.trace is None:
            result.distribution = parameters.distribucion
        if result.trace is not None and result.trace != trace:
            raise ValueError("El contexto de generación cambió durante el lote")
        result.trace = trace
        if before_call is not None:
            await before_call()
        round_ = {
            "ronda": round_offset + len(result.rounds) + 1,
            "solicitadas": remaining,
            "distribucion_solicitada": parameters.distribucion,
            "recibidas": None,
            "duplicadas": 0,
            "rechazadas_contrato": 0,
            "rechazos_contrato": [],
            "descartadas_cuota": 0,
            "candidatas_acumuladas": len(result.questions),
            "id_llamada": None,
            "stop_reason": None,
            "tokens_entrada": None,
            "tokens_salida": None,
            "coste_estimado_usd": None,
            "error_codigo": "ronda_interrumpida",
        }
        result.rounds.append(round_)
        try:
            response = GenerationResponse.model_validate(
                await provider.generate(
                    GenerationRequest(
                        str(generation_id),
                        text,
                        parameters,
                        generation_output_schema(parameters),
                        ronda=round_["ronda"],
                        validacion_parcial=True,
                    )
                )
            )
        except Exception as error:
            # Solo metadatos controlados; nunca mensajes, cuerpos ni cabeceras del proveedor.
            diagnostic = getattr(error, "diagnostic", None) or {}
            for key in ("tokens_entrada", "tokens_salida"):
                value = diagnostic.get(key)
                if type(value) is int and value >= 0:
                    round_[key] = value
            received = diagnostic.get("preguntas_recibidas")
            if type(received) is int and received >= 0:
                round_["recibidas"] = received
            for key in ("id_llamada", "stop_reason"):
                if isinstance(diagnostic.get(key), str):
                    round_[key] = diagnostic[key][:200]
            for name in ("tokens_entrada", "tokens_salida", "preguntas_recibidas"):
                value = diagnostic.get(name)
                if type(value) is int and value >= 0:
                    round_["recibidas" if name == "preguntas_recibidas" else name] = value
            for name in ("id_llamada", "stop_reason"):
                if isinstance(diagnostic.get(name), str):
                    round_[name] = diagnostic[name][:200]
            round_["error_codigo"] = "proveedor_error"
            raise
        result.responses.append(response)
        round_.update(
            {
                name: getattr(response, name)
                for name in ("tokens_entrada", "tokens_salida", "coste_estimado_usd", "id_llamada")
            }
        )
        raw_questions = response.payload.get("preguntas")
        if isinstance(raw_questions, list):
            round_["recibidas"] = len(raw_questions)
        payload = validate_generation_envelope(response.payload)
        valid = []
        rejected = []
        question_type = (
            ModalBankQuestion if parameters.distribucion is not None else GeneratedBankQuestion
        )
        for index, raw in enumerate(payload["preguntas"]):
            try:
                valid.append((index, question_type.model_validate(raw)))
            except ValidationError as error:
                rejected.append(
                    {
                        "indice": index,
                        "errores": question_contract_errors(error),
                    }
                )
        accepted_keys = {sentence_key(q.enunciado) for q in result.questions}
        duplicates = 0
        overflow = 0
        # Releer el banco para excluir también publicaciones concurrentes.
        current = await database.BancoEjercicios.find(
            {"banco_id": bank.id}, {"enunciado": 1}
        ).to_list()
        used = accepted_keys | {sentence_key(q["enunciado"]) for q in current}
        combined = list(result.questions)
        for _, question in valid:
            key = sentence_key(question.enunciado)
            if key in used:
                duplicates += 1
                continue
            if len(combined) >= quantity or (
                result.distribution is not None
                and modality_counts(combined)[question.modalidad]
                >= result.distribution[question.modalidad]
            ):
                overflow += 1
                continue
            combined.append(question)
            used.add(key)
        result.questions = (
            await filter_bank_duplicates(database, bank.id, GenerationOutput(preguntas=combined))
            if combined
            else []
        )
        round_.update(
            {
                "solicitadas": remaining,
                "distribucion_solicitada": parameters.distribucion,
                "recibidas": len(payload["preguntas"]),
                "duplicadas": duplicates + len(combined) - len(result.questions),
                "rechazadas_contrato": len(rejected),
                "rechazos_contrato": rejected,
                "descartadas_cuota": overflow,
                "candidatas_acumuladas": len(result.questions),
                "error_codigo": None,
                "id_llamada": response.id_llamada,
                "tokens_entrada": response.tokens_entrada,
                "tokens_salida": response.tokens_salida,
            }
        )
    return result
