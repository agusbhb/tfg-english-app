import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import ConnectionFailure, DuplicateKeyError, OperationFailure

from app.database import initialize_progress
from app.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def reservation(api, registration, publish_bank):
    client, db, settings = api
    bank = publish_bank(20)
    user = client.post("/auth/register", json=registration).json()
    token = client.post(
        "/auth/login", json={k: registration[k] for k in ("email", "password")}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    return client, db, settings, headers, bank, user


def create(client, headers, bank):
    return client.post("/tests", json={"tema_id": str(bank["tema_id"])}, headers=headers)


def sequences(db, test):
    document = db.Tests.find_one({"_id": ObjectId(test["id"])})
    return [q["secuencia"] for q in document["origenes_preguntas"]]


def deliver(client, headers, test):
    return client.post(
        f"/tests/{test['id']}/respuestas",
        headers=headers,
        json={
            "respuestas": [
                {"pregunta_id": q["pregunta_id"], "respuesta_dada": ""} for q in test["preguntas"]
            ]
        },
    )


def test_blocks_resume_drafts_submission_and_exhaustion(reservation):
    client, db, _, headers, bank, user = reservation
    first = create(client, headers, bank)
    assert first.status_code == 201
    first = first.json()
    assert sequences(db, first) == list(range(1, 11))
    assert first["resultados"] is None and "solucion" not in str(first)
    progress = db.Progreso.find_one()
    assert progress["usuario_id"] == ObjectId(user["id"])
    assert progress["banco_id"] == bank["_id"]
    assert progress["ultima_secuencia_reservada"] == 10
    draft = {"pregunta_id": first["preguntas"][0]["pregunta_id"], "respuesta_dada": "play"}
    assert (
        client.patch(f"/tests/{first['id']}/borrador", headers=headers, json=draft).status_code
        == 200
    )
    loaded = client.get(
        "/tests/pendiente", params={"tema_id": str(bank["tema_id"])}, headers=headers
    )
    assert loaded.json()["id"] == first["id"]
    assert loaded.json()["respuestas_borrador"] == [draft]
    assert create(client, headers, bank).status_code == 409
    assert db.Progreso.find_one() == progress
    assert deliver(client, headers, first).status_code == 200
    assert db.Progreso.find_one() == progress
    second = create(client, headers, bank).json()
    assert sequences(db, second) == list(range(11, 21))
    assert not set(q["pregunta_id"] for q in first["preguntas"]) & set(
        q["pregunta_id"] for q in second["preguntas"]
    )
    assert deliver(client, headers, second).status_code == 200
    before = db.Progreso.find_one()
    exhausted = create(client, headers, bank)
    assert exhausted.status_code == 409 and "agotado" in exhausted.json()["detail"]
    assert db.Progreso.find_one() == before and before["ultima_secuencia_reservada"] == 20
    assert db.Tests.count_documents({}) == 2
    # Reiniciar la aplicación tampoco reinicia el cursor.
    client.portal.call(initialize_progress, client.app.state.database)
    assert db.Progreso.find_one() == before


def test_discard_consumes_block_and_final_partial_block(reservation, publish_bank):
    client, db, _, headers, bank, _ = reservation
    publish_bank(23)
    for expected in (range(1, 11), range(11, 21), range(21, 24)):
        response = create(client, headers, bank)
        assert response.status_code == 201
        test = response.json()
        assert sequences(db, test) == list(expected)
        before = db.Progreso.find_one()
        assert client.delete(f"/tests/{test['id']}", headers=headers).status_code == 200
        assert db.Progreso.find_one() == before
    assert db.Tests.count_documents({}) == 0
    assert db.Progreso.find_one()["ultima_secuencia_reservada"] == 23
    assert create(client, headers, bank).status_code == 409


def test_independent_students_banks_and_unique_progress(reservation, registration):
    client, db, _, headers, bank, _ = reservation
    first = create(client, headers, bank).json()
    registration = {**registration, "email": "independent@example.com"}
    assert client.post("/auth/register", json=registration).status_code == 201
    token = client.post(
        "/auth/login", json={k: registration[k] for k in ("email", "password")}
    ).json()["access_token"]
    other = {"Authorization": f"Bearer {token}"}
    second = create(client, other, bank).json()
    assert sequences(db, first) == sequences(db, second) == list(range(1, 11))
    assert client.get(f"/tests/{first['id']}", headers=other).status_code == 404
    assert db.Progreso.count_documents({}) == 2
    # Otro tema curricular del mismo curso y su propio banco.
    topic = ObjectId()
    db.Asignaturas.update_one(
        {},
        {
            "$push": {
                "niveles.0.temas": {
                    "tema_id": topic,
                    "nombre": "Otro tema",
                    "nivel_mcer_objetivo": "A1",
                }
            }
        },
    )
    another = {**bank, "_id": ObjectId(), "tema_id": topic}
    db.Bancos.insert_one(another)
    for question in db.BancoEjercicios.find({"banco_id": bank["_id"]}):
        db.BancoEjercicios.insert_one({**question, "_id": ObjectId(), "banco_id": another["_id"]})
    third = create(client, headers, another).json()
    assert sequences(db, third) == list(range(1, 11))
    assert db.Tests.count_documents({"estado": "pendiente"}) == 3
    assert db.Progreso.count_documents({}) == 3
    progress = db.Progreso.find_one()
    with pytest.raises(DuplicateKeyError):
        db.Progreso.insert_one({**progress, "_id": ObjectId()})


@pytest.mark.parametrize("stage", ["before_cursor", "after_cursor", "after_snapshot"])
@pytest.mark.parametrize("already_reserved", [False, True])
def test_failure_rolls_back_and_retry_reuses_same_block(
    reservation, monkeypatch, stage, already_reserved
):
    client, db, _, headers, bank, _ = reservation
    if already_reserved:
        first = create(client, headers, bank).json()
        assert deliver(client, headers, first).status_code == 200
    start = 10 if already_reserved else 0
    collection_type = type(client.app.state.database.Tests)
    original_update = collection_type.update_one
    original_insert = collection_type.insert_one

    async def update(collection, *args, **kwargs):
        target = collection.name == "Progreso" and kwargs.get("session")
        if target and stage == "before_cursor":
            raise ConnectionFailure("Fallo de prueba anterior al cursor")
        result = await original_update(collection, *args, **kwargs)
        if target and stage == "after_cursor":
            raise ConnectionFailure("Fallo de prueba posterior al cursor")
        return result

    async def insert(collection, *args, **kwargs):
        result = await original_insert(collection, *args, **kwargs)
        if collection.name == "Tests" and stage == "after_snapshot":
            raise ConnectionFailure("Fallo de prueba posterior al snapshot")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(collection_type, "update_one", update)
        patch.setattr(collection_type, "insert_one", insert)
        assert create(client, headers, bank).status_code == 503
    assert db.Tests.count_documents({}) == int(already_reserved)
    assert db.Progreso.find_one()["ultima_secuencia_reservada"] == start
    test = create(client, headers, bank)
    assert test.status_code == 201 and sequences(db, test.json()) == list(
        range(start + 1, start + 11)
    )
    assert db.Progreso.find_one()["ultima_secuencia_reservada"] == start + 10


def test_transient_failure_retries_entire_transaction(reservation, monkeypatch):
    client, db, _, headers, bank, _ = reservation
    collection_type = type(client.app.state.database.Tests)
    original = collection_type.insert_one
    calls = 0

    async def insert(collection, *args, **kwargs):
        nonlocal calls
        result = await original(collection, *args, **kwargs)
        if collection.name == "Tests":
            calls += 1
            if calls == 1:
                raise OperationFailure(
                    "Fallo transitorio inyectado",
                    112,
                    {"errorLabels": ["TransientTransactionError"]},
                )
        return result

    monkeypatch.setattr(collection_type, "insert_one", insert)
    result = create(client, headers, bank)
    assert result.status_code == 201 and calls == 2
    assert db.Tests.count_documents({}) == 1
    assert sequences(db, result.json()) == list(range(1, 11))
    assert db.Progreso.find_one()["ultima_secuencia_reservada"] == 10


def test_unknown_commit_result_retries_commit_without_another_block(reservation, monkeypatch):
    client, db, _, headers, bank, _ = reservation
    original = AsyncClientSession.commit_transaction
    calls = 0

    async def commit(session):
        nonlocal calls
        await original(session)
        calls += 1
        if calls == 1:
            error = ConnectionFailure("Respuesta del commit perdida en el ensayo")
            error._add_error_label("UnknownTransactionCommitResult")
            raise error

    monkeypatch.setattr(AsyncClientSession, "commit_transaction", commit)
    result = create(client, headers, bank)
    assert result.status_code == 201 and calls == 2
    assert db.Tests.count_documents({}) == 1
    assert db.Progreso.find_one()["ultima_secuencia_reservada"] == 10


def test_concurrent_independent_application_instances(reservation):
    client, db, settings, headers, bank, _ = reservation
    ready = threading.Barrier(2)
    with TestClient(create_app(settings)) as second:

        def request(application):
            ready.wait(timeout=5)
            return create(application, headers, bank)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(request, (client, second)))
    assert sorted(response.status_code for response in results) == [201, 409]
    assert db.Tests.count_documents({}) == 1
    assert db.Progreso.count_documents({}) == 1
    assert db.Progreso.find_one()["ultima_secuencia_reservada"] == 10


