import asyncio
import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.errors import ConnectionFailure

from app.bank_contracts import BankMetadataDocument, BankQuestionDocument
from app.config import Settings
from app.generation import GenerationResponse
from app.main import create_app
from app.models import AIModel

pytestmark = pytest.mark.integration


def wait_for(condition, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        result = condition()
        if result:
            return result
        time.sleep(0.01)
    raise AssertionError("La reposición no alcanzó el estado esperado")


class SimulatedProvider:
    """Única implementación simulada: exclusivamente en tests, nunca importada por app."""

    model = AIModel(proveedor="simulado", modelo="prueba-local")

    def __init__(self, hold=True):
        self.hold = hold
        self.started = threading.Event()
        self.gate = None
        self.requests = []
        self.failures = 0
        self.transform = lambda payload: payload

    async def release(self):
        self.hold = False
        if self.gate is not None:
            self.gate.set()

    async def generate(self, request):
        self.requests.append(request)
        self.gate = asyncio.Event()
        self.started.set()
        if self.hold:
            await self.gate.wait()
        if self.failures:
            self.failures -= 1
            raise RuntimeError("secreto-del-proveedor-no-publicar")
        payload = {
            "preguntas": [
                {
                    "enunciado": (
                        f"Every morning, student {i + 100 * len(self.requests)} "
                        "___ (play) football."
                    ),
                    "respuesta_principal": "plays",
                    "solucion": {"respuestas_aceptadas": ["plays"], "sensible_mayusculas": False},
                    "distractores": ["play", "playing"],
                }
                for i in range(request.parameters.cantidad)
            ]
        }
        return GenerationResponse(
            payload=self.transform(payload),
            tokens_entrada=123,
            tokens_salida=456,
        )


@pytest.fixture
def refill_app(api, registration):
    _, db, initial = api

    @contextmanager
    def factory(provider, email="refill@example.com", **overrides):
        settings = Settings(
            _env_file=None,
            **{
                **initial.model_dump(),
                "bank_refill_poll_seconds": 0.01,
                "bank_refill_timeout_seconds": 5,
                "bank_refill_lease_seconds": 10,
                **overrides,
            },
        )
        app = create_app(settings, generation_provider=provider)
        with TestClient(app) as client:
            data = {**registration, "email": email}
            assert client.post("/auth/register", json=data).status_code == 201
            token = client.post(
                "/auth/login", json={k: data[k] for k in ("email", "password")}
            ).json()["access_token"]
            yield (
                client,
                db,
                {"Authorization": f"Bearer {token}"},
                str(db.Bancos.find_one()["tema_id"]),
            )

    return factory


def create(client, headers, topic):
    return client.post("/tests", json={"tema_id": topic}, headers=headers)


def complete(client, headers, test):
    return client.post(
        f"/tests/{test['id']}/respuestas",
        headers=headers,
        json={
            "respuestas": [
                {"pregunta_id": q["pregunta_id"], "respuesta_dada": ""} for q in test["preguntas"]
            ]
        },
    )


def state(db, value):
    return db.Bancos.find_one({"reposicion.estado": value})


@pytest.mark.parametrize(
    "threshold,total,requested",
    [(20, 31, False), (20, 30, True), (5, 16, False), (5, 15, True), (0, 10, True)],
)
def test_configurable_threshold_after_confirmed_reservation(
    refill_app, publish_bank, threshold, total, requested
):
    publish_bank(total)
    provider = SimulatedProvider()
    with refill_app(provider, bank_refill_threshold=threshold, bank_refill_batch_size=2) as (
        client,
        db,
        headers,
        topic,
    ):
        response = create(client, headers, topic)
        assert response.status_code == 201
        assert db.Progreso.find_one()["ultima_secuencia_reservada"] == 10
        job = db.Bancos.find_one().get("reposicion")
        assert bool(job) is requested
        if requested:
            assert provider.started.wait(2)
            assert job["cantidad"] == 2
            assert db.BancoEjercicios.count_documents({}) == total
            client.portal.call(provider.release)
            wait_for(lambda: state(db, "completada"))
            assert db.Bancos.find_one()["publicado_hasta"] == total + 2
        else:
            assert not provider.requests


def test_request_does_not_wait_and_continues_from_cursor_after_publication(refill_app):
    provider = SimulatedProvider()
    with refill_app(provider) as (client, db, headers, topic):
        response = create(client, headers, topic)
        assert response.status_code == 201
        assert provider.started.wait(2)
        request = provider.requests[0]
        assert request.parameters.cantidad == 30
        assert request.parameters.idioma_objetivo == "Inglés"
        assert (
            "Idioma objetivo: Inglés" in request.prompt
            and "{idioma_objetivo}" not in request.prompt
        )
        assert db.BancoEjercicios.count_documents({}) == 10  # Proveedor aún bloqueado.
        test = response.json()
        original = db.Tests.find_one()
        before = db.Progreso.find_one()
        assert complete(client, headers, test).status_code == 200
        exhausted = create(client, headers, topic)
        assert exhausted.status_code == 409
        assert exhausted.json()["codigo"] == "banco_reponiendo"
        assert exhausted.json()["detail"] == "Estamos preparando más preguntas para este tema"
        assert db.Progreso.find_one() == before
        client.portal.call(provider.release)
        bank = wait_for(lambda: state(db, "completada"))
        assert bank["publicado_hasta"] == 40
        metrics = bank["ultima_generacion"]
        assert metrics["prompt_version"] == 2
        assert metrics["preguntas_solicitadas"] == metrics["preguntas_validas"] == 30
        assert metrics["tokens_entrada"] == 123 and metrics["coste_estimado_usd"] is None
        new = create(client, headers, topic)
        assert new.status_code == 201
        stored = db.Tests.find_one({"_id": ObjectId(new.json()["id"])})
        assert [q["secuencia"] for q in stored["origenes_preguntas"]] == list(range(11, 21))
        assert (
            db.Tests.find_one({"_id": original["_id"]})["preguntas_generadas"]
            == original["preguntas_generadas"]
        )
        question = db.BancoEjercicios.find_one({"secuencia": 11})
        assert BankQuestionDocument.model_validate(question).generacion.prompt_version == 2
        assert question["validacion"] == {
            "estructural": "validada",
            "pedagogica": "automatica",
            "apta_multiple_choice": False,
        }
        assert len(provider.requests) == 1


def test_two_students_two_workers_request_only_one_generation(refill_app):
    one, two = SimulatedProvider(), SimulatedProvider()
    with (
        refill_app(one, email="one@example.com") as first,
        refill_app(two, email="two@example.com") as second,
    ):
        client, db, headers, topic = first
        barrier = threading.Barrier(2)

        def reserve(values):
            application, _, auth, selected = values
            barrier.wait(timeout=3)
            return create(application, auth, selected)

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(reserve, (first, second)))
        assert all(response.status_code == 201 for response in responses)
        wait_for(lambda: len(one.requests) + len(two.requests) == 1)
        generation = db.Bancos.find_one()["reposicion"]["generacion_id"]
        assert db.Tests.count_documents({}) == db.Progreso.count_documents({}) == 2
        client.portal.call(one.release)
        second[0].portal.call(two.release)
        bank = wait_for(lambda: state(db, "completada"))
        assert bank["reposicion"]["generacion_id"] == generation
        assert db.BancoEjercicios.count_documents({}) == bank["publicado_hasta"] == 40
        assert len(one.requests) + len(two.requests) == 1


