"""Preparación desde banco y corrección pura sobre una copia inmutable del test."""

from datetime import UTC, datetime

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from app.bank_contracts import BankMetadataDocument, BankQuestionDocument
from app.models import (
    BlankQuestion,
    StudentAnswer,
    SubjectDocument,
    TestDocument,
    validate_academic_reference,
)
from app.schemas import PublicTest


def grade(questions, answers):
    ids = [answer.pregunta_id for answer in answers]
    if len(ids) != len(set(ids)) or set(ids) != {q.pregunta_id for q in questions}:
        raise ValueError("Incluye cada pregunta exactamente una vez")
    by_id = {answer.pregunta_id: answer.respuesta_dada for answer in answers}
    results = []
    for question in questions:
        if not isinstance(question, BlankQuestion):
            raise ValueError("Este corrector solo admite fill_blanks")
        solution = question.solucion

        def normalize(value, sensitive=solution.sensible_mayusculas):
            value = value.strip()
            return value if sensitive else value.casefold()

        response = by_id[question.pregunta_id]
        correct = bool(response.strip()) and any(
            normalize(response) == normalize(accepted) for accepted in solution.respuestas_aceptadas
        )
        results.append(
            StudentAnswer(
                pregunta_id=question.pregunta_id, respuesta_dada=response, es_correcta=correct
            )
        )
    return results


def public_test(test):
    completed = test.estado == "completado"
    answers = {a.pregunta_id: a for a in test.respuestas_alumno}
    return PublicTest(
        id=test.id,
        tema_id=test.tema_id,
        estado=test.estado,
        respuestas_borrador=[a.model_dump() for a in test.respuestas_borrador]
        if test.estado == "pendiente"
        else [],
        preguntas=[
            {"pregunta_id": q.pregunta_id, "formato": q.formato, "enunciado": q.enunciado}
            for q in test.preguntas_generadas
        ],
        total_preguntas=len(test.preguntas_generadas),
        aciertos=test.estadisticas.aciertos if completed else None,
        puntuacion=round(10 * test.estadisticas.aciertos / test.estadisticas.total_preguntas, 2)
        if completed
        else None,
        resultados=[
            {
                "pregunta_id": q.pregunta_id,
                "respuesta_dada": answers[q.pregunta_id].respuesta_dada,
                "es_correcta": answers[q.pregunta_id].es_correcta,
                "respuestas_aceptadas": q.solucion.respuestas_aceptadas,
            }
            for q in test.preguntas_generadas
        ]
        if completed
        else None,
    )


async def validate_topic(database, user, topic_id):
    profile = user.perfil_academico
    subject = await database.Asignaturas.find_one({"_id": profile.asignatura_id})
    try:
        if subject is None:
            raise ValueError("Asignatura inexistente")
        catalog = SubjectDocument.model_validate(subject)
        validate_academic_reference(catalog, profile.nivel_id, topic_id)
    except ValueError:
        raise HTTPException(422, "El tema no pertenece a tu curso") from None
    for level in catalog.niveles:
        if level.nivel_id == profile.nivel_id:
            return next(topic for topic in level.temas if topic.tema_id == topic_id)


async def pending_test(database, user, topic_id):
    await validate_topic(database, user, topic_id)
    document = await database.Tests.find_one(
        {"usuario_id": user.id, "tema_id": topic_id, "estado": "pendiente"}
    )
    return TestDocument.model_validate(document) if document else None


