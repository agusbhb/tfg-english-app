import asyncio
import json
import ssl

import httpx
import pytest
from pydantic import ValidationError

from app.bank_contracts import GenerationOutput
from app.config import Settings
from app.generation import GenerationRequest
from app.openrouter import ENDPOINT, OpenRouterError, OpenRouterProvider
from app.prompts import GenerationParameters


def test_verified_runtime_ca_context_is_used(router_settings, generation_request, monkeypatch):
    from app import openrouter

    context = ssl.create_default_context()
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    monkeypatch.setattr(openrouter, "tls_context", lambda: context)
    original_client = httpx.AsyncClient
    calls = []

    def client(**kwargs):
        assert kwargs["verify"] is context
        assert kwargs["trust_env"] is False
        calls.append(kwargs)
        return original_client(**kwargs)

    monkeypatch.setattr(openrouter.httpx, "AsyncClient", client)
    provider = OpenRouterProvider(
        router_settings,
        httpx.MockTransport(
            lambda request: httpx.Response(200, json=successful_body(generation_request))
        ),
    )
    asyncio.run(provider.generate(generation_request))
    assert len(calls) == 1


def test_runtime_ca_context_verifies_certificates_and_hostname():
    from app.openrouter import tls_context

    context = tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


@pytest.fixture
def router_settings():
    return Settings(
        _env_file=None,
        mongodb_uri="mongodb://isolated.invalid/test",
        mongodb_db="trainer_test",
        jwt_secret="a" * 40,
        openrouter_api_key="sk-or-v1-test-unit-not-a-real-key",
    )


@pytest.fixture
def generation_request():
    return GenerationRequest(
        "only-test",
        "Plantilla completa renderizada desde MongoDB",
        GenerationParameters(
            codigo_tema="present-simple", tema="Present Simple", nivel_mcer="A1", cantidad=5
        ),
        GenerationOutput.model_json_schema(),
    )


def successful_body(request, model="anthropic/claude-haiku-4.5"):
    questions = [
        {
            "enunciado": f"Every day, student {index} ___ (play) football.",
            "respuesta_principal": "plays",
            "solucion": {"respuestas_aceptadas": ["plays"], "sensible_mayusculas": False},
            "distractores": ["play", "playing"],
        }
        for index in range(request.parameters.cantidad)
    ]
    return {
        "id": "gen-test-123",
        "model": model,
        "provider": "Anthropic",
        "usage": {"prompt_tokens": 120, "completion_tokens": 350, "is_byok": True, "cost": 0.0},
        "choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps({"preguntas": questions})}}
        ],
    }


def test_single_request_anthropic_only_byok_schema_and_safe_metadata(
    router_settings, generation_request
):
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == ENDPOINT
        assert (
            request.headers["authorization"]
            == "Bearer " + router_settings.openrouter_api_key.get_secret_value()
        )
        assert request.headers["x-openrouter-metadata"] == "enabled"
        body = json.loads(request.content)
        assert body["provider"] == {
            "only": ["anthropic"],
            "allow_fallbacks": False,
            "require_parameters": True,
        }
        assert body["model"] == router_settings.openrouter_model
        assert "models" not in body and "route" not in body
        assert body["messages"] == [{"role": "user", "content": generation_request.prompt}]
        assert body["response_format"]["json_schema"]["schema"] == generation_request.output_schema
        assert body["stream"] is False
        return httpx.Response(200, json=successful_body(generation_request))

    provider = OpenRouterProvider(router_settings, httpx.MockTransport(handler))
    result = asyncio.run(provider.generate(generation_request))
    assert len(calls) == 1 and len(result.payload["preguntas"]) == 5
    assert result.tokens_entrada == 120 and result.tokens_salida == 350
    assert result.id_llamada == "gen-test-123" and result.byok_confirmado is True
    assert result.coste_estimado_usd is None  # No inferir factura Anthropic de usage.cost=0.
    assert router_settings.openrouter_api_key.get_secret_value() not in str(result)


@pytest.mark.parametrize("status", [302, 400, 401, 402, 404, 429, 500, 503])
def test_http_errors_and_redirects_never_retry_or_print_secrets(
    router_settings, generation_request, status
):
    calls = []
    secret = router_settings.openrouter_api_key.get_secret_value()

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, json={"error": secret}, headers={"location": "https://other.invalid/steal"}
        )

    provider = OpenRouterProvider(router_settings, httpx.MockTransport(handler))
    with pytest.raises(OpenRouterError) as error:
        asyncio.run(provider.generate(generation_request))
    assert len(calls) == 1 and str(status) in str(error.value)
    assert secret not in str(error.value)


