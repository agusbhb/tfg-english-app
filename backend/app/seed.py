"""Catálogo inicial. Inserta lo que falta; no actualiza contenido existente."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from app.bank_contracts import BankMetadataDocument, BankQuestionDocument, ContentKey
from app.config import Settings
from app.content import load_educational_sheet
from app.database import create_client, initialize_database
from app.models import (
    PromptDocument,
    SubjectDocument,
    validate_academic_reference,
    validate_catalog,
)
from app.prompts import (
    GENERATION_PROMPT_NAME,
    GENERATION_VARIABLES_BY_VERSION,
    validate_generation_template,
)

SUBJECT_ID = ObjectId("700000000000000000000001")
LEVEL_ID = ObjectId("700000000000000000000002")
TOPIC_ID = ObjectId("700000000000000000000003")
BANK_ID = ObjectId("700000000000000000000004")
SEED_DATE = datetime(2026, 10, 7, tzinfo=UTC)

# La pista entre paréntesis y el sujeto delimitan la forma esperada.
SENTENCES = [
    ("I ___ (play) football every Saturday.", "play"),
    ("She ___ (watch) TV after dinner every day.", "watches"),
    ("He ___ (go) to school by bus every morning.", "goes"),
    ("They ___ (study) English on Mondays.", "study"),
    ("My brother ___ (have) breakfast at seven every day.", "has"),
    ("We ___ (live) in Bilbao.", "live"),
    ("The shop ___ (close) at six every evening.", "closes"),
    ("You ___ (speak) English very well.", "speak"),
    ("Our teacher ___ (teach) English on Fridays.", "teaches"),
    ("The train ___ (leave) at eight every morning.", "leaves"),
]


def initial_generation_prompt(version: int = 2) -> PromptDocument:
    # Fuente exclusiva del seed. La lógica de negocio solo lee Prompts de MongoDB.
    metadata = {
        1: ("720000000000000000000003", datetime(2026, 11, 5, tzinfo=UTC)),
        2: ("720000000000000000000004", datetime(2026, 11, 14, tzinfo=UTC)),
    }
    identifier, created = metadata[version]
    source = (
        Path(__file__).resolve().parent.parent
        / f"seed_data/prompts/generacion_banco.v{version}.txt"
    )
    prompt = PromptDocument(
        _id=ObjectId(identifier),
        asignatura_id=SUBJECT_ID,
        nombre=GENERATION_PROMPT_NAME,
        tipo="generacion",
        version=version,
        activo=True,
        variables_requeridas=sorted(GENERATION_VARIABLES_BY_VERSION[version]),
        contenido_prompt=source.read_text(encoding="utf-8"),
        creado_en=created,
    )
    validate_generation_template(prompt)
    return prompt


def initial_documents() -> tuple[
    SubjectDocument, BankMetadataDocument, list[BankQuestionDocument], list[PromptDocument]
]:
    subject = SubjectDocument(
        _id=SUBJECT_ID,
        codigo="ingles",
        nombre="Inglés",
        niveles=[
            {
                "nivel_id": LEVEL_ID,
                "nombre_curso": "1.º ESO",
                "temas": [
                    {
                        "tema_id": TOPIC_ID,
                        "nombre": "Present Simple",
                        "nivel_mcer_objetivo": "A1",
                        "descripcion": "Forma afirmativa del presente simple: hábitos y rutinas.",
                    }
                ],
            }
        ],
    )
    questions = []
    for index, (sentence, answer) in enumerate(SENTENCES, start=1):
        questions.append(
            BankQuestionDocument(
                _id=ObjectId(f"7100000000000000{index:08x}"),
                banco_id=BANK_ID,
                secuencia=index,
                version=1,
                enunciado=sentence,
                respuesta_principal=answer,
                solucion={"respuestas_aceptadas": [answer], "sensible_mayusculas": False},
                origen="manual",
                validacion={"estructural": "validada", "pedagogica": "revisada"},
            )
        )
    bank = BankMetadataDocument(
        _id=BANK_ID,
        tema_id=TOPIC_ID,
        codigo_tema="present-simple",
        nivel_mcer_objetivo="A1",
        publicado_hasta=10,
        ficha=load_educational_sheet(
            ContentKey(codigo_tema="present-simple", nivel_mcer="A1", version=1)
        ).referencia,
    )
    prompts = [
        PromptDocument(
            _id=ObjectId(f"7200000000000000{index:08x}"),
            asignatura_id=SUBJECT_ID,
            nombre=f"borrador_{kind}_frases",
            tipo=kind,
            version=1,
            activo=False,
            variables_requeridas=["asignatura", "curso", "tema"],
            contenido_prompt=(
                f"BORRADOR INACTIVO de {kind}. Asignatura: {{asignatura}}; "
                "curso: {curso}; tema: {tema}. Revisar antes de habilitar llamadas a IA."
            ),
            creado_en=SEED_DATE,
        )
        for index, kind in enumerate(("generacion", "evaluacion"), start=1)
    ]
    prompts.append(initial_generation_prompt())
    validate_academic_reference(subject, LEVEL_ID, bank.tema_id)
    return subject, bank, questions, prompts


async def seed_database(database: AsyncDatabase) -> None:
    subject, bank, questions, prompts = initial_documents()
    current = [
        SubjectDocument.model_validate(document)
        for document in await database.Asignaturas.find().to_list()
    ]
    effective = next((item for item in current if item.codigo == subject.codigo), subject)
    if effective.id != subject.id:
        raise ValueError(
            "El catálogo existente usa otros IDs; necesita una actualización explícita"
        )
    validate_catalog(current if effective in current else [*current, subject])
    validate_academic_reference(effective, LEVEL_ID, bank.tema_id)
    existing_bank = await database.Bancos.find_one(
        {"tema_id": bank.tema_id, "nivel_mcer_objetivo": bank.nivel_mcer_objetivo}
    )
    if existing_bank is not None and existing_bank["_id"] != bank.id:
        raise ValueError("El banco existente usa otro ID; revisa su configuración explícitamente")
    # Toda la validación anterior sucede antes de insertar datos.
    await initialize_database(database)
    await database.Asignaturas.update_one(
        {"codigo": subject.codigo}, {"$setOnInsert": subject.model_dump(by_alias=True)}, upsert=True
    )
    # Publicar el banco después de insertar sus preguntas permite reanudar un seed parcial.
    for question in questions:
        await database.BancoEjercicios.update_one(
            {"banco_id": question.banco_id, "secuencia": question.secuencia},
            {"$setOnInsert": question.model_dump(by_alias=True)},
            upsert=True,
        )
    await database.Bancos.update_one(
        {"tema_id": bank.tema_id, "nivel_mcer_objetivo": bank.nivel_mcer_objetivo},
        {"$setOnInsert": bank.model_dump(by_alias=True)},
        upsert=True,
    )
    for prompt in prompts:
        if prompt.activo:
            existing_active = await database.Prompts.find_one(
                {
                    "asignatura_id": prompt.asignatura_id,
                    "tipo": prompt.tipo,
                    "activo": True,
                    "_id": {"$ne": prompt.id},
                }
            )
            if existing_active:
                # No desactivar ni sustituir una elección previa del administrador.
                prompt = prompt.model_copy(update={"activo": False})
        await database.Prompts.update_one(
            {
                "asignatura_id": prompt.asignatura_id,
                "nombre": prompt.nombre,
                "tipo": prompt.tipo,
                "version": prompt.version,
            },
            {"$setOnInsert": prompt.model_dump(by_alias=True)},
            upsert=True,
        )


async def main() -> None:
    settings = Settings()
    client = create_client(settings)
    try:
        await seed_database(client[settings.mongodb_db])
    except PyMongoError:
        raise SystemExit("No se pudo cargar el seed; revisa MongoDB y la configuración") from None
    finally:
        await client.close()
    print("Seed completado: catálogo y banco inicial disponibles; datos existentes conservados.")


if __name__ == "__main__":
    asyncio.run(main())
