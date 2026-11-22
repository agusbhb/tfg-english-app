"""Reserva de preguntas y snapshot confirmados en una transacción corta."""

from datetime import UTC, datetime

from fastapi import HTTPException
from pymongo import ReadPreference, timeout
from pymongo.errors import DuplicateKeyError, OperationFailure
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.bank_contracts import BankMetadataDocument, BankProgressDocument, BankQuestionDocument
from app.models import BlankQuestion, TestDocument


class BankExhausted(HTTPException):
    def __init__(self, bank_id, cursor):
        super().__init__(409, "Has agotado las preguntas publicadas de este banco")
        self.bank_id = bank_id
        self.cursor = cursor


def snapshot(user, bank, documents):
    questions, origins = [], []
    for document in documents:
        question = BankQuestionDocument.model_validate(document)
        if question.validacion.pedagogica not in ("revisada", "automatica"):
            raise HTTPException(503, "El contenido publicado del banco necesita revisión")
        identifier = str(question.id)
        questions.append(
            BlankQuestion(
                pregunta_id=identifier,
                formato="fill_blanks",
                enunciado=question.enunciado,
                solucion=question.solucion.model_dump(),
                modalidad=question.modalidad,
            )
        )
        origins.append(
            {
                "pregunta_id": identifier,
                "banco_id": bank.id,
                "pregunta_banco_id": question.id,
                "version_pregunta": question.version,
                "secuencia": question.secuencia,
            }
        )
    return TestDocument(
        usuario_id=user.id,
        asignatura_id=user.perfil_academico.asignatura_id,
        nivel_id=user.perfil_academico.nivel_id,
        tema_id=bank.tema_id,
        objetivo="gramatica",
        nivel_mcer=bank.nivel_mcer_objetivo,
        presentacion="frases_independientes",
        origen="banco",
        origenes_preguntas=origins,
        preguntas_generadas=questions,
        fechas={"generacion": datetime.now(UTC)},
    )


async def reserve_test(database, user, topic):
    scope = {"tema_id": topic.tema_id, "nivel_mcer_objetivo": topic.nivel_mcer_objetivo}
    document = await database.Bancos.find_one(scope)
    if document is None:
        raise HTTPException(404, "No hay ejercicios disponibles para este tema")
    bank = BankMetadataDocument.model_validate(document)
    key = {"usuario_id": user.id, "banco_id": bank.id}
    # Crear el contador cero no consume preguntas; evita el upsert concurrente
    # dentro de una transacción. Los cursores históricos se inicializan al arrancar.
    progress = BankProgressDocument(**key)
    await database.Progreso.update_one(
        key,
        {"$setOnInsert": progress.model_dump(by_alias=True)},
        upsert=True,
    )

    async def reserve(session):
        if await database.Tests.find_one(
            {"usuario_id": user.id, "tema_id": topic.tema_id, "estado": "pendiente"},
            session=session,
        ):
            raise HTTPException(409, "Ya tienes un test pendiente para este tema")
        current_bank = await database.Bancos.find_one({"_id": bank.id}, session=session)
        if current_bank is None:
            raise HTTPException(404, "No hay ejercicios disponibles para este tema")
        published = BankMetadataDocument.model_validate(current_bank)
        current = BankProgressDocument.model_validate(
            await database.Progreso.find_one(key, session=session)
        )
        last = current.ultima_secuencia_reservada
        if last >= published.publicado_hasta:
            raise BankExhausted(bank.id, last)
        end = min(last + 10, published.publicado_hasta)
        documents = (
            await database.BancoEjercicios.find(
                {"banco_id": bank.id, "secuencia": {"$gt": last, "$lte": end}},
                session=session,
            )
            .sort("secuencia", 1)
            .to_list()
        )
        if [q["secuencia"] for q in documents] != list(range(last + 1, end + 1)):
            raise HTTPException(503, "El contenido publicado del banco está incompleto")
        test = snapshot(user, published, documents)
        result = await database.Progreso.update_one(
            {**key, "ultima_secuencia_reservada": last},
            {"$set": {"ultima_secuencia_reservada": end}},
            session=session,
        )
        if result.matched_count != 1:
            raise HTTPException(409, "La reserva ha cambiado; vuelve a intentarlo")
        await database.Tests.insert_one(test.model_dump(by_alias=True), session=session)
        return test

    try:
        with timeout(10):
            async with database.client.start_session() as session:
                return await session.with_transaction(
                    reserve,
                    read_concern=ReadConcern("snapshot"),
                    write_concern=WriteConcern("majority"),
                    read_preference=ReadPreference.PRIMARY,
                    max_commit_time_ms=5000,
                )
    except DuplicateKeyError:
        raise HTTPException(409, "Ya tienes un test pendiente para este tema") from None
    except OperationFailure as error:
        if error.code == 20:
            raise HTTPException(
                503, "La reserva requiere MongoDB configurado como replica set"
            ) from None
        raise