@pytest.mark.parametrize("failure", ["timeout", "network"])
def test_network_error_is_sanitized_without_retry(router_settings, generation_request, failure):
    calls = []

    def handler(request):
        calls.append(request)
        kind = httpx.ReadTimeout if failure == "timeout" else httpx.ConnectError
        raise kind(router_settings.openrouter_api_key.get_secret_value(), request=request)

    with pytest.raises(OpenRouterError) as error:
        asyncio.run(
            OpenRouterProvider(router_settings, httpx.MockTransport(handler)).generate(
                generation_request
            )
        )
    assert len(calls) == 1
    assert router_settings.openrouter_api_key.get_secret_value() not in str(error.value)


@pytest.mark.parametrize(
    "change",
    [
        "not_byok",
        "missing_byok",
        "conflicting_byok",
        "other_provider",
        "missing_provider",
        "other_model",
        "truncated",
        "refusal",
        "bad_json",
        "metadata",
        "wrong_count",
        "tokens",
        "embedded_error",
    ],
)
def test_untrusted_or_invalid_completion_is_rejected(router_settings, generation_request, change):
    data = successful_body(generation_request)
    if change == "not_byok":
        data["usage"]["is_byok"] = False
    elif change == "missing_byok":
        del data["usage"]["is_byok"]
    elif change == "conflicting_byok":
        data["openrouter_metadata"] = {"is_byok": False}
    elif change == "other_provider":
        data["provider"] = "Amazon Bedrock"
    elif change == "missing_provider":
        del data["provider"]
    elif change == "other_model":
        data["model"] = "openai/other"
    elif change == "truncated":
        data["choices"][0]["finish_reason"] = "length"
    elif change == "refusal":
        data["choices"][0]["message"]["refusal"] = "rejected"
    elif change == "bad_json":
        data["choices"][0]["message"]["content"] = "```json {} ```"
    elif change in ("metadata", "wrong_count"):
        payload = json.loads(data["choices"][0]["message"]["content"])
        if change == "metadata":
            payload["preguntas"][0]["banco_id"] = "not-allowed"
        else:
            payload["preguntas"].pop()
        data["choices"][0]["message"]["content"] = json.dumps(payload)
    elif change == "tokens":
        data["usage"]["prompt_tokens"] = True
    else:
        data["error"] = {"message": router_settings.openrouter_api_key.get_secret_value()}
    provider = OpenRouterProvider(
        router_settings, httpx.MockTransport(lambda request: httpx.Response(200, json=data))
    )
    with pytest.raises(OpenRouterError) as error:
        asyncio.run(provider.generate(generation_request))
    assert router_settings.openrouter_api_key.get_secret_value() not in str(error.value)


def test_optional_call_id_and_usage_and_selected_routing_metadata(
    router_settings, generation_request
):
    data = successful_body(generation_request)
    del data["id"], data["usage"], data["provider"]
    data["openrouter_metadata"] = {
        "is_byok": True,
        "endpoints": {"available": [{"provider": "Anthropic", "selected": True}]},
    }
    result = asyncio.run(
        OpenRouterProvider(
            router_settings, httpx.MockTransport(lambda request: httpx.Response(200, json=data))
        ).generate(generation_request)
    )
    assert result.byok_confirmado and result.id_llamada is None and result.tokens_entrada is None


def test_model_is_configurable_but_other_vendors_are_rejected(router_settings, generation_request):
    settings = Settings(
        _env_file=None,
        **{**router_settings.model_dump(), "openrouter_model": "anthropic/claude-sonnet-4.5"},
    )
    provider = OpenRouterProvider(
        settings,
        httpx.MockTransport(
            lambda request: httpx.Response(
                200, json=successful_body(generation_request, settings.openrouter_model)
            )
        ),
    )
    assert (
        asyncio.run(provider.generate(generation_request)).modelo_utilizado
        == settings.openrouter_model
    )
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None, **{**router_settings.model_dump(), "openrouter_model": "openai/other"}
        )