async def create_test(database, user, topic_id):
    if await pending_test(database, user, topic_id):
        raise HTTPException(409, "Ya tienes un test pendiente para este tema")
    profile = user.perfil_academico
    topic = await validate_topic(database, user, topic_id)
    document = await database.Bancos.find_one(
        {"tema_id": topic_id, "nivel_mcer_objetivo": topic.nivel_mcer_objetivo}
    )
    if document is None:
        raise HTTPException(404, "No hay ejercicios disponibles para este tema")
    bank = BankMetadataDocument.model_validate(document)
    questions, origins = [], []
    documents = (
        await database.BancoEjercicios.find(
            {
                "banco_id": bank.id,
                "secuencia": {"$lte": bank.publicado_hasta},
                "validacion.pedagogica": "revisada",
            }
        )
        .sort("secuencia", 1)
        .limit(10)
        .to_list()
    )
    for document in documents:
        question = BankQuestionDocument.model_validate(document)
        identifier = str(question.id)
        questions.append(
            BlankQuestion(
                pregunta_id=identifier,
                formato="fill_blanks",
                enunciado=question.enunciado,
                solucion=question.solucion.model_dump(),
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
    if not questions:
        raise HTTPException(404, "No hay ejercicios disponibles para este tema")
    test = TestDocument(
        usuario_id=user.id,
        asignatura_id=profile.asignatura_id,
        nivel_id=profile.nivel_id,
        tema_id=topic_id,
        objetivo="gramatica",
        nivel_mcer=bank.nivel_mcer_objetivo,
        presentacion="frases_independientes",
        origen="banco",
        origenes_preguntas=origins,
        preguntas_generadas=questions,
        fechas={"generacion": datetime.now(UTC)},
    )
    try:
        await database.Tests.insert_one(test.model_dump(by_alias=True))
    except DuplicateKeyError:
        raise HTTPException(409, "Ya tienes un test pendiente para este tema") from None
    return test


async def discard_test(database, user_id, test_id):
    await own_test(database, user_id, test_id)
    result = await database.Tests.delete_one(
        {"_id": test_id, "usuario_id": user_id, "estado": "pendiente"}
    )
    if result.deleted_count != 1:
        raise HTTPException(409, "El test ya no está pendiente; no se ha descartado")


async def own_test(database, user_id, test_id):
    document = await database.Tests.find_one({"_id": test_id, "usuario_id": user_id})
    if document is None:
        raise HTTPException(404, "Test no encontrado")
    return TestDocument.model_validate(document)


async def save_draft(database, user_id, test_id, answer):
    test = await own_test(database, user_id, test_id)
    if test.estado != "pendiente":
        raise HTTPException(409, "Este test ya no está pendiente")
    if answer.pregunta_id not in {q.pregunta_id for q in test.preguntas_generadas}:
        raise HTTPException(422, "La pregunta no pertenece a este test")
    # Actualiza una pregunta sobre el array actual, nunca una copia leída antes.
    # $literal protege IDs y respuestas que contengan expresiones MongoDB.
    result = await database.Tests.update_one(
        {
            "_id": test_id,
            "usuario_id": user_id,
            "estado": "pendiente",
            "preguntas_generadas.pregunta_id": answer.pregunta_id,
        },
        [
            {
                "$set": {
                    "respuestas_borrador": {
                        "$concatArrays": [
                            {
                                "$filter": {
                                    "input": {"$ifNull": ["$respuestas_borrador", []]},
                                    "as": "answer",
                                    "cond": {
                                        "$ne": [
                                            "$$answer.pregunta_id",
                                            {"$literal": answer.pregunta_id},
                                        ]
                                    },
                                }
                            },
                            {"$literal": [answer.model_dump()]},
                        ]
                    }
                }
            }
        ],
    )
    if result.matched_count != 1:
        raise HTTPException(409, "El test ya no está pendiente; no se guardó el borrador")
    return answer


async def submit_test(database, user_id, test_id, answers):
    test = await own_test(database, user_id, test_id)
    if test.estado != "pendiente":
        raise HTTPException(409, "Este test ya se ha entregado")
    try:
        graded = grade(test.preguntas_generadas, answers)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    document = test.model_dump(by_alias=True)
    document.pop("respuestas_borrador", None)
    document.update(
        estado="completado",
        respuestas_alumno=[a.model_dump() for a in graded],
        estadisticas={
            "aciertos": sum(a.es_correcta for a in graded),
            "total_preguntas": len(graded),
        },
    )
    document["fechas"]["resolucion"] = datetime.now(UTC)
    completed = TestDocument.model_validate(document)
    result = await database.Tests.replace_one(
        {"_id": test_id, "usuario_id": user_id, "estado": "pendiente"},
        completed.model_dump(by_alias=True, exclude={"respuestas_borrador"}),
    )
    if result.modified_count != 1:
        raise HTTPException(409, "Este test ya se ha entregado")
    return completed