def test_generation_failure_is_distinct_sanitized_and_does_not_change_progress(refill_app):
    provider = SimulatedProvider(hold=False)
    provider.failures = 1
    with refill_app(provider, bank_refill_max_attempts=1) as (client, db, headers, topic):
        first = create(client, headers, topic).json()
        bank = wait_for(lambda: state(db, "error"))
        progress = db.Progreso.find_one()
        assert bank["publicado_hasta"] == db.BancoEjercicios.count_documents({}) == 10
        assert "secreto-del-proveedor" not in str(bank)
        assert bank["ultima_generacion"]["preguntas_validas"] == 0
        assert complete(client, headers, first).status_code == 200
        failed = create(client, headers, topic)
        assert failed.status_code == 409 and failed.json()["codigo"] == "reposicion_fallida"
        assert "Estamos preparando" not in failed.text
        assert db.Progreso.find_one() == progress
        assert len(provider.requests) == 1


def test_retry_keeps_generation_identity_and_publishes_only_once(refill_app):
    provider = SimulatedProvider(hold=False)
    provider.failures = 1
    with refill_app(provider, bank_refill_retry_seconds=0) as (client, db, headers, topic):
        assert create(client, headers, topic).status_code == 201
        bank = wait_for(lambda: state(db, "completada"))
        assert bank["reposicion"]["intentos"] == 2
        assert provider.requests[0].generacion_id == provider.requests[1].generacion_id
        assert db.BancoEjercicios.count_documents({}) == bank["publicado_hasta"] == 40
        assert db.Progreso.find_one()["ultima_secuencia_reservada"] == 10


