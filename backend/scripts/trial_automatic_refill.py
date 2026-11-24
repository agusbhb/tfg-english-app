"""Ensayo HTTP autenticado de un disparo automático, solo en MongoDB de pruebas."""

import argparse
import asyncio
import json
from urllib.parse import parse_qs, urlsplit

import httpx
from bson import ObjectId
from pymongo.errors import PyMongoError

from app.anthropic import AnthropicError
from app.bank_contracts import BankQuestionDocument, modality_counts, modality_distribution
from app.config import Settings
from app.database import create_client
from app.generation import sentence_key
from app.main import create_app
from app.seed import seed_database
from scripts.activate_generation_prompt import activate


class TrialError(ValueError):
    """Fallo controlado del ensayo, sin cuerpos HTTP ni credenciales."""


def check_isolation(settings):
    uri = urlsplit(settings.mongodb_uri.get_secret_value())
    if (
        settings.mongodb_db != "trainer_refill_trial"
        or uri.hostname != "mongodb-test"
        or uri.username != "trainer_test"
        or parse_qs(uri.query).get("replicaSet") != ["trainer-test"]
    ):
        raise TrialError("Ensayo bloqueado: requiere la base y el MongoDB aislados de pruebas")
    if (
        not settings.bank_refill_enabled
        or settings.bank_refill_threshold != 20
        or settings.bank_refill_batch_size != 30
        or settings.bank_refill_max_attempts != 1
        or settings.bank_generation_max_rounds > 3
    ):
        raise TrialError(
            "Ensayo bloqueado: requiere umbral 20, lote 30, un intento y hasta 3 rondas"
        )


