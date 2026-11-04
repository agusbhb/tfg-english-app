import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from bson import ObjectId

from app.database import ensure_pending_index
from app.models import BlankQuestion
from app.practice import grade
from app.schemas import AnswerInput
from app.seed import seed_database


@pytest.fixture
def practice(api, registration):
    client, db, _ = api
    client.post("/auth/register", json=registration)
    token = client.post(
        "/auth/login", json={k: registration[k] for k in ("email", "password")}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    topic = str(db.BancoEjercicios.find_one()["tema_id"])
    response = client.post("/tests", json={"tema_id": topic}, headers=headers)
    assert response.status_code == 201
    return client, db, headers, response.json(), topic


def payload(test, answer=""):
    return {
        "respuestas": [
            {"pregunta_id": q["pregunta_id"], "respuesta_dada": answer} for q in test["preguntas"]
        ]
    }


def test_snapshot_privacy_origin_persistence_and_seed(practice):
    client, db, headers, test, _ = practice
    assert len(test["preguntas"]) == 10
    assert test["resultados"] is None
    assert all(set(q) == {"pregunta_id", "formato", "enunciado"} for q in test["preguntas"])
    stored = db.Tests.find_one({"_id": ObjectId(test["id"])})
    assert stored["origen"] == "banco" and len(stored["origenes_preguntas"]) == 10
    answers = {
        "respuestas": [
            {
                "pregunta_id": q["pregunta_id"],
                "respuesta_dada": "  " + q["solucion"]["respuestas_aceptadas"][0].upper() + " ",
            }
            for q in stored["preguntas_generadas"]
        ]
    }
    db.BancoEjercicios.delete_many({})
    result = client.post(f"/tests/{test['id']}/respuestas", json=answers, headers=headers)
    assert result.status_code == 200
    assert result.json()["aciertos"] == 10 and result.json()["puntuacion"] == 10
    assert client.get(f"/tests/{test['id']}", headers=headers).json() == result.json()
    before = db.Tests.find_one({"_id": stored["_id"]})
    client.portal.call(seed_database, client.app.state.database)
    client.portal.call(seed_database, client.app.state.database)
    assert db.Tests.find_one({"_id": stored["_id"]}) == before
    assert (
        client.post(f"/tests/{test['id']}/respuestas", json=answers, headers=headers).status_code
        == 409
    )


def test_empty_answers_and_atomic_double_delivery(practice):
    client, db, headers, test, _ = practice

    def deliver(_):
        return client.post(f"/tests/{test['id']}/respuestas", json=payload(test), headers=headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(deliver, range(2)))
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert next(r for r in responses if r.status_code == 200).json()["aciertos"] == 0
    assert db.Tests.find_one()["estado"] == "completado"


@pytest.mark.parametrize("kind", ["missing", "duplicate", "unknown", "spoof", "long"])
def test_invalid_answers(practice, kind):
    client, db, headers, test, _ = practice
    body = payload(test)
    if kind == "missing":
        body["respuestas"].pop()
    elif kind == "duplicate":
        body["respuestas"][1] = body["respuestas"][0]
    elif kind == "unknown":
        body["respuestas"][0]["pregunta_id"] = "unknown"
    elif kind == "spoof":
        body["respuestas"][0]["es_correcta"] = True
    else:
        body["respuestas"][0]["respuesta_dada"] = "x" * 10001
    assert (
        client.post(f"/tests/{test['id']}/respuestas", json=body, headers=headers).status_code
        == 422
    )
    assert db.Tests.find_one()["estado"] == "pendiente"


def test_authentication_and_isolation(practice, registration):
    client, _, headers, test, topic = practice
    for method, url, body in [
        ("post", "/tests", {"tema_id": topic}),
        ("get", f"/tests/{test['id']}", None),
        ("post", f"/tests/{test['id']}/respuestas", payload(test)),
    ]:
        kwargs = {"json": body} if body else {}
        assert getattr(client, method)(url, **kwargs).status_code == 401
        assert (
            getattr(client, method)(
                url, headers={"Authorization": "Bearer invalid"}, **kwargs
            ).status_code
            == 401
        )
    registration["email"] = "second@example.com"
    client.post("/auth/register", json=registration)
    token = client.post(
        "/auth/login", json={k: registration[k] for k in ("email", "password")}
    ).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    assert client.get(f"/tests/{test['id']}", headers=other).status_code == 404
    assert (
        client.post(
            f"/tests/{test['id']}/respuestas", json=payload(test), headers=other
        ).status_code
        == 404
    )
    assert client.get("/tests/not-an-id", headers=headers).status_code == 422
    assert client.get(f"/tests/{ObjectId()}", headers=headers).status_code == 404


def test_topic_and_opt_in_selection(practice):
    client, db, headers, original, topic = practice
    assert client.delete(f"/tests/{original['id']}", headers=headers).status_code == 200
    assert (
        client.post("/tests", json={"tema_id": str(ObjectId())}, headers=headers).status_code == 422
    )
    bank = db.BancoEjercicios.find_one()
    # Identical local IDs in different examples are remapped within the test.
    for example in bank["ejemplos_resueltos"]:
        example["preguntas"][0]["pregunta_id"] = "same-local-id"
        example["uso_practica"] = False
    bank["ejemplos_resueltos"][0]["uso_practica"] = True
    bank["ejemplos_resueltos"][1]["uso_practica"] = True
    db.BancoEjercicios.replace_one({"_id": bank["_id"]}, bank)
    test = client.post("/tests", json={"tema_id": topic}, headers=headers).json()
    assert len(test["preguntas"]) == 2
    assert len({q["pregunta_id"] for q in test["preguntas"]}) == 2
    assert client.delete(f"/tests/{test['id']}", headers=headers).status_code == 200
    db.BancoEjercicios.update_one({}, {"$set": {"ejemplos_resueltos.$[].uso_practica": False}})
    assert client.post("/tests", json={"tema_id": topic}, headers=headers).status_code == 404
    db.BancoEjercicios.delete_many({})
    assert client.post("/tests", json={"tema_id": topic}, headers=headers).status_code == 404


def test_legacy_seed_enables_only_unchanged_originals(practice):
    client, db, _, _, _ = practice
    bank = db.BancoEjercicios.find_one()
    for example in bank["ejemplos_resueltos"]:
        example.pop("uso_practica")
    bank["ejemplos_resueltos"][0]["preguntas"][0]["enunciado"] = "Contenido editado"
    bank["ejemplos_resueltos"][1]["uso_practica"] = False
    db.BancoEjercicios.replace_one({"_id": bank["_id"]}, bank)
    client.portal.call(seed_database, client.app.state.database)
    examples = db.BancoEjercicios.find_one()["ejemplos_resueltos"]
    assert "uso_practica" not in examples[0]
    assert examples[1]["uso_practica"] is False
    assert all(e["uso_practica"] for e in examples[2:])


@pytest.mark.parametrize(
    "answer,sensitive,accepted,correct",
    [
        (" WATCHES ", False, ["watches"], True),
        ("WATCHES", True, ["watches"], False),
        (" watches ", True, ["watches"], True),
        ("", False, ["watches"], False),
        ("   ", False, ["watches"], False),
        ("watch", False, ["watches", "watch"], True),
        ("watches.", False, ["watches"], False),
    ],
)
def test_deterministic_grader(answer, sensitive, accepted, correct):
    question = BlankQuestion(
        pregunta_id="q1",
        formato="fill_blanks",
        enunciado="She ___ TV.",
        solucion={"respuestas_aceptadas": accepted, "sensible_mayusculas": sensitive},
    )
    result = grade([question], [AnswerInput(pregunta_id="q1", respuesta_dada=answer)])
    assert result[0].es_correcta is correct


def test_pending_resume_discard_and_completed_protection(practice):
    client, db, headers, test, topic = practice
    found = client.get("/tests/pendiente", params={"tema_id": topic}, headers=headers)
    assert found.status_code == 200 and found.json() == test
    assert db.Tests.count_documents({}) == 1
    assert client.post("/tests", json={"tema_id": topic}, headers=headers).status_code == 409
    assert client.delete(f"/tests/{test['id']}", headers=headers).status_code == 200
    assert client.get("/tests/pendiente", params={"tema_id": topic}, headers=headers).json() is None
    replacement = client.post("/tests", json={"tema_id": topic}, headers=headers).json()
    assert replacement["id"] != test["id"]
    result = client.post(
        f"/tests/{replacement['id']}/respuestas", json=payload(replacement), headers=headers
    )
    assert result.status_code == 200
    completed = db.Tests.find_one({"_id": ObjectId(replacement["id"])})
    assert client.delete(f"/tests/{replacement['id']}", headers=headers).status_code == 409
    assert db.Tests.find_one({"_id": completed["_id"]}) == completed
    assert client.post("/tests", json={"tema_id": topic}, headers=headers).status_code == 201


def test_pending_creation_concurrency_and_other_topics(practice, monkeypatch):
    client, db, headers, test, topic = practice
    client.delete(f"/tests/{test['id']}", headers=headers)
    collection_type = type(client.app.state.database.Tests)
    original_insert = collection_type.insert_one
    ready = asyncio.Event()
    arrivals = 0

    async def simultaneous_insert(collection, *args, **kwargs):
        nonlocal arrivals
        if collection.name == "Tests":
            arrivals += 1
            if arrivals == 4:
                ready.set()
            await asyncio.wait_for(ready.wait(), timeout=5)
        return await original_insert(collection, *args, **kwargs)

    monkeypatch.setattr(collection_type, "insert_one", simultaneous_insert)

    def create(_):
        return client.post("/tests", json={"tema_id": topic}, headers=headers)

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(create, range(4)))
    assert sorted(r.status_code for r in responses) == [201, 409, 409, 409]
    assert db.Tests.count_documents({"estado": "pendiente"}) == 1
    other_topic = ObjectId()
    bank = db.BancoEjercicios.find_one()
    db.Asignaturas.update_one(
        {"_id": bank["asignatura_id"]},
        {"$push": {"niveles.0.temas": {"tema_id": other_topic, "nombre": "Otro tema"}}},
    )
    bank.update(_id=ObjectId(), tema_id=other_topic)
    db.BancoEjercicios.insert_one(bank)
    assert (
        client.post("/tests", json={"tema_id": str(other_topic)}, headers=headers).status_code
        == 201
    )
    assert db.Tests.count_documents({"estado": "pendiente"}) == 2