@pytest.mark.parametrize("completed", [False, True])
def test_startup_initializes_2b_cursor_without_changing_snapshots(reservation, completed):
    client, db, settings, headers, bank, _ = reservation
    old = create(client, headers, bank).json()
    if completed:
        assert deliver(client, headers, old).status_code == 200
        copy = db.Tests.find_one()
        copy["_id"] = ObjectId()
        db.Tests.insert_one(copy)  # 2B podía repetir el mismo primer bloque.
    before = list(db.Tests.find().sort("_id", 1))
    db.Progreso.delete_many({})  # Base 2B: snapshots sin colección de progreso.
    with TestClient(create_app(settings)) as application:
        assert before == list(db.Tests.find().sort("_id", 1))
        assert db.Progreso.find_one()["ultima_secuencia_reservada"] == 10
        assert application.get(f"/tests/{old['id']}", headers=headers).status_code == 200
        if not completed:
            assert application.delete(f"/tests/{old['id']}", headers=headers).status_code == 200
        result = create(application, headers, bank)
        assert result.status_code == 201 and sequences(db, result.json()) == list(range(11, 21))
    with TestClient(create_app(settings)):
        assert db.Progreso.find_one()["ultima_secuencia_reservada"] == 20


@pytest.mark.parametrize("problem", ["gap", "unreviewed", "unpublished"])
def test_invalid_or_unpublished_content_does_not_consume_progress(reservation, problem):
    client, db, _, headers, bank, _ = reservation
    if problem == "gap":
        db.BancoEjercicios.delete_one({"banco_id": bank["_id"], "secuencia": 4})
    elif problem == "unreviewed":
        db.BancoEjercicios.update_one(
            {"secuencia": 4}, {"$set": {"validacion.pedagogica": "pendiente"}}
        )
    else:
        db.Bancos.update_one({}, {"$set": {"publicado_hasta": 0}})
    result = create(client, headers, bank)
    assert result.status_code == (409 if problem == "unpublished" else 503)
    assert db.Progreso.find_one()["ultima_secuencia_reservada"] == 0
    assert db.Tests.count_documents({}) == 0
