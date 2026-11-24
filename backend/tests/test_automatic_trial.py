import asyncio
import json
import os

import httpx
import pytest
from test_anthropic import body
from test_modalities import question

from app.anthropic import AnthropicProvider
from app.config import Settings
from scripts.trial_automatic_refill import check_isolation, run_trial


def test_cli_refuses_without_authorization_before_loading_settings(monkeypatch, capsys):
    from scripts import trial_automatic_refill as cli

    def forbidden():
        pytest.fail("No debe cargar secretos ni conectar antes de autorizar")

    monkeypatch.setattr(cli, "Settings", forbidden)
    monkeypatch.setattr("sys.argv", ["trial"])
    with pytest.raises(SystemExit, match="falta --autorizar-anthropic"):
        cli.main()
    assert not capsys.readouterr().out


def configuration(**changes):
    return Settings(
        _env_file=None,
        **{
            "mongodb_uri": os.environ.get("TEST_MONGODB_URI", "mongodb://isolated.invalid"),
            "mongodb_db": "trainer_refill_trial",
            "jwt_secret": "f" * 40,
            "anthropic_api_key": "sk-ant-only-test-not-a-real-key",
            "bank_refill_enabled": True,
            "bank_refill_max_attempts": 1,
            "bank_refill_timeout_seconds": 5,
            "bank_refill_lease_seconds": 10,
            "bank_refill_poll_seconds": 0.01,
            **changes,
        },
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"mongodb_db": "trainer"},
        {"mongodb_uri": "mongodb://trainer_test:fake@mongodb:27017/?replicaSet=trainer-test"},
        {"bank_refill_max_attempts": 2},
        {"bank_generation_max_rounds": 4},
    ],
)
def test_isolation_and_call_limits_are_mandatory(changes):
    with pytest.raises(ValueError, match="Ensayo bloqueado"):
        check_isolation(configuration(**changes))


@pytest.mark.integration
@pytest.mark.parametrize("outcome", ["success", "http_failure", "incomplete"])
def test_authenticated_trigger_two_students_real_worker_with_mock_http(mongo_client, outcome):
    settings = configuration()
    check_isolation(settings)
    db = mongo_client[settings.mongodb_db]
    assert db.list_collection_names() == []
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        if outcome == "http_failure":
            return httpx.Response(503, text=settings.anthropic_api_key.get_secret_value())
        data = body(settings)
        questions = [
            question(mode, index)
            for mode, count in (("afirmativa", 21), ("negativa", 6), ("interrogativa", 3))
            for index in range(count)
        ]
        if outcome == "incomplete":
            questions = questions[:1]  # Las rondas siguientes repiten ese enunciado.
        data["content"][0]["text"] = json.dumps({"preguntas": questions})
        return httpx.Response(200, json=data)

    try:
        provider = AnthropicProvider(settings, httpx.MockTransport(handler))
        report = asyncio.run(run_trial(settings, authorized=True, provider=provider))
        assert report["estado"] == ("completada" if outcome == "success" else "error")
        assert report["publicadas_nuevas"] == (30 if outcome == "success" else 0)
        assert report["cursores_conservados"] and report["cursores_antes"] == [10, 10]
        assert report["restantes_al_disparar"] == 20 and report["intentos_worker"] == 1
        assert report["rondas_consumidas"] == len(calls) == (3 if outcome == "incomplete" else 1)
        assert db.Tests.count_documents({}) == db.Usuarios.count_documents({}) == 2
        assert settings.anthropic_api_key.get_secret_value() not in json.dumps(report)
        assert db.Bancos.count_documents({"reposicion.estado": report["estado"]}) == 1
        if outcome == "success":
            assert report["tokens_entrada"] == 120 and report["tokens_salida"] == 350
        # Reejecutar no repone otra vez ni reinicia una base usada.
        with pytest.raises(ValueError, match="ya contiene colecciones"):
            asyncio.run(run_trial(settings, authorized=True, provider=provider))
        assert len(calls) == (3 if outcome == "incomplete" else 1)
    finally:
        mongo_client.drop_database(settings.mongodb_db)
