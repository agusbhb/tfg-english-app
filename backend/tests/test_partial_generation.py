import asyncio

import pytest
from test_modalities import question

from app.config import Settings
from app.generation import GenerationResponse
from app.models import AIModel
from app.replenishment import BankRefiller
from scripts.activate_generation_prompt import activate
from scripts.data_snapshot import snapshot
from scripts.preview_generation import preview

pytestmark = pytest.mark.integration


class PartialProvider:
    model = AIModel(proveedor="simulado", modelo="test")

    def __init__(self, bad_rounds):
        self.requests = []
        self.bad_rounds = bad_rounds

    async def generate(self, request):
        self.requests.append(request)
        count = len(self.requests)
        questions = [
            question(mode, count * 100 + i)
            for mode, total in request.parameters.distribucion.items()
            for i in range(total)
        ]
        if count in self.bad_rounds:
            item = next(q for q in questions if q["modalidad"] == "negativa")
            item["distractores"] = ["don't like", "don't like"]
        return GenerationResponse(
            payload={"preguntas": questions}, tokens_entrada=10, tokens_salida=20
        )


@pytest.mark.parametrize("bad_rounds,expected_calls", [({1}, 2), ({1, 2}, 3)])
def test_preview_thirty_keeps_twenty_nine_and_retries_only_invalid_modality(
    api, bad_rounds, expected_calls
):
    client, _, settings = api
    client.portal.call(activate, client.app.state.database, 4)
    provider = PartialProvider(bad_rounds)
    before = snapshot(settings)
    report = asyncio.run(preview(settings, provider, 30, complete_batch=True))
    assert snapshot(settings) == before
    assert report["lote_completo"] and report["preguntas_candidatas"] == 30
    assert report["candidatas_primera_ronda"] == 29
    assert report["numero_llamadas"] == expected_calls
    assert report["preguntas_rechazadas_contrato"] == len(bad_rounds)
    assert report["preguntas_descartadas_duplicadas"] == 0
    assert report["rechazos_contrato"][0]["indice"] == 21
    assert report["rechazos_contrato"][0]["errores"][0]["campo"] == "distractores"
    assert "distractores" in report["rechazos_contrato"][0]["errores"][0]["motivo"]
    assert [r.parameters.cantidad for r in provider.requests] == [30] + [1] * (expected_calls - 1)
    assert all(
        r.parameters.distribucion == {"afirmativa": 0, "negativa": 1, "interrogativa": 0}
        for r in provider.requests[1:]
    )


@pytest.mark.parametrize("success", [True, False])
def test_worker_partial_validation_publication_is_all_or_nothing(api, success):
    client, database, initial = api
    client.portal.call(activate, client.app.state.database, 4)
    settings = Settings(_env_file=None, **{**initial.model_dump(), "bank_generation_max_rounds": 2})
    provider = PartialProvider({1} if success else {1, 2})
    refiller = BankRefiller(client.app.state.database, settings, provider)
    bank_id = database.Bancos.find_one()["_id"]
    client.portal.call(refiller.request, bank_id, 10)
    claimed = client.portal.call(refiller.claim)
    client.portal.call(refiller.process, claimed)
    bank = database.Bancos.find_one()
    assert (
        bank["publicado_hasta"]
        == database.BancoEjercicios.count_documents({})
        == (40 if success else 10)
    )
    assert bank["ultima_generacion"]["preguntas_validas"] == (30 if success else 29)
    assert bank["ultima_generacion"]["preguntas_rechazadas_contrato"] == (1 if success else 2)
    assert any(
        error["codigo"] == "pregunta_invalida" for error in bank["ultima_generacion"]["errores"]
    )
    assert database.Progreso.count_documents({}) == 0


@pytest.mark.parametrize(
    "payload", [[], {}, {"preguntas": "no array"}, {"preguntas": [], "otra": True}]
)
def test_invalid_global_structure_stays_a_clear_error(api, payload):
    client, _, settings = api

    class Provider:
        model = AIModel(proveedor="simulado", modelo="test")

        async def generate(self, request):
            return GenerationResponse(payload=payload) if isinstance(payload, dict) else payload

    before = snapshot(settings)
    with pytest.raises(ValueError):
        asyncio.run(preview(settings, Provider(), 5, complete_batch=True))
    assert snapshot(settings) == before
