import asyncio
import copy
import json
import ssl
from dataclasses import replace

import httpx
import pytest
from pydantic import ValidationError

from app.anthropic import ENDPOINT, AnthropicError, AnthropicProvider, anthropic_schema
from app.bank_contracts import GenerationOutput
from app.config import Settings
from app.generation import GenerationRequest
from app.prompts import GenerationParameters


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        mongodb_uri="mongodb://isolated.invalid/test",
        jwt_secret="a" * 40,
        anthropic_api_key="sk-ant-only-test-not-a-real-key",
    )


@pytest.fixture
def generation():
    return GenerationRequest(
        "only-test",
        "Prompt completo leído de MongoDB",
        GenerationParameters(
            codigo_tema="present-simple", tema="Present Simple", nivel_mcer="A1", cantidad=5
        ),
        GenerationOutput.model_json_schema(),
    )


def body(settings):
    questions = [
        {
            "enunciado": f"Every day, student {index} ___ (play) football.",
            "respuesta_principal": "plays",
            "solucion": {"respuestas_aceptadas": ["plays"], "sensible_mayusculas": False},
            "distractores": ["play", "playing"],
        }
        for index in range(5)
    ]
    return {
        "id": "msg_test_123",
        "type": "message",
        "role": "assistant",
        "model": settings.anthropic_model,
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": json.dumps({"preguntas": questions})}],
        "usage": {"input_tokens": 120, "output_tokens": 350},
    }


def test_one_request_tls_headers_schema_and_metrics(settings, generation, monkeypatch):
    from app import anthropic

    original = httpx.AsyncClient
    clients = []
    calls = []

    def client(**kwargs):
        assert kwargs["verify"].verify_mode == ssl.CERT_REQUIRED
        assert kwargs["verify"].check_hostname is True
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        clients.append(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(anthropic.httpx, "AsyncClient", client)

    def handler(request):
        calls.append(request)
        assert str(request.url) == ENDPOINT
        assert request.headers["x-api-key"] == settings.anthropic_api_key.get_secret_value()
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in request.headers
        sent = json.loads(request.content)
        assert sent["model"] == settings.anthropic_model
        assert sent["max_tokens"] == settings.anthropic_max_tokens
        assert sent["stream"] is False
        assert sent["messages"] == [{"role": "user", "content": generation.prompt}]
        assert sent["output_config"]["format"] == {
            "type": "json_schema",
            "schema": anthropic_schema(generation.output_schema),
        }
        return httpx.Response(200, json=body(settings))

    result = asyncio.run(
        AnthropicProvider(settings, httpx.MockTransport(handler)).generate(generation)
    )
    assert len(calls) == len(clients) == 1
    assert len(result.payload["preguntas"]) == 5
    assert result.id_llamada == "msg_test_123"
    assert (result.tokens_entrada, result.tokens_salida) == (120, 350)
    assert result.proveedor_utilizado == "Anthropic"
    assert result.byok_confirmado is None and result.coste_estimado_usd is None
    assert settings.anthropic_api_key.get_secret_value() not in str(result)


def test_schema_adapter_preserves_original_and_describes_constraints(generation):
    original = copy.deepcopy(generation.output_schema)
    adapted = anthropic_schema(original)
    question = adapted["$defs"]["GeneratedBankQuestion"]["properties"]
    assert "maxItems" not in question["distractores"]
    assert "minItems" not in question["distractores"]
    assert '"minItems": 2' in question["distractores"]["description"]
    assert "maxLength" not in question["enunciado"]
    assert adapted["additionalProperties"] is False
    assert original == generation.output_schema


@pytest.mark.parametrize("status", [302, 400, 401, 402, 429, 500])
def test_http_errors_do_not_retry_redirect_or_expose_secrets(settings, generation, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            json={"error": settings.anthropic_api_key.get_secret_value()},
            headers={"location": "https://other.invalid"},
        )

    with pytest.raises(AnthropicError, match=f"HTTP {status}") as error:
        asyncio.run(AnthropicProvider(settings, httpx.MockTransport(handler)).generate(generation))
    assert len(calls) == 1
    assert settings.anthropic_api_key.get_secret_value() not in str(error.value)


@pytest.mark.parametrize("kind", [httpx.ReadTimeout, httpx.ConnectError])
def test_network_errors_are_sanitized_and_not_retried(settings, generation, kind):
    calls = []

    def handler(request):
        calls.append(request)
        raise kind(settings.anthropic_api_key.get_secret_value(), request=request)

    with pytest.raises(AnthropicError) as error:
        asyncio.run(AnthropicProvider(settings, httpx.MockTransport(handler)).generate(generation))
    assert len(calls) == 1
    assert settings.anthropic_api_key.get_secret_value() not in str(error.value)


@pytest.mark.parametrize(
    "change",
    [
        "truncated",
        "refusal",
        "model",
        "json",
        "quantity",
        "metadata",
        "distractors",
        "length",
        "tokens",
        "block",
    ],
)
def test_invalid_output_is_rejected_by_original_contract(settings, generation, change):
    data = body(settings)
    if change in ("truncated", "refusal"):
        data["stop_reason"] = "max_tokens" if change == "truncated" else "refusal"
    elif change == "model":
        data["model"] = "unexpected"
    elif change == "json":
        data["content"][0]["text"] = "not JSON"
    elif change == "tokens":
        data["usage"]["input_tokens"] = True
    elif change == "block":
        data["content"][0]["type"] = "tool_use"
    else:
        payload = json.loads(data["content"][0]["text"])
        if change == "quantity":
            payload["preguntas"].pop()
        elif change == "metadata":
            payload["preguntas"][0]["banco_id"] = "invented"
        elif change == "distractors":
            payload["preguntas"][0]["distractores"] = ["plays", "playing"]
        else:
            payload["preguntas"][0]["respuesta_principal"] = "x" * 201
        data["content"][0]["text"] = json.dumps(payload)
    with pytest.raises(AnthropicError):
        asyncio.run(
            AnthropicProvider(
                settings, httpx.MockTransport(lambda request: httpx.Response(200, json=data))
            ).generate(generation)
        )


def test_key_required_and_model_configurable(settings, generation):
    for key in (None, "", "CHANGE_ME_ANTHROPIC_API_KEY"):
        with pytest.raises(AnthropicError):
            AnthropicProvider(
                Settings(
                    _env_file=None,
                    **{
                        **settings.model_dump(),
                        "anthropic_api_key": key,
                    },
                )
            )
    alternate = Settings(
        _env_file=None,
        **{
            **settings.model_dump(),
            "anthropic_model": "claude-sonnet-4-5-20250929",
        },
    )
    provider = AnthropicProvider(
        alternate, httpx.MockTransport(lambda request: httpx.Response(200, json=body(alternate)))
    )
    assert asyncio.run(provider.generate(generation)).modelo_utilizado == alternate.anthropic_model
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**settings.model_dump(), "anthropic_model": "other/model"})