def test_no_provider_never_reports_stale_job_as_preparing(refill_app):
    with refill_app(None) as (client, db, headers, topic):
        first = create(client, headers, topic).json()
        assert client.app.state.refiller.task is None
        assert db.Bancos.find_one()["reposicion"] is None
        stale = {
            "generacion_id": ObjectId(),
            "cantidad": 30,
            "estado": "pendiente",
            "creado_en": datetime.now(UTC),
        }
        db.Bancos.update_one({}, {"$set": {"reposicion": stale}})
        saved = db.Bancos.find_one()["reposicion"]
        assert complete(client, headers, first).status_code == 200
        response = create(client, headers, topic)
        assert (
            response.status_code == 409 and response.json()["codigo"] == "proveedor_no_configurado"
        )
        assert "Estamos preparando" not in response.text
        assert db.Bancos.find_one()["reposicion"] == saved


@pytest.mark.parametrize("invalid", ["count", "gap", "answer", "metadata", "duplicate"])
def test_malformed_generation_is_not_published(refill_app, invalid):
    provider = SimulatedProvider(hold=False)

    def corrupt(payload):
        if invalid == "count":
            payload["preguntas"].pop()
        elif invalid == "gap":
            payload["preguntas"][0]["enunciado"] = "She ___ and ___ every day."
        elif invalid == "answer":
            payload["preguntas"][0]["distractores"][0] = "plays"
        elif invalid == "metadata":
            payload["preguntas"][0]["banco_id"] = str(ObjectId())
        else:
            payload["preguntas"][1] = copy.deepcopy(payload["preguntas"][0])
        return payload

    provider.transform = corrupt
    with refill_app(provider, bank_refill_max_attempts=1) as (client, db, headers, topic):
        assert create(client, headers, topic).status_code == 201
        bank = wait_for(lambda: state(db, "error"))
        assert bank["reposicion"]["error_codigo"] == "contenido_invalido"
        assert db.BancoEjercicios.count_documents({}) == bank["publicado_hasta"] == 10
        assert db.Progreso.find_one()["ultima_secuencia_reservada"] == 10


def test_existing_questions_are_excluded_and_accepted_sequence_has_no_gaps(refill_app, api):
    provider = SimulatedProvider(hold=False)
    existing = api[1].BancoEjercicios.find_one({"secuencia": 1})

    def duplicate(payload):
        if len(provider.requests) > 1:
            return payload
        payload["preguntas"][0] = {
            "enunciado": "  " + existing["enunciado"].upper() + "  ",
            "respuesta_principal": "play",
            "solucion": existing["solucion"],
            "distractores": ["plays", "playing"],
        }
        return payload

    provider.transform = duplicate
    with refill_app(provider) as (client, db, headers, topic):
        assert create(client, headers, topic).status_code == 201
        bank = wait_for(lambda: state(db, "completada"))
        assert bank["publicado_hasta"] == db.BancoEjercicios.count_documents({}) == 40
        assert bank["ultima_generacion"]["preguntas_validas"] == 30
        assert [r.parameters.cantidad for r in provider.requests] == [30, 1]
        assert [q["secuencia"] for q in db.BancoEjercicios.find().sort("secuencia", 1)] == list(
            range(1, 41)
        )


