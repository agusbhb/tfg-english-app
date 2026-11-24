"""Factoría operativa y un ciclo controlado; HTTP simulado, MongoDB aislado."""

import asyncio
import json
import ssl

import httpx
import pytest
from fastapi.testclient import TestClient
from test_anthropic import body
from test_modalities import question
from test_refill_cursor_integration import login
from test_replenishment import complete, create, wait_for

from app import main
from app.anthropic import AnthropicError, AnthropicProvider
from app.config import Settings
from app.http_tls import tls_context
from scripts.activate_generation_prompt import activate
from scripts.refill_once import run_once


def configured(initial, **changes):
    return Settings(
        _env_file=None,
        **{
            **initial.model_dump(),
            "anthropic_api_key": "sk-ant-only-test-not-a-real-key",
            **changes,
        },
    )


def test_tls_still_verifies_certificates_and_hostnames():
    context = tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


@pytest.mark.parametrize("key", [None, "", "CHANGE_ME_ANTHROPIC_API_KEY"])
def test_enabled_factory_requires_anthropic_key_without_fallback(key, monkeypatch):
    # Incluso un antiguo secreto OpenRouter no sirve como alternativa.
    monkeypatch.setenv("OPENROUTER_API_KEY", "obsolete-only-test-not-a-real-key")
    settings = Settings(
        _env_file=None,
        mongodb_uri="mongodb://isolated.invalid/test",
        jwt_secret="a" * 40,
        bank_refill_enabled=True,
        anthropic_api_key=key,
    )
    with pytest.raises(AnthropicError, match="ANTHROPIC_API_KEY") as error:
        main.create_app(settings)
    assert "obsolete-only-test" not in str(error.value)


@pytest.mark.integration
def test_key_alone_never_starts_worker_or_reports_preparing(api, registration):
    settings = configured(api[2])
    with TestClient(main.create_app(settings)) as client:
        headers = login(client, registration, "disabled@example.com")
        topic = str(api[1].Bancos.find_one()["tema_id"])
        attempt = create(client, headers, topic).json()
        assert client.app.state.refiller.provider is None
        assert client.app.state.refiller.task is None
        assert complete(client, headers, attempt).status_code == 200
        exhausted = create(client, headers, topic)
        assert exhausted.json()["codigo"] == "reposicion_desactivada"
        assert "Estamos preparando" not in exhausted.text
        assert api[1].Bancos.find_one()["reposicion"] is None


@pytest.mark.integration
@pytest.mark.parametrize("failure", [False, True])
def test_factory_anthropic_worker_publishes_exactly_thirty_or_nothing(
    api, registration, monkeypatch, failure
):
    initial_client, db, initial = api
    initial_client.portal.call(activate, initial_client.app.state.database, 4)
    settings = configured(
        initial,
        bank_refill_enabled=True,
        bank_refill_poll_seconds=0.01,
        bank_refill_max_attempts=1,
    )
    gate = asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(request)
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        assert request.headers["x-api-key"] == settings.anthropic_api_key.get_secret_value()
        assert "authorization" not in request.headers
        sent = json.loads(request.content)
        assert sent["model"] == "claude-haiku-4-5-20251001"
        assert "Cantidad exacta de preguntas: 30" in sent["messages"][0]["content"]
        await gate.wait()
        if failure:
            return httpx.Response(503, text=settings.anthropic_api_key.get_secret_value())
        data = body(settings)
        questions = [
            question(mode, index)
            for mode, count in (("afirmativa", 21), ("negativa", 6), ("interrogativa", 3))
            for index in range(count)
        ]
        data["content"][0]["text"] = json.dumps({"preguntas": questions})
        return httpx.Response(200, json=data)

    provider = AnthropicProvider(settings, httpx.MockTransport(handler))
    selected = []

    def factory(configuration):
        selected.append(configuration)
        return provider

    monkeypatch.setattr(main, "AnthropicProvider", factory)
    with TestClient(main.create_app(settings)) as client:
        assert selected == [settings]
        assert client.app.state.refiller.provider is provider
        headers = [login(client, registration, f"direct{i}@example.com") for i in range(2)]
        topic = str(db.Bancos.find_one()["tema_id"])
        for auth in headers:
            assert create(client, auth, topic).status_code == 201
        wait_for(lambda: len(calls) == 1)
        before = list(db.Progreso.find().sort("_id", 1))
        assert [p["ultima_secuencia_reservada"] for p in before] == [10, 10]
        client.portal.call(gate.set)
        state = "error" if failure else "completada"
        wait_for(lambda: db.Bancos.find_one()["reposicion"]["estado"] == state)
        bank = db.Bancos.find_one()
        assert (
            db.BancoEjercicios.count_documents({})
            == bank["publicado_hasta"]
            == (10 if failure else 40)
        )
        assert list(db.Progreso.find().sort("_id", 1)) == before
        assert len(calls) == 1
        assert settings.anthropic_api_key.get_secret_value() not in str(bank)
        if not failure:
            assert [q["secuencia"] for q in db.BancoEjercicios.find().sort("secuencia", 1)] == list(
                range(1, 41)
            )
            assert bank["ultima_generacion"]["modelo"]["proveedor"] == "anthropic"
            assert bank["ultima_generacion"]["tokens_entrada"] == 120
            assert bank["ultima_generacion"]["tokens_salida"] == 350


@pytest.mark.integration
@pytest.mark.parametrize("failure", [False, True])
def test_controlled_cycle_dry_run_then_single_job_preserves_cursors(api, registration, failure):
    from demo_refill_app import DemoProvider

    client, db, initial = api
    client.portal.call(activate, client.app.state.database, 4)
    topic = str(db.Bancos.find_one()["tema_id"])
    for i in range(2):
        auth = login(client, registration, f"once{i}@example.com")
        assert create(client, auth, topic).status_code == 201
    progress = list(db.Progreso.find().sort("_id", 1))
    settings = configured(initial, bank_refill_max_attempts=1)
    user = progress[0]["usuario_id"]
    provider = DemoProvider()
    if failure:

        async def fail(request):
            provider.requests.append(request)
            raise AnthropicError("Anthropic devolvió HTTP 503; no se ha reintentado")

        provider.generate = fail
    report = asyncio.run(run_once(settings, user, provider=provider))
    assert report["ejecutado"] is False and report["restantes"] == 0
    assert not provider.requests and db.Bancos.find_one()["reposicion"] is None
    report = asyncio.run(run_once(settings, user, execute=True, provider=provider))
    assert report["estado"] == ("error" if failure else "completada")
    assert json.loads(json.dumps(report))["metricas"]["prompt_version"] == 4
    assert len(provider.requests) == 1
    assert list(db.Progreso.find().sort("_id", 1)) == progress
    assert db.BancoEjercicios.count_documents({}) == (10 if failure else 40)
    if not failure:
        report = asyncio.run(run_once(settings, user, execute=True, provider=provider))
        assert report["ejecutado"] is False and report["restantes"] == 30
        assert len(provider.requests) == 1


@pytest.mark.integration
def test_preview_cannot_run_with_automatic_refill_enabled(api):
    from scripts.preview_generation import preview

    with pytest.raises(ValueError):
        asyncio.run(preview(configured(api[2], bank_refill_enabled=True), None, 5))
