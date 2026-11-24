"""Ensayo controlado: un trabajo de reposición con el worker real, sin bucle continuo."""

import argparse
import asyncio
import json

from pymongo.errors import PyMongoError

from app.anthropic import AnthropicError, AnthropicProvider
from app.bank_contracts import BankMetadataDocument
from app.config import Settings
from app.database import create_client
from app.models import object_id
from app.replenishment import BankRefiller


async def run_once(settings, user_id, execute=False, provider=None):
    if settings.bank_refill_enabled:
        raise ValueError(
            "Mantén BANK_REFILL_ENABLED=false; el ensayo no requiere activar el servidor"
        )
    user_id = object_id(user_id)
    client = create_client(settings)
    try:
        db = client[settings.mongodb_db]
        banks = (
            await db.Bancos.find({"codigo_tema": "present-simple", "nivel_mcer_objetivo": "A1"})
            .limit(2)
            .to_list()
        )
        if len(banks) != 1:
            raise ValueError("Se requiere un único banco Present Simple A1")
        bank = BankMetadataDocument.model_validate(banks[0])
        progress = await db.Progreso.find_one({"usuario_id": user_id, "banco_id": bank.id})
        if progress is None:
            raise ValueError("El alumno debe tener una reserva previa en ese banco")
        cursor = progress["ultima_secuencia_reservada"]
        remaining = bank.publicado_hasta - cursor
        if bank.reposicion is not None and bank.reposicion.estado != "completada":
            raise ValueError(
                "Hay una tarea pendiente/en curso/en error; revisarla antes del ensayo"
            )
        report = {
            "ejecutado": False,
            "cursor": cursor,
            "restantes": remaining,
            "umbral": settings.bank_refill_threshold,
            "publicado_hasta": bank.publicado_hasta,
        }
        if not execute or remaining > settings.bank_refill_threshold:
            return report
        refiller = BankRefiller(db, settings, provider or AnthropicProvider(settings))
        await refiller.request(bank.id, cursor)
        job = await refiller.claim(bank.id)
        if job is None:
            return {**report, "estado": "sin_tarea_reclamada"}
        await refiller.process(job)  # Un trabajo; sin run/start ni reintentos de otros trabajos.
        final = await db.Bancos.find_one({"_id": bank.id})
        report.update(
            ejecutado=True,
            estado=final["reposicion"]["estado"],
            publicado_hasta=final["publicado_hasta"],
            metricas=final.get("ultima_generacion"),
        )
        # La trazabilidad MongoDB se omite del resumen para facilitar salida JSON segura.
        if report["metricas"]:
            report["metricas"] = {
                k: report["metricas"].get(k)
                for k in (
                    "modelo",
                    "prompt_version",
                    "preguntas_solicitadas",
                    "preguntas_recibidas",
                    "preguntas_validas",
                    "preguntas_rechazadas_contrato",
                    "tokens_entrada",
                    "tokens_salida",
                    "latencia_ms",
                    "errores",
                )
            }
        return report
    finally:
        await client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--usuario-id", required=True)
    parser.add_argument(
        "--ejecutar",
        action="store_true",
        help="Autorizar un trabajo real; sin esta opción solo consulta",
    )
    args = parser.parse_args()
    try:
        report = asyncio.run(run_once(Settings(), args.usuario_id, args.ejecutar))
    except AnthropicError as error:
        raise SystemExit(str(error)) from None
    except ValueError as error:
        raise SystemExit(str(error)) from None
    except (PyMongoError, TimeoutError):
        raise SystemExit("No se pudo acceder a MongoDB; revisa la conexión local") from None
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["ejecutado"] and report["estado"] != "completada":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
