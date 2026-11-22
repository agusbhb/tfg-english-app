"""Activación manual atómica; inserta solo la plantilla ausente y conserva versiones."""

import argparse
import asyncio

from pymongo.errors import PyMongoError

from app.bank_contracts import ContentKey
from app.config import Settings
from app.content import load_educational_sheet
from app.database import create_client
from app.models import PromptDocument
from app.prompts import validate_generation_template
from app.seed import initial_generation_prompt


async def activate(database, version, update_text=False):
    if update_text and version != 4:
        raise ValueError("Solo se permite actualizar explícitamente el borrador v4")
    template = initial_generation_prompt(version).model_copy(update={"activo": False})

    async def transaction(session):
        key = {
            "asignatura_id": template.asignatura_id,
            "nombre": template.nombre,
            "tipo": template.tipo,
            "version": version,
        }
        await database.Prompts.update_one(
            key, {"$setOnInsert": template.model_dump(by_alias=True)}, upsert=True, session=session
        )
        if update_text:
            await database.Prompts.update_one(
                key,
                {
                    "$set": {
                        "contenido_prompt": template.contenido_prompt,
                        "variables_requeridas": template.variables_requeridas,
                    }
                },
                session=session,
            )
        target = await database.Prompts.find_one(key, session=session)
        validate_generation_template(PromptDocument.model_validate(target))
        await database.Prompts.update_many(
            {"asignatura_id": template.asignatura_id, "tipo": "generacion", "activo": True},
            {"$set": {"activo": False}},
            session=session,
        )
        await database.Prompts.update_one(
            {"_id": target["_id"]}, {"$set": {"activo": True}}, session=session
        )
        sheet = load_educational_sheet(
            ContentKey(
                codigo_tema="present-simple", nivel_mcer="A1", version=2 if version >= 4 else 1
            )
        )
        await database.Bancos.update_one(
            {"codigo_tema": "present-simple", "nivel_mcer_objetivo": "A1"},
            {"$set": {"ficha": sheet.referencia.model_dump()}},
            session=session,
        )

    async with database.client.start_session() as session:
        await session.with_transaction(transaction)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", type=int, choices=(1, 2, 3, 4), required=True)
    parser.add_argument(
        "--actualizar-texto",
        action="store_true",
        help="Actualizar explícitamente el texto del borrador v4 desde el seed",
    )
    args = parser.parse_args()

    async def run():
        settings = Settings()
        if settings.bank_refill_enabled:
            raise ValueError("La reposición debe estar apagada")
        client = create_client(settings)
        try:
            await activate(client[settings.mongodb_db], args.version, args.actualizar_texto)
        finally:
            await client.close()

    try:
        asyncio.run(run())
    except (ValueError, PyMongoError):
        raise SystemExit(
            "No se pudo activar: revisa plantilla, replica set y BANK_REFILL_ENABLED=false"
        ) from None
    print(f"generacion_banco v{args.version} activa; versiones anteriores conservadas.")


if __name__ == "__main__":
    main()
