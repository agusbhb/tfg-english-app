import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from bson import ObjectId
from demo_refill_app import DemoProvider
from fastapi.testclient import TestClient
from test_replenishment import complete, create, wait_for

from app.bank_contracts import BankQuestionDocument, modality_counts
from app.config import Settings
from app.main import create_app
from scripts.activate_generation_prompt import activate

pytestmark = pytest.mark.integration


def login(client, registration, email):
    data = {**registration, "email": email}
    assert client.post("/auth/register", json=data).status_code == 201
    response = client.post("/auth/login", json={"email": email, "password": data["password"]})
    return {"Authorization": "Bearer " + response.json()["access_token"]}


def sequences(database, test):
    return [
        q["secuencia"]
        for q in database.Tests.find_one({"_id": ObjectId(test["id"])})["origenes_preguntas"]
    ]


@pytest.mark.parametrize("total,trigger", [(31, False), (30, True)])
def test_v4_two_cursors_single_job_and_exact_publication(
    api, publish_bank, registration, total, trigger
):
    initial_client, database, initial = api
    publish_bank(total)
    initial_client.portal.call(activate, initial_client.app.state.database, 4)
    settings = Settings(
        _env_file=None,
        **{**initial.model_dump(), "bank_refill_enabled": True, "bank_refill_poll_seconds": 0.01},
    )
    providers = [DemoProvider(), DemoProvider()]
    for provider in providers:
        provider.gate = asyncio.Event()
    with (
        TestClient(create_app(settings, providers[0])) as first,
        TestClient(create_app(settings, providers[1])) as second,
    ):
        clients = [first, second]
        headers = [login(c, registration, f"cursor{i}@example.com") for i, c in enumerate(clients)]
        topic = str(database.Bancos.find_one()["tema_id"])
        bank_before = database.Bancos.find_one()
        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(
                executor.map(lambda i: create(clients[i], headers[i], topic), range(2))
            )
        assert all(response.status_code == 201 for response in responses)
        tests = [r.json() for r in responses]
        assert all(sequences(database, test) == list(range(1, 11)) for test in tests)
        progress = list(database.Progreso.find().sort("_id", 1))
        assert [p["ultima_secuencia_reservada"] for p in progress] == [10, 10]
        if not trigger:
            # Barrera en ambos loops; solo quedan 21, sin tarea nueva.
            for c in clients:
                c.portal.call(asyncio.sleep, 0.05)
            assert not database.Bancos.find_one().get("reposicion")
            assert not any(p.requests for p in providers)
            return
        wait_for(lambda: sum(len(p.requests) for p in providers) == 1)
        job = database.Bancos.find_one()["reposicion"]
        assert job["cantidad"] == 30 and job["estado"] == "en_curso"
        published_before = list(database.BancoEjercicios.find().sort("secuencia", 1))
        attempts_before = list(database.Tests.find().sort("_id", 1))
        for c in clients:
            for _ in range(3):
                c.portal.call(c.app.state.refiller.request, bank_before["_id"], 10)
        assert database.Bancos.find_one()["reposicion"]["generacion_id"] == job["generacion_id"]
        assert sum(len(p.requests) for p in providers) == 1
        for c, provider in zip(clients, providers, strict=True):
            c.portal.call(provider.gate.set)
        wait_for(lambda: database.Bancos.find_one()["reposicion"]["estado"] == "completada")
        assert database.BancoEjercicios.count_documents({}) == total + 30
        new = list(
            database.BancoEjercicios.find({"secuencia": {"$gt": total}}).sort("secuencia", 1)
        )
        assert [q["secuencia"] for q in new] == list(range(total + 1, total + 31))
        assert len({q["enunciado"] for q in database.BancoEjercicios.find()}) == total + 30
        assert modality_counts([BankQuestionDocument.model_validate(q) for q in new]) == {
            "afirmativa": 21,
            "negativa": 6,
            "interrogativa": 3,
        }
        # El proveedor agrupa modalidades; el punto común de publicación las intercala.
        for offset in range(0, 30, 10):
            assert modality_counts(
                [BankQuestionDocument.model_validate(q) for q in new[offset : offset + 10]]
            ) == {"afirmativa": 7, "negativa": 2, "interrogativa": 1}
        assert (
            list(database.BancoEjercicios.find({"secuencia": {"$lte": total}}).sort("secuencia", 1))
            == published_before
        )
        assert list(database.Tests.find().sort("_id", 1)) == attempts_before
        assert list(database.Progreso.find().sort("_id", 1)) == progress
        assert sum(len(p.requests) for p in providers) == 1
        for i, c in enumerate(clients):
            old_ids = set(tests[i]["preguntas"][j]["pregunta_id"] for j in range(10))
            assert complete(c, headers[i], tests[i]).status_code == 200
            next_test = create(c, headers[i], topic).json()
            assert sequences(database, next_test) == list(range(11, 21))
            assert old_ids.isdisjoint(q["pregunta_id"] for q in next_test["preguntas"])
            assert complete(c, headers[i], next_test).status_code == 200
            third = create(c, headers[i], topic).json()
            assert sequences(database, third) == list(range(21, 31))
            assert complete(c, headers[i], third).status_code == 200
            fourth = create(c, headers[i], topic).json()
            assert sequences(database, fourth) == list(range(31, 41))
        assert [p["ultima_secuencia_reservada"] for p in database.Progreso.find()] == [40, 40]
        # Cursor 40 con banco 60 habilita legítimamente OTRO lote, nunca repite el anterior.
        assert (
            sum(r.generacion_id == str(job["generacion_id"]) for p in providers for r in p.requests)
            == 1
        )


def test_exhausted_while_pending_keeps_cursor_and_resumes_after_publication(api, registration):
    client, database, initial = api
    client.portal.call(activate, client.app.state.database, 4)
    provider = DemoProvider()
    provider.gate = asyncio.Event()
    settings = Settings(
        _env_file=None,
        **{**initial.model_dump(), "bank_refill_enabled": True, "bank_refill_poll_seconds": 0.01},
    )
    with TestClient(create_app(settings, provider)) as app:
        headers = login(app, registration, "exhausted@example.com")
        topic = str(database.Bancos.find_one()["tema_id"])
        app.portal.call(app.app.state.refiller.stop)
        first = create(app, headers, topic).json()
        assert database.Bancos.find_one()["reposicion"]["estado"] == "pendiente"
        assert not provider.requests
        assert complete(app, headers, first).status_code == 200
        progress = list(database.Progreso.find())
        exhausted = create(app, headers, topic)
        assert exhausted.status_code == 409 and exhausted.json()["codigo"] == "banco_reponiendo"
        assert exhausted.json()["detail"] == "Estamos preparando más preguntas para este tema"
        assert list(database.Progreso.find()) == progress
        assert not provider.requests and database.Tests.count_documents({}) == 1
        app.portal.call(app.app.state.refiller.start)
        wait_for(lambda: len(provider.requests) == 1)
        in_progress = create(app, headers, topic)
        assert in_progress.json()["codigo"] == "banco_reponiendo"
        assert list(database.Progreso.find()) == progress
        app.portal.call(provider.gate.set)
        wait_for(lambda: database.Bancos.find_one()["reposicion"]["estado"] == "completada")
        assert list(database.Progreso.find()) == progress
        new = create(app, headers, topic).json()
        assert sequences(database, new) == list(range(11, 21))
        assert len(provider.requests) == 1