def test_cli_selects_direct_adapter_without_changing_default(settings, monkeypatch, capsys):
    from scripts import preview_openrouter as cli

    monkeypatch.setattr(cli, "Settings", lambda: settings)
    calls = []

    async def preview(configuration, provider, quantity, complete_batch=False):
        calls.append((configuration, provider, quantity))
        return {"estado": "solo_revision_sin_publicar"}

    monkeypatch.setattr(cli, "preview", preview)
    monkeypatch.setattr("sys.argv", ["preview", "--proveedor", "anthropic", "--cantidad", "5"])
    cli.main()
    assert isinstance(calls[0][1], AnthropicProvider) and calls[0][2] == 5
    assert "solo_revision_sin_publicar" in capsys.readouterr().out


@pytest.mark.integration
def test_preview_uses_mongodb_prompt_once_and_changes_no_data(api):
    from scripts.data_snapshot import snapshot
    from scripts.preview_openrouter import preview

    settings = Settings(
        _env_file=None,
        **{
            **api[2].model_dump(),
            "anthropic_api_key": "sk-ant-only-test-not-a-real-key",
        },
    )
    calls = []

    def handler(request):
        calls.append(request)
        assert (
            "Cantidad exacta de preguntas: 5"
            in json.loads(request.content)["messages"][0]["content"]
        )
        return httpx.Response(200, json=body(settings))

    before = snapshot(settings)
    report = asyncio.run(
        preview(settings, AnthropicProvider(settings, httpx.MockTransport(handler)), 5)
    )
    assert len(calls) == 1 and len(report["preguntas"]) == 5
    assert report["prompt_version"] == 2 and report["id_llamada"] == "msg_test_123"
    assert report["estado"] == "solo_revision_sin_publicar"
    assert report["byok_confirmado"] is None
    assert snapshot(settings) == before
    assert settings.anthropic_api_key.get_secret_value() not in json.dumps(report)