def test_publication_failure_rolls_back_and_retry_has_no_duplicate_questions(
    refill_app, monkeypatch
):
    provider = SimulatedProvider()
    with refill_app(provider, bank_refill_retry_seconds=60) as (client, db, headers, topic):
        assert create(client, headers, topic).status_code == 201
        assert provider.started.wait(2)
        collection_type = type(client.app.state.database.Bancos)
        original = collection_type.insert_many

        async def failing_insert(collection, *args, **kwargs):
            result = await original(collection, *args, **kwargs)
            if collection.name == "BancoEjercicios":
                raise ConnectionFailure("Fallo de prueba después de insertar")
            return result

        with monkeypatch.context() as patch:
            patch.setattr(collection_type, "insert_many", failing_insert)
            client.portal.call(provider.release)
            wait_for(lambda: state(db, "error"))
        assert (
            db.Bancos.find_one()["publicado_hasta"] == db.BancoEjercicios.count_documents({}) == 10
        )
        assert db.Progreso.find_one()["ultima_secuencia_reservada"] == 10
        db.Bancos.update_one(
            {}, {"$set": {"reposicion.reintento_en": datetime.now(UTC) - timedelta(seconds=1)}}
        )
        bank = wait_for(lambda: state(db, "completada"))
        assert bank["publicado_hasta"] == db.BancoEjercicios.count_documents({}) == 40
        assert bank["reposicion"]["intentos"] == 2


def test_late_worker_cannot_publish_after_another_worker_reclaims_lease(refill_app):
    slow = SimulatedProvider()
    with refill_app(slow, email="slow@example.com") as (client, db, headers, topic):
        assert create(client, headers, topic).status_code == 201
        assert slow.started.wait(2)
        job_id = db.Bancos.find_one()["reposicion"]["generacion_id"]
        db.Bancos.update_one(
            {}, {"$set": {"reposicion.lease_hasta": datetime.now(UTC) - timedelta(seconds=1)}}
        )
        fast = SimulatedProvider(hold=False)
        with refill_app(fast, email="fast@example.com"):
            bank = wait_for(lambda: state(db, "completada"))
            client.portal.call(slow.release)
            # Barrera en el loop para que el worker lento reciba su resultado.
            client.portal.call(asyncio.sleep, 0.05)
            assert len(fast.requests) == len(slow.requests) == 1
            assert db.BancoEjercicios.count_documents({}) == bank["publicado_hasta"] == 40
            assert db.Bancos.find_one()["reposicion"]["generacion_id"] == job_id
            assert db.Progreso.find_one()["ultima_secuencia_reservada"] == 10


def test_timeout_is_a_failure_not_a_permanent_preparing_state(refill_app):
    provider = SimulatedProvider()
    with refill_app(
        provider,
        bank_refill_timeout_seconds=0.05,
        bank_refill_lease_seconds=2,
        bank_refill_max_attempts=1,
    ) as (client, db, headers, topic):
        assert create(client, headers, topic).status_code == 201
        bank = wait_for(lambda: state(db, "error"))
        assert bank["reposicion"]["error_codigo"] == "proveedor_timeout"
        assert bank["reposicion"]["reintento_en"] is None
        assert db.BancoEjercicios.count_documents({}) == 10


def test_enqueue_failure_does_not_hide_a_confirmed_attempt(refill_app, monkeypatch):
    provider = SimulatedProvider()
    with refill_app(provider) as (client, db, headers, topic):

        async def unavailable(*args):
            raise ConnectionFailure("Fallo inyectado al encolar")

        with monkeypatch.context() as patch:
            patch.setattr(client.app.state.refiller, "request", unavailable)
            response = create(client, headers, topic)
            assert response.status_code == 201
        assert db.Tests.count_documents({}) == 1
        assert complete(client, headers, response.json()).status_code == 200
        assert create(client, headers, topic).json()["codigo"] == "banco_reponiendo"
        client.portal.call(provider.release)
        wait_for(lambda: state(db, "completada"))


def test_unauthenticated_or_invalid_topic_does_not_request_generation(refill_app):
    provider = SimulatedProvider()
    with refill_app(provider) as (client, db, headers, topic):
        assert create(client, {}, topic).status_code == 401
        assert create(client, headers, str(ObjectId())).status_code == 422
        assert db.Progreso.count_documents({}) == db.Tests.count_documents({}) == 0
        assert db.Bancos.find_one()["reposicion"] is None
        assert provider.requests == []


