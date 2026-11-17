from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import CollectionInvalid

from app.bank_contracts import BankProgressDocument
from app.config import Settings

COLLECTIONS = (
    "Usuarios",
    "Asignaturas",
    "Prompts",
    "Bancos",
    "BancoEjercicios",
    "Tests",
    "Progreso",
)


def create_client(settings: Settings) -> AsyncMongoClient:
    return AsyncMongoClient(
        settings.mongodb_uri.get_secret_value(),
        tz_aware=True,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        timeoutMS=10000,
    )


async def initialize_database(database: AsyncDatabase) -> None:
    # Evitar modificar una BD anterior: el cambio de formato requiere una base nueva.
    if (
        await database.BancoEjercicios.find_one({"ejemplos_resueltos": {"$exists": True}})
        or "tema_unique" in await database.BancoEjercicios.index_information()
    ):
        raise ValueError(
            "Banco antiguo: utiliza una base nueva o una copia con migración explícita"
        )
    existing = await database.list_collection_names()
    for name in COLLECTIONS:
        if name not in existing:
            try:
                await database.create_collection(name)
            except CollectionInvalid:
                pass  # Otro arranque concurrente puede crearla primero.
    await database.Usuarios.create_index("email", unique=True, name="email_unique")
    await database.Asignaturas.create_index("codigo", unique=True, name="codigo_unique")
    await database.Prompts.create_index(
        [
            ("asignatura_id", ASCENDING),
            ("nombre", ASCENDING),
            ("tipo", ASCENDING),
            ("version", ASCENDING),
        ],
        unique=True,
        name="prompt_version_unique",
    )
    await database.Bancos.create_index(
        [("tema_id", ASCENDING), ("nivel_mcer_objetivo", ASCENDING)],
        unique=True,
        name="tema_mcer_unique",
    )
    await database.BancoEjercicios.create_index(
        [("banco_id", ASCENDING), ("secuencia", ASCENDING)],
        unique=True,
        name="banco_secuencia_unique",
    )
    await database.Tests.create_index(
        [("usuario_id", ASCENDING), ("fechas.generacion", DESCENDING)], name="historial_usuario"
    )
    await database.Progreso.create_index(
        [("usuario_id", ASCENDING), ("banco_id", ASCENDING)],
        unique=True,
        name="usuario_banco_unique",
    )
    # Capturar también los snapshots 2B antes del saneamiento de pendientes.
    await initialize_progress(database)
    await ensure_pending_index(database)


async def initialize_progress(database: AsyncDatabase) -> None:
    """Inicializa solo claves ausentes a partir de snapshots 2B conservados."""
    groups = await database.Tests.aggregate(
        [
            {"$unwind": "$origenes_preguntas"},
            {"$match": {"origenes_preguntas.pregunta_banco_id": {"$exists": True}}},
            {
                "$group": {
                    "_id": {"usuario": "$usuario_id", "banco": "$origenes_preguntas.banco_id"},
                    "ultima": {"$max": "$origenes_preguntas.secuencia"},
                }
            },
        ]
    )
    async for group in groups:
        progress = BankProgressDocument(
            usuario_id=group["_id"]["usuario"],
            banco_id=group["_id"]["banco"],
            ultima_secuencia_reservada=group["ultima"],
        )
        await database.Progreso.update_one(
            {"usuario_id": progress.usuario_id, "banco_id": progress.banco_id},
            {"$setOnInsert": progress.model_dump(by_alias=True)},
            upsert=True,
        )


async def ensure_pending_index(database: AsyncDatabase) -> None:
    """Conserva el pendiente más antiguo; elimina solo duplicados aún pendientes."""
    groups = database.Tests.aggregate(
        [
            {"$match": {"estado": "pendiente"}},
            {"$sort": {"fechas.generacion": 1, "_id": 1}},
            {
                "$group": {
                    "_id": {"usuario": "$usuario_id", "tema": "$tema_id"},
                    "ids": {"$push": "$_id"},
                    "count": {"$sum": 1},
                }
            },
            {"$match": {"count": {"$gt": 1}}},
        ]
    )
    async for group in await groups:
        await database.Tests.delete_many({"_id": {"$in": group["ids"][1:]}, "estado": "pendiente"})
    # Si aparecen duplicados durante la migración, el índice falla y el arranque
    # no acepta peticiones sin la garantía de unicidad.
    await database.Tests.create_index(
        [("usuario_id", ASCENDING), ("tema_id", ASCENDING)],
        unique=True,
        partialFilterExpression={"estado": "pendiente"},
        name="pending_user_topic_unique",
    )