@pytest.mark.parametrize(
    "change,field,rule,count",
    [
        ("quantity", "preguntas", "cantidad_exacta", 4),
        ("gap", "preguntas.0", "value_error", 5),
        ("length", "preguntas.0.respuesta_principal", "string_too_long", 5),
        ("duplicate", "$", "value_error", 5),
        ("json", "content.0.text", "json_valido", None),
        ("truncated", "stop_reason", "end_turn", None),
        ("stop_with_json", "stop_reason", "end_turn", 5),
        ("tokens", "metadatos.tokens_entrada", "int_type", 5),
    ],
)
def test_failure_diagnostics_preserve_metadata_and_exact_rule(
    settings, generation, change, field, rule, count
):
    data = body(settings)
    payload = json.loads(data["content"][0]["text"])
    if change == "quantity":
        payload["preguntas"].pop()
    elif change == "gap":
        payload["preguntas"][0]["enunciado"] = "She ___ and ___ every day."
    elif change == "length":
        payload["preguntas"][0]["respuesta_principal"] = "x" * 201
    elif change == "duplicate":
        payload["preguntas"][1] = copy.deepcopy(payload["preguntas"][0])
    elif change == "tokens":
        data["usage"]["input_tokens"] = True
    if change in ("truncated", "stop_with_json"):
        data["stop_reason"] = "max_tokens"
    data["content"][0]["text"] = (
        '{"preguntas": [' if change in ("json", "truncated") else json.dumps(payload)
    )
    provider = AnthropicProvider(
        settings, httpx.MockTransport(lambda request: httpx.Response(200, json=data))
    )
    with pytest.raises(AnthropicError) as error:
        asyncio.run(provider.generate(replace(generation, ronda=2)))
    diagnostic = error.value.diagnostic
    assert diagnostic["ronda"] == 2
    assert diagnostic["modelo_utilizado"] == settings.anthropic_model
    assert diagnostic["id_llamada"] == "msg_test_123"
    assert diagnostic["stop_reason"] == data["stop_reason"]
    assert diagnostic["tokens_salida"] == 350
    assert diagnostic["tokens_entrada"] == (None if change == "tokens" else 120)
    assert diagnostic["preguntas_recibidas"] == count
    assert diagnostic["errores"][0]["campo"] == field
    assert diagnostic["errores"][0]["regla"] == rule
    assert all("input" not in item and "ctx" not in item for item in diagnostic["errores"])


@pytest.mark.parametrize("invalid", [True, False])
def test_optional_capture_saves_only_text_and_redacts_key(settings, generation, tmp_path, invalid):
    from scripts.preview_openrouter import text_capture

    secret = settings.anthropic_api_key.get_secret_value()
    data = body(settings)
    text = data["content"][0]["text"]
    if invalid:
        payload = json.loads(text)
        payload["preguntas"][0]["enunciado"] += secret
        payload["unexpected"] = "error-contract"
        text = json.dumps(payload)
    data["content"][0]["text"] = text
    captures = []
    provider = AnthropicProvider(
        settings,
        httpx.MockTransport(
            lambda request: httpx.Response(
                200, json=data, headers={"x-private-header": "header-must-not-be-saved"}
            )
        ),
        capture_text=text_capture(tmp_path / ".tools", captures),
    )
    if invalid:
        with pytest.raises(AnthropicError) as error:
            asyncio.run(provider.generate(generation))
        assert secret not in json.dumps(error.value.diagnostic)
    else:
        asyncio.run(provider.generate(generation))
    files = list((tmp_path / ".tools").glob("anthropic-ronda-1-*.txt"))
    assert len(files) == len(captures) == 1
    assert files[0].read_text(encoding="utf-8") == text.replace(secret, "[REDACTADO]")
    assert secret not in files[0].read_text(encoding="utf-8")
    assert "header-must-not-be-saved" not in files[0].read_text(encoding="utf-8")


