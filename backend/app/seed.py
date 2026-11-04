"""Catálogo inicial. Inserta lo que falta; no actualiza contenido existente."""

import asyncio
from datetime import UTC, datetime

from bson import ObjectId
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import PyMongoError

from app.config import Settings
from app.database import create_client, initialize_database
from app.models import (
    ExerciseBankDocument,
    PromptDocument,
    SubjectDocument,
    validate_academic_reference,
    validate_catalog,
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


def initial_documents() -> tuple[SubjectDocument, ExerciseBankDocument, list[PromptDocument]]:
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
                        "descripcion": "Forma afirmativa del presente simple: hábitos y rutinas.",
                    }
                ],
            }
        ],
    )
    examples = []
    for index, (sentence, answer) in enumerate(SENTENCES, start=1):
        examples.append(
            {
                "ejemplo_id": ObjectId(f"7100000000000000{index:08x}"),
                "uso_practica": True,
                "objetivo": "gramatica",
                "presentacion": "frases_independientes",
                "texto_base": None,
                "preguntas": [
                    {
                        "pregunta_id": f"q{index}",
                        "formato": "fill_blanks",
                        "enunciado": sentence,
                        "solucion": {
                            "respuestas_aceptadas": [answer],
                            "sensible_mayusculas": False,
                        },
                    }
                ],
            }
        )
    bank = ExerciseBankDocument(
        _id=BANK_ID,
        asignatura_id=SUBJECT_ID,
        nivel_id=LEVEL_ID,
        tema_id=TOPIC_ID,
        ejemplos_resueltos=examples,
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
    validate_academic_reference(subject, bank.nivel_id, bank.tema_id)
    return subject, bank, prompts


async def seed_database(database: AsyncDatabase) -> None:
    subject, bank, prompts = initial_documents()
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
    validate_academic_reference(effective, bank.nivel_id, bank.tema_id)
    # Toda la validación anterior sucede antes de insertar datos.
    await initialize_database(database)
    await database.Asignaturas.update_one(
        {"codigo": subject.codigo}, {"$setOnInsert": subject.model_dump(by_alias=True)}, upsert=True
    )
    await database.BancoEjercicios.update_one(
        {"tema_id": bank.tema_id}, {"$setOnInsert": bank.model_dump(by_alias=True)}, upsert=True
    )
    # Migración acotada: habilita solo los ejemplos originales sin marca previa,
    # si su contenido sigue coincidiendo. No modifica preguntas ni Tests.
    for example in bank.ejemplos_resueltos:
        await database.BancoEjercicios.update_one(
            {
                "_id": bank.id,
                "ejemplos_resueltos": {
                    "$elemMatch": {
                        "ejemplo_id": example.ejemplo_id,
                        "uso_practica": {"$exists": False},
                        "preguntas": [q.model_dump() for q in example.preguntas],
                    }
                },
            },
            {"$set": {"ejemplos_resueltos.$.uso_practica": True}},
        )
    for prompt in prompts:
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