async def run_trial(settings, *, authorized=False, provider=None):
    if not authorized:
        raise TrialError("Se requiere autorización explícita; no se ha iniciado el ensayo")
    check_isolation(settings)
    client = create_client(settings)
    try:
        if await client[settings.mongodb_db].list_collection_names():
            raise TrialError(
                "La base del ensayo ya contiene colecciones; no se modifica ni reinicia"
            )
    finally:
        await client.close()

    # La factoría normal elige Anthropic. Inyección exclusivamente para pruebas simuladas.
    app = create_app(settings, generation_provider=provider)
    async with app.router.lifespan_context(app):
        db, worker = app.state.database, app.state.refiller
        # Base nueva, sin trabajos. Preparar y verificar los dos disparos antes de procesarlos.
        await worker.stop()
        await seed_database(db)
        await activate(db, 4, True)
        bank = await db.Bancos.find_one()
        template = await db.BancoEjercicios.find_one({"secuencia": 1})
        for sequence in range(11, 31):
            await db.BancoEjercicios.insert_one(
                {
                    **template,
                    "_id": ObjectId(),
                    "secuencia": sequence,
                    "enunciado": f"Trial initial {sequence}: My sister ___ (read) every day.",
                    "respuesta_principal": "reads",
                    "solucion": {"respuestas_aceptadas": ["reads"], "sensible_mayusculas": False},
                    "distractores": ["read", "reading"],
                }
            )
        await db.Bancos.update_one({"_id": bank["_id"]}, {"$set": {"publicado_hasta": 30}})
        if (await db.Bancos.find_one())["reposicion"] is not None:
            raise ValueError("No debe existir tarea antes de reservar")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://isolated-trial") as api:
            subject = (await api.get("/asignaturas")).json()[0]
            for number in range(2):
                registration = {
                    "nombre": f"Alumno ficticio {number}",
                    "email": f"trial{number}@example.com",
                    "password": "Isolated trial password 123!",
                    "asignatura_id": subject["id"],
                    "nivel_id": subject["niveles"][0]["nivel_id"],
                }
                response = await api.post("/auth/register", json=registration)
                if response.status_code != 201:
                    raise ValueError("Falló el registro del alumno ficticio")
                token = await api.post(
                    "/auth/login", json={k: registration[k] for k in ("email", "password")}
                )
                response = await api.post(
                    "/tests",
                    json={"tema_id": str(bank["tema_id"])},
                    headers={"Authorization": "Bearer " + token.json()["access_token"]},
                )
                if response.status_code != 201:
                    raise ValueError("Falló la reserva mediante POST /tests")
                job = (await db.Bancos.find_one())["reposicion"]
                if number == 0:
                    generation_id = job["generacion_id"]
                if job["generacion_id"] != generation_id or job["estado"] != "pendiente":
                    raise ValueError("El segundo alumno no conservó la única tarea pendiente")

        before = await db.Progreso.find().sort("_id", 1).to_list()
        old_questions = await db.BancoEjercicios.find().sort("secuencia", 1).to_list()
        old_tests = await db.Tests.find().sort("_id", 1).to_list()
        if [p["ultima_secuencia_reservada"] for p in before] != [10, 10]:
            raise ValueError("Los alumnos no terminaron con cursor 10 y 20 preguntas restantes")
        worker.start()  # Bucle real: claim, lease, rondas y publicación transaccional.
        try:
            async with asyncio.timeout(settings.bank_refill_timeout_seconds + 15):
                while True:
                    final = await db.Bancos.find_one()
                    if final["reposicion"]["estado"] in ("completada", "error"):
                        break
                    await asyncio.sleep(0.02)
        finally:
            await worker.stop()
        new = (
            await db.BancoEjercicios.find({"secuencia": {"$gt": 30}}).sort("secuencia", 1).to_list()
        )
        after = await db.Progreso.find().sort("_id", 1).to_list()
        unchanged = (
            after == before
            and await db.Tests.find().sort("_id", 1).to_list() == old_tests
            and await db.BancoEjercicios.find({"secuencia": {"$lte": 30}})
            .sort("secuencia", 1)
            .to_list()
            == old_questions
        )
        if (
            not unchanged
            or final["reposicion"]["generacion_id"] != generation_id
            or final["reposicion"]["intentos"] != 1
            or final["reposicion"]["rondas_consumidas"] > settings.bank_generation_max_rounds
        ):
            raise ValueError("No se conservaron cursores, snapshots o la tarea original")
        if final["reposicion"]["estado"] == "completada":
            counts = modality_counts([BankQuestionDocument.model_validate(q) for q in new])
            if (
                len(new) != 30
                or final["publicado_hasta"] != 60
                or [q["secuencia"] for q in new] != list(range(31, 61))
                or counts != modality_distribution(30, "present-simple", "A1")
                or len({sentence_key(q["enunciado"]) for q in old_questions + new}) != 60
            ):
                raise ValueError("La publicación no cumple cantidad, secuencias, cuotas o unicidad")
        elif new or final["publicado_hasta"] != 30:
            raise ValueError("Se detectó publicación parcial tras el fallo")
        metrics = final.get("ultima_generacion") or {}
        return {
            "estado": final["reposicion"]["estado"],
            "base": settings.mongodb_db,
            "publicado_antes": 30,
            "restantes_al_disparar": 20,
            "publicadas_nuevas": len(new),
            "cursores_antes": [p["ultima_secuencia_reservada"] for p in before],
            "cursores_conservados": unchanged,
            "intentos_worker": final["reposicion"]["intentos"],
            "publicado_despues": final["publicado_hasta"],
            "cursores_despues": [p["ultima_secuencia_reservada"] for p in after],
            "misma_tarea_dos_alumnos": True,
            "snapshots_y_publicadas_conservados": unchanged,
            "rondas_consumidas": final["reposicion"]["rondas_consumidas"],
            "error_codigo": final["reposicion"]["error_codigo"],
            "modelo": metrics.get("modelo"),
            "prompt_version": metrics.get("prompt_version"),
            "tokens_entrada": metrics.get("tokens_entrada"),
            "tokens_salida": metrics.get("tokens_salida"),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--autorizar-anthropic", action="store_true")
    args = parser.parse_args()
    if not args.autorizar_anthropic:
        raise SystemExit(
            "Preparado, sin ejecutar: falta --autorizar-anthropic. No se ha llamado al LLM."
        )
    try:
        report = asyncio.run(run_trial(Settings(_env_file=None), authorized=True))
    except TrialError as error:
        raise SystemExit(str(error)) from None
    except (ValueError, AnthropicError, PyMongoError, TimeoutError, RuntimeError):
        raise SystemExit(
            "Ensayo detenido: revisa aislamiento, credencial, estado y límites. "
            "Sin reintento automático del ensayo."
        ) from None
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["estado"] != "completada":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
