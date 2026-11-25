import asyncio

import pytest
from test_modalities import question
from test_refill_cursor_integration import login
from test_replenishment import create

from app.anthropic import AnthropicError
from app.config import Settings
from app.generation import GenerationResponse
from app.models import AIModel
from app.replenishment import BankRefiller
from scripts.activate_generation_prompt import activate
from scripts.preview_generation import preview

pytestmark = pytest.mark.integration


class FailingSecondRound:
    model = AIModel(proveedor="simulado", modelo="regresion-metricas")

    def __init__(self, known=False, extra=False, timeout=False):
        self.calls = 0
        self.known, self.extra, self.timeout = known, extra, timeout

    async def generate(self, request):
        self.calls += 1
        if self.calls == 2:
            if self.timeout:
                await asyncio.Event().wait()
            diagnostic = {"id_llamada": "msg_failed", "stop_reason": "max_tokens"}
            if self.known:
                diagnostic.update(tokens_entrada=200, tokens_salida=0, preguntas_recibidas=1)
            raise AnthropicError("no persistir este mensaje del proveedor", diagnostic)
        questions = [
            question(mode, i)
            for mode, count in (("afirmativa", 21), ("negativa", 6), ("interrogativa", 3))
            for i in range(count)
        ]
        if self.calls == 1:
            questions.pop()  # 29 candidatas válidas; falta una interrogativa.
            if self.extra:
                bad = question("interrogativa", 99)
                bad["distractores"] = ["bad", "bad"]
                questions += [bad, questions[0], question("afirmativa", 99)]
        return GenerationResponse(
            payload={"preguntas": questions},
            tokens_entrada=1000,
            tokens_salida=500,
            id_llamada=f"msg_{self.calls}",
        )


@pytest.mark.parametrize(
    "known,extra", [(False, False), (True, False), (False, True), (True, True)]
)
def test_failed_second_round_keeps_known_metrics_without_partial_publication(
    api, registration, known, extra
):
    client, db, initial = api
    client.portal.call(activate, client.app.state.database, 4)
    headers = login(client, registration, "metrics@example.com")
    bank = db.Bancos.find_one()
    assert create(client, headers, str(bank["tema_id"])).status_code == 201
    progress = list(db.Progreso.find())
    attempts = list(db.Tests.find())
    settings = Settings(_env_file=None, **{**initial.model_dump(), "bank_refill_max_attempts": 1})
    provider = FailingSecondRound(known, extra)
    worker = BankRefiller(client.app.state.database, settings, provider)
    client.portal.call(worker.request, bank["_id"], 10)
    claimed = client.portal.call(worker.claim)
    client.portal.call(worker.process, claimed)
    final = db.Bancos.find_one()
    metrics = final["ultima_generacion"]
    assert final["reposicion"]["estado"] == "error"
    assert metrics["preguntas_validas"] == 29
    assert metrics["preguntas_recibidas"] == 29 + (3 if extra else 0) + (1 if known else 0)
    assert metrics["preguntas_recibidas_completas"] is known
    assert metrics["tokens_entrada_conocidos"] == (1200 if known else 1000)
    assert metrics["tokens_salida_conocidos"] == 500
    assert metrics["tokens_entrada"] == (1200 if known else None)
    assert metrics["tokens_salida"] == (500 if known else None)
    assert metrics["preguntas_rechazadas_contrato"] == int(extra)
    assert metrics["preguntas_descartadas_duplicadas"] == int(extra)
    assert metrics["preguntas_descartadas_cuota"] == int(extra)
    assert metrics["rondas_consumidas"] == final["reposicion"]["rondas_consumidas"] == 2
    assert [r["ronda"] for r in metrics["rondas"]] == [1, 2]
    assert metrics["rondas"][1]["id_llamada"] == "msg_failed"
    assert metrics["errores"][-1]["codigo"] == "reposicion_fallida"
    if extra:
        assert metrics["errores"][0]["codigo"] == "pregunta_invalida"
    assert "no persistir" not in str(final)
    assert db.BancoEjercicios.count_documents({}) == final["publicado_hasta"] == 10
    assert list(db.Progreso.find()) == progress and list(db.Tests.find()) == attempts


def test_retry_preserves_consumption_and_round_history_of_same_job(api):
    client, db, settings = api
    client.portal.call(activate, client.app.state.database, 4)
    provider = FailingSecondRound()
    worker = BankRefiller(client.app.state.database, settings, provider)
    client.portal.call(worker.request, db.Bancos.find_one()["_id"], 10)
    claimed = client.portal.call(worker.claim)
    client.portal.call(worker.process, claimed)
    db.Bancos.update_one({}, {"$set": {"reposicion.reintento_en": claimed.reposicion.creado_en}})
    recovered = client.portal.call(worker.claim)
    client.portal.call(worker.process, recovered)
    bank = db.Bancos.find_one()
    metrics = bank["ultima_generacion"]
    assert bank["reposicion"]["estado"] == "completada"
    assert provider.calls == metrics["rondas_consumidas"] == 3
    assert metrics["preguntas_recibidas"] == 59 and metrics["preguntas_validas"] == 30
    assert (
        metrics["tokens_entrada_conocidos"] == 2000 and metrics["tokens_salida_conocidos"] == 1000
    )
    assert metrics["tokens_entrada"] is metrics["tokens_salida"] is None
    assert [r["ronda"] for r in metrics["rondas"]] == [1, 2, 3]
    assert metrics["errores"][0]["codigo"] == "reposicion_fallida"
    assert db.BancoEjercicios.count_documents({}) == 40


def test_timeout_keeps_first_round_metrics(api):
    client, db, initial = api
    client.portal.call(activate, client.app.state.database, 4)
    settings = Settings(
        _env_file=None, **{**initial.model_dump(), "bank_refill_timeout_seconds": 1}
    )
    worker = BankRefiller(client.app.state.database, settings, FailingSecondRound(timeout=True))
    client.portal.call(worker.request, db.Bancos.find_one()["_id"], 10)
    client.portal.call(worker.process, client.portal.call(worker.claim))
    metrics = db.Bancos.find_one()["ultima_generacion"]
    assert metrics["preguntas_validas"] == 29 and metrics["tokens_entrada_conocidos"] == 1000
    assert metrics["errores"][-1]["codigo"] == "proveedor_timeout"
    assert db.BancoEjercicios.count_documents({}) == 10


def test_preview_failure_preserves_accumulated_diagnostic_without_writing(api):
    _, db, settings = api
    from scripts.data_snapshot import snapshot

    async def run():
        from app.database import create_client

        client = create_client(settings)
        try:
            await activate(client[settings.mongodb_db], 4, True)
        finally:
            await client.close()

    asyncio.run(run())
    before = snapshot(settings)
    with pytest.raises(AnthropicError) as failure:
        asyncio.run(preview(settings, FailingSecondRound(), 30, complete_batch=True))
    metrics = failure.value.diagnostic["metricas_acumuladas"]
    assert metrics["preguntas_candidatas"] == 29 and metrics["tokens_entrada_conocidos"] == 1000
    assert metrics["tokens_entrada"] is None and len(metrics["rondas"]) == 2
    assert snapshot(settings) == before and db.BancoEjercicios.count_documents({}) == 10