def test_capture_write_failure_does_not_mask_validation(settings, generation, tmp_path):
    from scripts.preview_openrouter import text_capture

    directory = tmp_path / ".tools"
    directory.write_text("un archivo impide crear el directorio", encoding="utf-8")
    data = body(settings)
    data["stop_reason"] = "max_tokens"
    provider = AnthropicProvider(
        settings,
        httpx.MockTransport(lambda request: httpx.Response(200, json=data)),
        capture_text=text_capture(directory, []),
    )
    with pytest.raises(AnthropicError) as error:
        asyncio.run(provider.generate(generation))
    assert error.value.diagnostic["errores"][0]["campo"] == "stop_reason"
    assert "aviso_guardado" in error.value.diagnostic


@pytest.mark.integration
def test_cli_round_two_failure_reports_metadata_and_keeps_database_unchanged(
    api, monkeypatch, capsys, tmp_path
):
    from scripts import preview_openrouter as cli
    from scripts.data_snapshot import snapshot

    initial = api[2]
    settings = Settings(
        _env_file=None,
        **{
            **initial.model_dump(),
            "anthropic_api_key": "sk-ant-only-test-not-a-real-key",
        },
    )
    calls = []

    def handler(request):
        calls.append(request)
        data = body(settings)
        payload = json.loads(data["content"][0]["text"])
        if len(calls) == 1:
            existing = api[1].BancoEjercicios.find_one({"secuencia": 1})
            payload["preguntas"][0].update(
                enunciado=existing["enunciado"],
                respuesta_principal=existing["respuesta_principal"],
                solucion=existing["solucion"],
                distractores=["never-accepted", "also-wrong"],
            )
        else:
            payload["preguntas"] = payload["preguntas"][:1]
            payload["preguntas"][0]["enunciado"] = "She ___ and ___ every day."
            data["id"] = "msg_failed_round_2"
        data["content"][0]["text"] = json.dumps(payload)
        return httpx.Response(200, json=data)

    real_provider = AnthropicProvider
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(
        cli,
        "AnthropicProvider",
        lambda configuration, **kwargs: real_provider(
            configuration, httpx.MockTransport(handler), **kwargs
        ),
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "preview",
            "--proveedor",
            "anthropic",
            "--cantidad",
            "5",
            "--modo",
            "lote",
            "--guardar-texto",
        ],
    )
    before = snapshot(settings)
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["estado"] == "fallo_generacion_sin_publicar"
    assert report["diagnostico"]["ronda"] == len(calls) == 2
    assert report["diagnostico"]["id_llamada"] == "msg_failed_round_2"
    assert report["diagnostico"]["preguntas_recibidas"] == 1
    assert report["diagnostico"]["errores"][0]["campo"] == "preguntas.0"
    assert len(report["textos_guardados"]) == 2
    assert len(list((tmp_path / ".tools").glob("*.txt"))) == 2
    assert snapshot(settings) == before
    assert settings.anthropic_api_key.get_secret_value() not in json.dumps(report)


def test_thirty_questions_report_exact_failing_question(settings, generation):
    parameters = generation.parameters.model_copy(update={"cantidad": 30})
    generation = replace(generation, parameters=parameters)
    data = body(settings)
    template = json.loads(data["content"][0]["text"])["preguntas"][0]
    questions = [
        {**copy.deepcopy(template), "enunciado": f"Every day, student {i} ___ (play) football."}
        for i in range(30)
    ]
    questions[17]["distractores"] = ["plays", "playing"]
    data["content"][0]["text"] = json.dumps({"preguntas": questions})
    with pytest.raises(AnthropicError) as error:
        asyncio.run(
            AnthropicProvider(
                settings, httpx.MockTransport(lambda request: httpx.Response(200, json=data))
            ).generate(generation)
        )
    diagnostic = error.value.diagnostic
    assert diagnostic["preguntas_recibidas"] == diagnostic["preguntas_solicitadas"] == 30
    assert diagnostic["errores"][0]["campo"] == "preguntas.17"
    assert "Los distractores deben ser distintos" in diagnostic["errores"][0]["detalle"]