def test_missing_or_ambiguous_prompt_fails_without_calling_provider(refill_app):
    provider = SimulatedProvider(hold=False)
    with refill_app(provider, bank_refill_max_attempts=1) as (client, db, headers, topic):
        db.Prompts.update_many({}, {"$set": {"activo": False}})
        assert create(client, headers, topic).status_code == 201
        wait_for(lambda: state(db, "error"))
        assert provider.requests == []
        assert db.BancoEjercicios.count_documents({}) == 10
        assert BankMetadataDocument.model_validate(db.Bancos.find_one()).publicado_hasta == 10


def test_pending_job_survives_restart_without_losing_progress(refill_app):
    provider = SimulatedProvider()
    with refill_app(provider, email="before@example.com") as (client, db, headers, topic):
        test = create(client, headers, topic).json()
        assert provider.started.wait(2)
        job = db.Bancos.find_one()["reposicion"]
        before = db.Progreso.find_one()
    assert state(db, "en_curso")
    db.Bancos.update_one(
        {}, {"$set": {"reposicion.lease_hasta": datetime.now(UTC) - timedelta(seconds=1)}}
    )
    recovered = SimulatedProvider(hold=False)
    with refill_app(recovered, email="after@example.com") as (client, db, _, _):
        bank = wait_for(lambda: state(db, "completada"))
        assert bank["reposicion"]["generacion_id"] == job["generacion_id"]
        assert bank["reposicion"]["intentos"] == 2
        assert db.Progreso.find_one() == before
        assert (
            client.get(f"/tests/{test['id']}", headers=headers).json()["preguntas"]
            == test["preguntas"]
        )
        assert db.BancoEjercicios.count_documents({}) == 40


def test_unknown_publication_commit_is_retried_without_duplicate_batch(refill_app, monkeypatch):
    provider = SimulatedProvider()
    with refill_app(provider) as (client, db, headers, topic):
        assert create(client, headers, topic).status_code == 201
        assert provider.started.wait(2)
        original = AsyncClientSession.commit_transaction
        calls = 0

        async def uncertain_commit(session):
            nonlocal calls
            await original(session)
            calls += 1
            if calls == 1:
                error = ConnectionFailure("Respuesta de commit perdida en la prueba")
                error._add_error_label("UnknownTransactionCommitResult")
                raise error

        with monkeypatch.context() as patch:
            patch.setattr(AsyncClientSession, "commit_transaction", uncertain_commit)
            client.portal.call(provider.release)
            wait_for(lambda: calls >= 2)
            wait_for(lambda: state(db, "completada"))
        assert db.BancoEjercicios.count_documents({}) == 40
        assert db.Bancos.find_one()["publicado_hasta"] == 40
        assert db.Progreso.find_one()["ultima_secuencia_reservada"] == 10
        assert len(provider.requests) == 1


def test_fully_duplicate_batch_is_not_published(refill_app, api):
    provider = SimulatedProvider(hold=False)
    originals = list(api[1].BancoEjercicios.find().sort("secuencia", 1))

    def duplicates(payload):
        payload["preguntas"] = [
            {
                "enunciado": question["enunciado"],
                "respuesta_principal": question["respuesta_principal"],
                "solucion": question["solucion"],
                "distractores": ["never-accepted", "also-wrong"],
            }
            for question in originals
        ]
        return payload

    provider.transform = duplicates
    with refill_app(provider, bank_refill_batch_size=10, bank_refill_max_attempts=1) as (
        client,
        db,
        headers,
        topic,
    ):
        assert create(client, headers, topic).status_code == 201
        bank = wait_for(lambda: state(db, "error"))
        assert bank["reposicion"]["error_codigo"] == "sin_preguntas_nuevas"
        assert bank["publicado_hasta"] == db.BancoEjercicios.count_documents({}) == 10


def test_refill_configuration_defaults_and_invalid_lease(api):
    settings = api[2]
    assert settings.bank_refill_threshold == 20 and settings.bank_refill_batch_size == 30
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="lease"):
        Settings(_env_file=None, **{**settings.model_dump(), "bank_refill_lease_seconds": 30})