def test_pending_endpoints_require_auth_and_ownership(practice, registration):
    client, db, headers, test, topic = practice
    for auth in ({}, {"Authorization": "Bearer invalid"}):
        assert (
            client.get("/tests/pendiente", params={"tema_id": topic}, headers=auth).status_code
            == 401
        )
        assert client.delete(f"/tests/{test['id']}", headers=auth).status_code == 401
    registration["email"] = "pending-other@example.com"
    client.post("/auth/register", json=registration)
    token = client.post(
        "/auth/login", json={k: registration[k] for k in ("email", "password")}
    ).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    assert client.get("/tests/pendiente", params={"tema_id": topic}, headers=other).json() is None
    assert client.delete(f"/tests/{test['id']}", headers=other).status_code == 404
    assert client.post("/tests", json={"tema_id": topic}, headers=other).status_code == 201
    assert db.Tests.count_documents({"estado": "pendiente"}) == 2
    assert (
        client.get(
            "/tests/pendiente", params={"tema_id": str(ObjectId())}, headers=headers
        ).status_code
        == 422
    )
    assert (
        client.get("/tests/pendiente", params={"tema_id": "invalid"}, headers=headers).status_code
        == 422
    )
    assert client.delete("/tests/invalid", headers=headers).status_code == 422


def test_legacy_duplicate_migration_preserves_completed_and_oldest(practice):
    client, db, headers, test, topic = practice
    client.post(f"/tests/{test['id']}/respuestas", json=payload(test), headers=headers)
    completed = db.Tests.find_one({"_id": ObjectId(test["id"])})
    pending = client.post("/tests", json={"tema_id": topic}, headers=headers).json()
    original = db.Tests.find_one({"_id": ObjectId(pending["id"])})
    db.Tests.drop_index("pending_user_topic_unique")
    clone = dict(original)
    clone["_id"] = ObjectId()
    db.Tests.insert_one(clone)
    client.portal.call(ensure_pending_index, client.app.state.database)
    client.portal.call(ensure_pending_index, client.app.state.database)
    assert db.Tests.count_documents({"estado": "pendiente"}) == 1
    assert db.Tests.find_one({"_id": original["_id"]}) == original
    assert db.Tests.find_one({"_id": completed["_id"]}) == completed
    index = db.Tests.index_information()["pending_user_topic_unique"]
    assert index["unique"] and index["partialFilterExpression"] == {"estado": "pendiente"}


