"""Puerto inyectable. No incluye proveedor real ni implementación simulada."""

from dataclasses import dataclass, field
from typing import Protocol

from pydantic import Field

from app.bank_contracts import Counter, GenerationOutput, GenerationReference
from app.content import load_educational_sheet
from app.models import AIModel, Model, SubjectDocument
from app.prompts import (
    GenerationParameters,
    load_generation_prompt,
    render_generation_prompt,
    validate_generation_output,
)


@dataclass(frozen=True)
class GenerationRequest:
    generacion_id: str
    prompt: str
    parameters: GenerationParameters
    output_schema: dict
    ronda: int = 1


class GenerationResponse(Model):
    payload: dict
    tokens_entrada: Counter | None = None
    tokens_salida: Counter | None = None
    coste_estimado_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False, strict=True)
    id_llamada: str | None = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9._-]+$")
    modelo_utilizado: str | None = Field(default=None, max_length=200)
    proveedor_utilizado: str | None = Field(default=None, max_length=100)
    byok_confirmado: bool | None = None


class GenerationProvider(Protocol):
    model: AIModel

    async def generate(self, request: GenerationRequest) -> GenerationResponse: ...


def sentence_key(text):
    """Mismo enunciado pese a mayúsculas o diferencias de espacios."""
    return " ".join(text.split()).casefold()


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
    database, bank, model, quantity, generation_id, extra_exclusions=()
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
    trace: GenerationReference | None = None
    questions: list = field(default_factory=list)
    rounds: list = field(default_factory=list)
    responses: list = field(default_factory=list)

    @property
    def complete(self):
        return len(self.questions) == self.quantity

    def total(self, name):
        values = [getattr(response, name) for response in self.responses]
        return sum(values) if values and all(value is not None for value in values) else None

    @property
    def received(self):
        return sum(round_["recibidas"] for round_ in self.rounds)


async def collect_generation_batch(
    database, bank, provider, quantity, generation_id, max_rounds=1, before_call=None
):
    """Acumular solo candidatas nuevas. No abre transacciones ni escribe en MongoDB."""
    result = GenerationBatch(quantity)
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
        )
        if result.trace is not None and result.trace != trace:
            raise ValueError("El contexto de generación cambió durante el lote")
        result.trace = trace
        if before_call is not None:
            await before_call()
        response = GenerationResponse.model_validate(
            await provider.generate(
                GenerationRequest(
                    str(generation_id),
                    text,
                    parameters,
                    GenerationOutput.model_json_schema(),
                    ronda=len(result.responses) + 1,
                )
            )
        )
        output = validate_generation_output(response.payload, parameters)
        result.responses.append(response)
        accepted_keys = {sentence_key(q.enunciado) for q in result.questions}
        new = [q for q in output.preguntas if sentence_key(q.enunciado) not in accepted_keys]
        combined = result.questions + new
        previous = len(result.questions)
        result.questions = (
            await filter_bank_duplicates(database, bank.id, GenerationOutput(preguntas=combined))
            if combined
            else []
        )
        result.rounds.append(
            {
                "solicitadas": remaining,
                "recibidas": len(output.preguntas),
                "duplicadas": previous + len(output.preguntas) - len(result.questions),
                "candidatas_acumuladas": len(result.questions),
                "id_llamada": response.id_llamada,
                "tokens_entrada": response.tokens_entrada,
                "tokens_salida": response.tokens_salida,
            }
        )
    return result