def test_missing_or_placeholder_key_fails_before_a_request(router_settings):
    for value in (None, "", "CHANGE_ME_OPENROUTER_API_KEY"):
        settings = Settings(
            _env_file=None, **{**router_settings.model_dump(), "openrouter_api_key": value}
        )
        with pytest.raises(OpenRouterError):
            OpenRouterProvider(settings)


@pytest.mark.integration
def test_preview_one_call_reads_existing_prompt_and_does_not_publish(api):
    from scripts.data_snapshot import snapshot
    from scripts.preview_openrouter import preview

    _, _, initial = api
    settings = Settings(
        _env_file=None,
        **{**initial.model_dump(), "openrouter_api_key": "sk-or-v1-test-unit-not-a-real-key"},
    )
    calls = []

    def handler(request):
        calls.append(request)
        assert (
            "Cantidad exacta de preguntas: 5"
            in json.loads(request.content)["messages"][0]["content"]
        )
        parameters = GenerationParameters(
            codigo_tema="present-simple", tema="Present Simple", nivel_mcer="A1", cantidad=5
        )
        return httpx.Response(
            200, json=successful_body(GenerationRequest("test", "", parameters, {}))
        )

    before = snapshot(settings)
    report = asyncio.run(
        preview(settings, OpenRouterProvider(settings, httpx.MockTransport(handler)))
    )
    assert len(calls) == 1 and len(report["preguntas"]) == 5
    assert report["id_llamada"] == "gen-test-123" and report["prompt_version"] == 2
    assert report["estado"] == "solo_revision_sin_publicar"
    assert snapshot(settings) == before
    assert settings.openrouter_api_key.get_secret_value() not in json.dumps(report)


@pytest.mark.integration
def test_configured_key_does_not_start_worker_with_default_disabled_flag(api, registration):
    from fastapi.testclient import TestClient

    from app.main import create_app

    _, _, initial = api
    settings = Settings(
        _env_file=None,
        **{**initial.model_dump(), "openrouter_api_key": "sk-or-v1-test-unit-not-a-real-key"},
    )
    with TestClient(create_app(settings)) as client:
        assert client.app.state.refiller.provider is None
        assert client.app.state.refiller.task is None
        assert client.post("/auth/register", json=registration).status_code == 201
        token = client.post(
            "/auth/login", json={k: registration[k] for k in ("email", "password")}
        ).json()["access_token"]
        headers = {"Authorization": "Bearer " + token}
        topic = client.get("/asignaturas").json()[0]["niveles"][0]["temas"][0]["tema_id"]
        first = client.post("/tests", json={"tema_id": topic}, headers=headers).json()
        assert (
            client.post(
                f"/tests/{first['id']}/respuestas",
                headers=headers,
                json={
                    "respuestas": [
                        {"pregunta_id": q["pregunta_id"], "respuesta_dada": ""}
                        for q in first["preguntas"]
                    ]
                },
            ).status_code
            == 200
        )
        response = client.post("/tests", json={"tema_id": topic}, headers=headers)
        assert response.status_code == 409 and response.json()["codigo"] == "reposicion_desactivada"
        assert "Estamos preparando" not in response.text


def test_manual_preview_refuses_enabled_automatic_refill(router_settings):
    from scripts.preview_openrouter import preview

    settings = Settings(
        _env_file=None, **{**router_settings.model_dump(), "bank_refill_enabled": True}
    )
    with pytest.raises(ValueError, match="BANK_REFILL_ENABLED=false"):
        asyncio.run(preview(settings, None))  # Antes de MongoDB o del proveedor.


@pytest.mark.integration
def test_factory_selects_openrouter_only_when_explicitly_enabled(api, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    settings = Settings(
        _env_file=None,
        **{
            **api[2].model_dump(),
            "openrouter_api_key": "sk-or-v1-test-unit-not-a-real-key",
            "bank_refill_enabled": True,
        },
    )
    calls = []

    def reject_network(request):
        calls.append(request)
        raise AssertionError("No se esperaba una llamada al proveedor en el arranque")

    provider = OpenRouterProvider(settings, httpx.MockTransport(reject_network))
    monkeypatch.setattr(main, "OpenRouterProvider", lambda configuration: provider)
    with TestClient(main.create_app(settings)) as client:
        assert client.app.state.refiller.provider is provider
        assert client.app.state.refiller.task is not None
    assert not calls
