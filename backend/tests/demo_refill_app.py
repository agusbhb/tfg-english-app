"""Aplicación exclusiva de demostración aislada. Nunca importada por app.main."""

import asyncio
import os
from contextlib import asynccontextmanager

from bson import ObjectId

from app.config import Settings
from app.generation import GenerationResponse
from app.main import create_app
from app.models import AIModel
from app.seed import seed_database
from scripts.activate_generation_prompt import activate


class DemoProvider:
    model = AIModel(proveedor="simulado", modelo="demo-sin-red")

    def __init__(self, delay=0):
        self.delay = delay
        self.requests = []
        self.gate = None

    async def generate(self, request):
        self.requests.append(request)
        if self.gate is not None:
            await self.gate.wait()
        await asyncio.sleep(self.delay)
        questions = []
        for mode, count in request.parameters.distribucion.items():
            for index in range(count):
                # Marcador deliberadamente sintético para distinguir la demo del contenido real.
                prefix = f"Demo {request.generacion_id}-{request.ronda}-{index}: "
                if mode == "afirmativa":
                    sentence, accepted, distractors = (
                        prefix + "My brother ___ (read) every evening.",
                        ["reads"],
                        ["read", "reading"],
                    )
                elif mode == "negativa":
                    sentence, accepted, distractors = (
                        prefix + "My brother ___ (not like) fish.",
                        ["doesn't like", "does not like"],
                        ["don't like", "doesn't likes"],
                    )
                else:
                    # El contrato exige el auxiliar en el hueco inicial o tras partícula A1.
                    sentence, accepted, distractors = (
                        f"Where ___ your friends from demo {request.generacion_id}-{index} work?",
                        ["do"],
                        ["does", "are"],
                    )
                questions.append(
                    {
                        "modalidad": mode,
                        "enunciado": sentence,
                        "respuesta_principal": accepted[0],
                        "solucion": {
                            "respuestas_aceptadas": accepted,
                            "sensible_mayusculas": False,
                        },
                        "distractores": distractors,
                    }
                )
        return GenerationResponse(payload={"preguntas": questions})


async def prepare_demo(database):
    """Idempotente: treinta iniciales, sin reiniciar datos de una demo ya usada."""
    if database.name != "trainer_demo_refill":
        raise ValueError("La preparación demo requiere su base exclusiva")
    await seed_database(database)
    await activate(database, 4, True)
    bank = await database.Bancos.find_one()
    template = await database.BancoEjercicios.find_one({"banco_id": bank["_id"], "secuencia": 1})
    for sequence in range(11, 31):
        question = {
            **template,
            "_id": ObjectId(),
            "secuencia": sequence,
            "enunciado": f"Demo inicial {sequence}: My sister ___ (read) every day.",
            "respuesta_principal": "reads",
            "solucion": {"respuestas_aceptadas": ["reads"], "sensible_mayusculas": False},
            "distractores": ["read", "reading"],
            "modalidad": "afirmativa",
        }
        await database.BancoEjercicios.update_one(
            {"banco_id": bank["_id"], "secuencia": sequence},
            {"$setOnInsert": question},
            upsert=True,
        )
    await database.Bancos.update_one({"_id": bank["_id"]}, {"$max": {"publicado_hasta": 30}})


def create_demo_app():
    settings = Settings(_env_file=None)
    if (
        os.environ.get("TRAINER_REFILL_DEMO") != "1"
        or settings.mongodb_db != "trainer_demo_refill"
        or not settings.bank_refill_enabled
        or settings.anthropic_api_key is not None
    ):
        raise ValueError("Demo bloqueada: requiere base exclusiva, flag y ausencia de claves LLM")
    provider = DemoProvider(delay=90)
    app = create_app(settings, generation_provider=provider)
    production_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with production_lifespan(application):
            await application.state.refiller.stop()
            await prepare_demo(application.state.database)
            application.state.refiller.start()
            yield

    app.router.lifespan_context = lifespan
    return app