def test_discard_racing_with_submission_never_removes_completed(practice):
    client, db, headers, test, _ = practice

    def operation(kind):
        if kind == "discard":
            return client.delete(f"/tests/{test['id']}", headers=headers)
        return client.post(f"/tests/{test['id']}/respuestas", json=payload(test), headers=headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        discarded, submitted = list(pool.map(operation, ["discard", "submit"]))
    if submitted.status_code == 200:
        assert discarded.status_code == 409
        assert db.Tests.find_one({"_id": ObjectId(test["id"])})["estado"] == "completado"
    else:
        assert discarded.status_code == 200
        assert submitted.status_code in (404, 409)
        assert db.Tests.find_one({"_id": ObjectId(test["id"])}) is None


def test_draft_updates_and_recovery_without_solutions(practice):
    client, db, headers, test, topic = practice
    url = f"/tests/{test['id']}/borrador"
    # Compatibilidad con intentos anteriores sin el campo.
    db.Tests.update_one({"_id": ObjectId(test["id"])}, {"$unset": {"respuestas_borrador": ""}})
    assert client.get(f"/tests/{test['id']}", headers=headers).json()["respuestas_borrador"] == []
    for question, answer in [("q1", " play "), ("q2", "$literal"), ("q1", "modified"), ("q1", "")]:
        response = client.patch(
            url, json={"pregunta_id": question, "respuesta_dada": answer}, headers=headers
        )
        assert response.status_code == 200 and response.json()["respuesta_dada"] == answer
    client.patch(url, json={"pregunta_id": "q1", "respuesta_dada": ""}, headers=headers)
    loaded = client.get("/tests/pendiente", params={"tema_id": topic}, headers=headers).json()
    assert loaded["id"] == test["id"]
    assert {a["pregunta_id"]: a["respuesta_dada"] for a in loaded["respuestas_borrador"]} == {
        "q1": "",
        "q2": "$literal",
    }
    assert "solucion" not in str(loaded) and loaded["resultados"] is None
    assert db.Tests.count_documents({}) == 1
    assert db.Tests.find_one()["respuestas_alumno"] == []


def test_parallel_drafts_do_not_overwrite_other_questions(practice):
    client, db, headers, test, _ = practice

    def save(question):
        return client.patch(
            f"/tests/{test['id']}/borrador",
            json={"pregunta_id": question, "respuesta_dada": question},
            headers=headers,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(save, ["q1", "q2"]))
    assert all(r.status_code == 200 for r in responses)
    assert {a["pregunta_id"] for a in db.Tests.find_one()["respuestas_borrador"]} == {"q1", "q2"}


@pytest.mark.parametrize(
    "body",
    [
        {"pregunta_id": "unknown", "respuesta_dada": "x"},
        {"pregunta_id": "", "respuesta_dada": "x"},
        {"pregunta_id": "q1", "respuesta_dada": "x" * 10001},
        {"pregunta_id": "q1", "respuesta_dada": None},
        {"pregunta_id": "q1", "respuesta_dada": "x", "es_correcta": True},
    ],
)
def test_invalid_draft(practice, body):
    client, db, headers, test, _ = practice
    before = db.Tests.find_one()
    assert (
        client.patch(f"/tests/{test['id']}/borrador", json=body, headers=headers).status_code == 422
    )
    assert db.Tests.find_one() == before


def test_draft_auth_and_owner(practice, registration):
    client, db, headers, test, _ = practice
    url = f"/tests/{test['id']}/borrador"
    body = {"pregunta_id": "q1", "respuesta_dada": "play"}
    for auth in ({}, {"Authorization": "Bearer invalid"}):
        assert client.patch(url, json=body, headers=auth).status_code == 401
    registration["email"] = "draft-other@example.com"
    client.post("/auth/register", json=registration)
    token = client.post(
        "/auth/login", json={k: registration[k] for k in ("email", "password")}
    ).json()["access_token"]
    assert (
        client.patch(url, json=body, headers={"Authorization": f"Bearer {token}"}).status_code
        == 404
    )
    assert (
        client.patch(f"/tests/{ObjectId()}/borrador", json=body, headers=headers).status_code == 404
    )
    assert client.patch("/tests/bad/borrador", json=body, headers=headers).status_code == 422
    assert db.Tests.find_one()["respuestas_borrador"] == []


def test_submission_uses_current_answers_and_removes_draft(practice):
    client, db, headers, test, _ = practice
    url = f"/tests/{test['id']}/borrador"
    client.patch(url, json={"pregunta_id": "q1", "respuesta_dada": "play"}, headers=headers)
    result = client.post(f"/tests/{test['id']}/respuestas", json=payload(test), headers=headers)
    assert result.status_code == 200 and result.json()["aciertos"] == 0
    assert result.json()["respuestas_borrador"] == []
    completed = db.Tests.find_one()
    assert "respuestas_borrador" not in completed
    assert (
        client.patch(
            url, json={"pregunta_id": "q1", "respuesta_dada": "play"}, headers=headers
        ).status_code
        == 409
    )
    assert db.Tests.find_one() == completed


def test_late_draft_after_submission_cannot_recreate_field(practice, monkeypatch):
    client, db, headers, test, _ = practice
    ready = threading.Event()
    release = asyncio.Event()
    collection_type = type(client.app.state.database.Tests)
    original_update = collection_type.update_one

    async def paused_update(collection, *args, **kwargs):
        if collection.name == "Tests":
            ready.set()
            await asyncio.wait_for(release.wait(), timeout=5)
        return await original_update(collection, *args, **kwargs)

    monkeypatch.setattr(collection_type, "update_one", paused_update)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            client.patch,
            f"/tests/{test['id']}/borrador",
            json={"pregunta_id": "q1", "respuesta_dada": "play"},
            headers=headers,
        )
        try:
            assert ready.wait(timeout=5)
            result = client.post(
                f"/tests/{test['id']}/respuestas", json=payload(test), headers=headers
            )
            assert result.status_code == 200
        finally:
            client.portal.call(release.set)
        assert future.result(timeout=5).status_code == 409
    completed = db.Tests.find_one()
    assert completed["estado"] == "completado"
    assert "respuestas_borrador" not in completed


def test_draft_racing_submission_never_changes_completed(practice):
    client, db, headers, test, _ = practice

    def operation(kind):
        if kind == "save":
            return client.patch(
                f"/tests/{test['id']}/borrador",
                json={"pregunta_id": "q1", "respuesta_dada": "play"},
                headers=headers,
            )
        return client.post(f"/tests/{test['id']}/respuestas", json=payload(test), headers=headers)

    with ThreadPoolExecutor(max_workers=2) as pool:
        saved, submitted = list(pool.map(operation, ["save", "submit"]))
    assert saved.status_code in (200, 409)
    assert submitted.status_code == 200
    completed = db.Tests.find_one()
    assert completed["estadisticas"]["aciertos"] == 0
    assert "respuestas_borrador" not in completed
