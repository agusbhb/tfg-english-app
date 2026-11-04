from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import CollectionInvalid

from app.config import Settings

COLLECTIONS = ("Usuarios", "Asignaturas", "Prompts", "BancoEjercicios", "Tests")


def create_client(settings: Settings) -> AsyncMongoClient:
    return AsyncMongoClient(
        settings.mongodb_uri.get_secret_value(),
        tz_aware=True,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=5000,
        timeoutMS=10000,
    )


async def initialize_database(database: AsyncDatabase) -> None:
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
    await database.BancoEjercicios.create_index("tema_id", unique=True, name="tema_unique")
    await database.Tests.create_index(
        [("usuario_id", ASCENDING), ("fechas.generacion", DESCENDING)], name="historial_usuario"
    )
    await ensure_pending_index(database)


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
