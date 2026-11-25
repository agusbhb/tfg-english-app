"""Cola durable en Bancos, lease y publicación atómica. Sin proveedor predeterminado."""

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from time import monotonic

from bson import ObjectId
from pydantic import ValidationError
from pymongo import ReadPreference, ReturnDocument, timeout
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.bank_contracts import (
    BankMetadataDocument,
    BankQuestionDocument,
    BankRefillJob,
    GenerationError,
    GenerationMetrics,
    GenerationOutput,
    GenerationReference,
    GenerationRoundMetrics,
    modality_counts,
    modality_distribution,
)
from app.generation import (
    GenerationBatch,
    collect_generation_batch,
    filter_bank_duplicates,
    interleave_modalities,
    prepare_generation_context,
)
from app.models import AIModel

logger = logging.getLogger(__name__)


def now():
    return datetime.now(UTC)


class NoNewQuestions(ValueError):
    pass


class IncompleteBatch(ValueError):
    pass


class BankRefiller:
    def __init__(self, database, settings, provider=None):
        self.database = database
        self.settings = settings
        self.provider = provider
        self.model = AIModel.model_validate(provider.model) if provider is not None else None
        self.task = None

    def start(self):
        if self.provider is not None:
            self.task = asyncio.create_task(self.run(), name="bank-refill")

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task

    async def request(self, bank_id, cursor):
        if self.provider is None:
            return
        job = BankRefillJob(
            generacion_id=ObjectId(),
            cantidad=self.settings.bank_refill_batch_size,
            creado_en=now(),
        )
        await self.database.Bancos.update_one(
            {
                "_id": bank_id,
                "$expr": {
                    "$lte": [
                        {"$subtract": ["$publicado_hasta", cursor]},
                        self.settings.bank_refill_threshold,
                    ]
                },
                "$or": [
                    {"reposicion": None},
                    {"reposicion.estado": "completada"},
                ],
            },
            {"$set": {"reposicion": job.model_dump()}},
        )

    async def after_reservation(self, test):
        origin = test.origenes_preguntas[-1]
        try:
            await self.request(origin.banco_id, origin.secuencia)
        except PyMongoError:
            # El intento ya está confirmado: no presentarlo como una reserva fallida.
            logger.warning("No se pudo solicitar la reposición tras la reserva")

    async def exhausted_status(self, bank_id, cursor):
        # No interpretar una tarea antigua como trabajo activo si falta el proveedor.
        if self.provider is None:
            if self.settings.anthropic_api_key and not self.settings.bank_refill_enabled:
                return "reposicion_desactivada", (
                    "Has agotado las preguntas publicadas de este banco. "
                    "La reposición automática está desactivada para el ensayo manual."
                )
            return "proveedor_no_configurado", (
                "Has agotado las preguntas publicadas de este banco. "
                "La reposición no está disponible porque no hay un proveedor configurado."
            )
        # Recupera también el caso de una caída entre confirmar la reserva y encolar.
        await self.request(bank_id, cursor)
        document = await self.database.Bancos.find_one({"_id": bank_id})
        bank = BankMetadataDocument.model_validate(document)
        if bank.publicado_hasta > cursor:
            return "preguntas_disponibles", "Ya hay nuevas preguntas. Vuelve a empezar el test."
        if bank.reposicion and bank.reposicion.estado in ("pendiente", "en_curso"):
            return "banco_reponiendo", "Estamos preparando más preguntas para este tema"
        if bank.reposicion and bank.reposicion.estado == "error":
            return "reposicion_fallida", "No se han podido preparar más preguntas para este tema."
        return "banco_agotado", "Has agotado las preguntas publicadas de este banco"

    async def claim(self, bank_id=None):
        instant = now()
        document = await self.database.Bancos.find_one_and_update(
            {
                **({"_id": bank_id} if bank_id is not None else {}),
                "$or": [
                    {"reposicion.estado": "pendiente"},
                    {"reposicion.estado": "en_curso", "reposicion.lease_hasta": {"$lte": instant}},
                    {
                        "reposicion.estado": "error",
                        "reposicion.reintento_en": {"$type": "date", "$lte": instant},
                    },
                ],
            },
            {
                "$set": {
                    "reposicion.estado": "en_curso",
                    "reposicion.token": ObjectId(),
                    "reposicion.lease_hasta": instant
                    + timedelta(seconds=self.settings.bank_refill_lease_seconds),
                    "reposicion.reintento_en": None,
                    "reposicion.error_codigo": None,
                },
                "$inc": {"reposicion.intentos": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        return BankMetadataDocument.model_validate(document) if document else None

    def owned(self, bank):
        return {
            "_id": bank.id,
            "reposicion.generacion_id": bank.reposicion.generacion_id,
            "reposicion.token": bank.reposicion.token,
            "reposicion.estado": "en_curso",
            "reposicion.lease_hasta": {"$gt": now()},
        }

    async def context(self, bank):
        return await prepare_generation_context(
            self.database,
            bank,
            self.model,
            bank.reposicion.cantidad,
            bank.reposicion.generacion_id,
        )

    async def publish(self, bank, output, metrics):
        async def transaction(session):
            raw = await self.database.Bancos.find_one(self.owned(bank), session=session)
            if raw is None:
                return False  # Lease perdido o tarea ya publicada: no escribir nada.
            current = BankMetadataDocument.model_validate(raw)
            if current.ficha != bank.ficha:
                raise ValueError("El contexto del banco ha cambiado")
            accepted = await filter_bank_duplicates(self.database, bank.id, output, session=session)
            if not accepted:
                raise NoNewQuestions("El lote no contiene preguntas nuevas")
            if len(accepted) != bank.reposicion.cantidad:
                raise IncompleteBatch("El lote ha perdido candidatas antes de publicar")
            if metrics.prompt_version >= 4:
                if modality_counts(accepted) != modality_distribution(
                    bank.reposicion.cantidad, bank.codigo_tema, bank.nivel_mcer_objetivo
                ):
                    raise IncompleteBatch("La distribución no coincide al publicar")
                # Solo el lote nuevo, antes de asignar secuencias y dentro de la transacción.
                accepted = interleave_modalities(accepted)
            errors = (
                list(metrics.errores)
                if len(accepted) == len(output.preguntas)
                else [
                    GenerationError(
                        codigo="duplicadas_banco",
                        mensaje="Se excluyeron preguntas ya existentes",
                    )
                ]
            )
            final_metrics = metrics.model_copy(
                update={"preguntas_validas": len(accepted), "errores": errors}
            )
            trace = GenerationReference.model_validate(
                metrics.model_dump(include=set(GenerationReference.model_fields))
            )
            documents = [
                BankQuestionDocument(
                    **question.model_dump(),
                    banco_id=bank.id,
                    secuencia=current.publicado_hasta + index + 1,
                    version=1,
                    origen="llm",
                    validacion={
                        "estructural": "validada",
                        "pedagogica": "automatica",
                        "apta_multiple_choice": False,
                    },
                    generacion=trace,
                ).model_dump(by_alias=True)
                for index, question in enumerate(accepted)
            ]
            # El avance de publicación bloquea el banco y verifica el token antes de insertar.
            result = await self.database.Bancos.update_one(
                {**self.owned(bank), "publicado_hasta": current.publicado_hasta},
                {
                    "$set": {
                        "publicado_hasta": current.publicado_hasta + len(documents),
                        "ultima_generacion": final_metrics.model_dump(),
                        "reposicion.estado": "completada",
                        "reposicion.token": None,
                        "reposicion.lease_hasta": None,
                        "reposicion.error_codigo": None,
                    }
                },
                session=session,
            )
            if result.matched_count != 1:
                raise ValueError("La concesión de publicación ha vencido")
            await self.database.BancoEjercicios.insert_many(documents, session=session)
            return True

        with timeout(10):
            async with self.database.client.start_session() as session:
                return await session.with_transaction(
                    transaction,
                    read_concern=ReadConcern("snapshot"),
                    write_concern=WriteConcern("majority"),
                    read_preference=ReadPreference.PRIMARY,
                    max_commit_time_ms=5000,
                )

    async def fail(self, bank, code, metrics=None):
        current = await self.database.Bancos.find_one(self.owned(bank))
        if current is None:
            return
        retry = (
            bank.reposicion.intentos < self.settings.bank_refill_max_attempts
            and current["reposicion"].get("rondas_consumidas", 0)
            < self.settings.bank_generation_max_rounds
            and code not in ("lote_incompleto", "sin_preguntas_nuevas")
        )
        changes = {
            "reposicion.estado": "error",
            "reposicion.error_codigo": code,
            "reposicion.token": None,
            "reposicion.lease_hasta": None,
            "reposicion.reintento_en": now()
            + timedelta(seconds=self.settings.bank_refill_retry_seconds)
            if retry
            else None,
        }
        if metrics is not None:
            changes["ultima_generacion"] = metrics.model_copy(
                update={
                    "errores": (
                        metrics.errores
                        + [GenerationError(codigo=code, mensaje="El lote no se ha publicado")]
                    )[-50:],
                }
            ).model_dump()
        await self.database.Bancos.update_one(self.owned(bank), {"$set": changes})

    async def process(self, bank):
        metrics = None
        batch = None
        previous = bank.ultima_generacion
        if previous is not None and previous.generacion_id != bank.reposicion.generacion_id:
            previous = None
        started = monotonic()

        def accumulated_metrics():
            if metrics is None or batch is None:
                return metrics
            rounds = [
                GenerationRoundMetrics.model_validate(
                    {k: v for k, v in r.items() if k in GenerationRoundMetrics.model_fields}
                )
                for r in batch.rounds
            ]
            changes = {
                "latencia_ms": (previous.latencia_ms if previous else 0)
                + int((monotonic() - started) * 1000),
                "preguntas_recibidas": (previous.preguntas_recibidas if previous else 0)
                + batch.received,
                "preguntas_recibidas_completas": all(
                    r["recibidas"] is not None for r in batch.rounds
                )
                and (previous is None or previous.preguntas_recibidas_completas is True),
                "preguntas_validas": len(batch.questions),
                "preguntas_rechazadas_contrato": (
                    previous.preguntas_rechazadas_contrato if previous else 0
                )
                + batch.rejected,
                "preguntas_descartadas_duplicadas": (
                    previous.preguntas_descartadas_duplicadas if previous else 0
                )
                + batch.duplicate_count,
                "preguntas_descartadas_cuota": (
                    previous.preguntas_descartadas_cuota if previous else 0
                )
                + sum(r["descartadas_cuota"] for r in batch.rounds),
                "rondas": (previous.rondas if previous else []) + rounds,
                "rondas_consumidas": bank.reposicion.rondas_consumidas + len(rounds),
                "errores": (
                    (previous.errores if previous else [])
                    + [
                        GenerationError(
                            codigo="pregunta_invalida",
                            mensaje=(
                                f"Ronda {r['ronda']}, índice {rejection['indice']}: "
                                + "; ".join(
                                    f"{issue['campo']}: {issue['motivo']}"
                                    for issue in rejection["errores"]
                                )
                            )[:500],
                        )
                        for r in batch.rounds
                        for rejection in r["rechazos_contrato"]
                    ]
                )[-49:],
            }
            for name in ("tokens_entrada", "tokens_salida", "coste_estimado_usd"):
                current = batch.total(name)
                old = getattr(previous, name) if previous else None
                changes[name] = (
                    (old + current if old is not None and current is not None else None)
                    if previous
                    else current
                )
                if name != "coste_estimado_usd":
                    old_known = getattr(previous, name + "_conocidos") if previous else None
                    if old_known is None:
                        old_known = old
                    known = [v for v in (old_known, batch.known_total(name)) if v is not None]
                    changes[name + "_conocidos"] = sum(known) if known else None
            if batch.trace is not None:
                changes.update(batch.trace.model_dump())
            return GenerationMetrics.model_validate({**metrics.model_dump(), **changes})

        async def record_failure(code):
            await self.fail(bank, code, accumulated_metrics())

        try:
            if bank.reposicion.intentos > self.settings.bank_refill_max_attempts:
                await self.fail(bank, "intentos_agotados")
                return
            parameters, text, trace = await self.context(bank)
            metrics = GenerationMetrics(
                **trace.model_dump(),
                creado_en=now(),
                latencia_ms=0,
                preguntas_solicitadas=parameters.cantidad,
                preguntas_recibidas=0,
                preguntas_validas=0,
            )
            batch = GenerationBatch(parameters.cantidad)

            async def before_call():
                reserved = await self.database.Bancos.update_one(
                    {
                        **self.owned(bank),
                        "$expr": {
                            "$lt": [
                                {"$ifNull": ["$reposicion.rondas_consumidas", 0]},
                                self.settings.bank_generation_max_rounds,
                            ]
                        },
                    },
                    {"$inc": {"reposicion.rondas_consumidas": 1}},
                )
                if reserved.matched_count != 1:
                    raise IncompleteBatch("Sin presupuesto o concesión de generación")

            # Fuera de cualquier sesión/transacción MongoDB.
            async with asyncio.timeout(self.settings.bank_refill_timeout_seconds):
                batch = await collect_generation_batch(
                    self.database,
                    bank,
                    self.provider,
                    parameters.cantidad,
                    bank.reposicion.generacion_id,
                    max_rounds=max(
                        0,
                        self.settings.bank_generation_max_rounds
                        - bank.reposicion.rondas_consumidas,
                    ),
                    before_call=before_call,
                    progress=batch,
                    round_offset=bank.reposicion.rondas_consumidas,
                )
            metrics = accumulated_metrics()
            if not batch.complete:
                if not batch.questions:
                    raise NoNewQuestions("El lote no consiguió preguntas nuevas")
                raise IncompleteBatch("Rondas agotadas sin completar el lote")
            output = GenerationOutput(preguntas=batch.questions)
            await self.publish(bank, output, metrics)
        except (ValidationError, ValueError) as error:
            code = (
                "sin_preguntas_nuevas"
                if isinstance(error, NoNewQuestions)
                else "lote_incompleto"
                if isinstance(error, IncompleteBatch)
                else "contenido_invalido"
            )
            await record_failure(code)
        except TimeoutError:
            await record_failure("proveedor_timeout")
        except Exception:
            # No persistir mensajes del proveedor: podrían contener secretos o payloads.
            await record_failure("reposicion_fallida")

    async def run(self):
        while True:
            try:
                bank = await self.claim()
                if bank is not None:
                    await self.process(bank)
                    continue
            except Exception:
                logger.warning("El worker de reposición no pudo completar su ciclo")
            await asyncio.sleep(self.settings.bank_refill_poll_seconds)
