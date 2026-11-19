import asyncio
import copy
import json
import re

import pytest
from bson import ObjectId

from app.bank_contracts import BankMetadataDocument
from app.config import Settings
from app.generation import GenerationResponse, prepare_generation_context, sentence_key
from app.models import AIModel
from app.replenishment import BankRefiller
from app.seed import SUBJECT_ID, seed_database
from scripts.activate_generation_prompt import activate
from scripts.data_snapshot import snapshot
from scripts.preview_openrouter import preview

pytestmark = pytest.mark.integration


@pytest.fixture
def v3(api):
    client, database, _ = api
    before = database.Prompts.find_one({"version": 2, "nombre": "generacion_banco"})
    client.portal.call(activate, client.app.state.database, 3)
    assert database.Prompts.count_documents({"tipo": "generacion", "activo": True}) == 1
    after = database.Prompts.find_one({"_id": before["_id"]})
    assert after == {**before, "activo": False}
    return api


def fresh(index):
    return {
        "enunciado": f"Every morning, learner {index} ___ (play) football.",
        "respuesta_principal": "plays",
        "solucion": {"respuestas_aceptadas": ["plays"], "sensible_mayusculas": False},
        "distractores": ["play", "playing"],
    }


def old(database, count):
    return [
        {
            "enunciado": q["enunciado"],
            "respuesta_principal": q["respuesta_principal"],
            "solucion": q["solucion"],
            "distractores": ["never-accepted", "also-wrong"],
        }
        for q in database.BancoEjercicios.find().sort("secuencia", 1).limit(count)
    ]


class ScriptedProvider:
    model = AIModel(proveedor="simulado", modelo="solo-test")

    def __init__(self, rounds):
        self.rounds = copy.deepcopy(rounds)
        self.requests = []

    async def generate(self, request):
        self.requests.append(request)
        return GenerationResponse(
            payload={"preguntas": self.rounds.pop(0)},
            tokens_entrada=10,
            tokens_salida=20,
            id_llamada=f"msg_{len(self.requests)}",
        )


def exclusions(prompt):
    return json.loads(
        re.search(r"<enunciados_excluidos>\n(.*?)\n</enunciados_excluidos>", prompt, re.S)[1]
    )


def test_v3_separates_examples_and_excludes_all_ten_bank_sentences(v3):
    client, database, _ = v3
    bank = BankMetadataDocument.model_validate(database.Bancos.find_one())
    _, prompt, trace = client.portal.call(
        prepare_generation_context,
        client.app.state.database,
        bank,
        ScriptedProvider.model,
        5,
        ObjectId(),
    )
    existing = [q["enunciado"] for q in database.BancoEjercicios.find().sort("secuencia", 1)]
    assert trace.prompt_version == 3
    assert exclusions(prompt) == existing and len(existing) == 10
    pedagogical = prompt.split("<enunciados_excluidos>")[0]
    assert all(sentence_key(text) not in sentence_key(pedagogical) for text in existing)
    assert "My sister ___ (study) English on Mondays." in pedagogical
    assert "No copies sus enunciados" in prompt
    assert "distractores" in prompt and "Cantidad exacta de preguntas: 5" in prompt


def test_seed_and_atomic_activation_preserve_versions_and_remain_idempotent(v3):
    client, database, _ = v3
    before = list(database.Prompts.find().sort("_id", 1))
    client.portal.call(seed_database, client.app.state.database)
    client.portal.call(seed_database, client.app.state.database)
    client.portal.call(activate, client.app.state.database, 3)
    assert list(database.Prompts.find().sort("_id", 1)) == before
    client.portal.call(activate, client.app.state.database, 2)
    assert database.Prompts.count_documents({"tipo": "generacion", "activo": True}) == 1
    assert database.Prompts.find_one({"asignatura_id": SUBJECT_ID, "activo": True})["version"] == 2


@pytest.mark.parametrize("quantity,duplicates", [(30, 2), (5, 5)])
def test_preview_completes_only_missing_questions_and_accumulates_tokens(v3, quantity, duplicates):
    _, database, settings = v3
    first = old(database, duplicates) + [fresh(i) for i in range(quantity - duplicates)]
    second = [fresh(i + 100) for i in range(duplicates)]
    provider = ScriptedProvider([first, second])
    before = snapshot(settings)
    report = asyncio.run(preview(settings, provider, quantity, complete_batch=True))
    assert [r.parameters.cantidad for r in provider.requests] == [quantity, duplicates]
    assert report["lote_completo"] is True
    assert report["preguntas_candidatas"] == quantity
    assert report["candidatas_primera_ronda"] == quantity - duplicates
    assert report["duplicadas_primera_ronda"] == duplicates
    assert report["numero_llamadas"] == 2
    assert (report["tokens_entrada"], report["tokens_salida"]) == (20, 40)
    assert (report["preguntas_recibidas"], report["preguntas_descartadas_duplicadas"]) == (
        quantity + duplicates,
        duplicates,
    )
    assert set(q["enunciado"] for q in first[duplicates:]) <= set(
        exclusions(provider.requests[1].prompt)
    )
    assert len({sentence_key(q["enunciado"]) for q in report["preguntas"]}) == quantity
    assert snapshot(settings) == before


def test_diagnostic_remains_one_call_even_if_all_questions_are_duplicates(v3):
    _, database, settings = v3
    provider = ScriptedProvider([old(database, 5)])
    before = snapshot(settings)
    report = asyncio.run(preview(settings, provider))
    assert report["numero_llamadas"] == 1
    assert report["preguntas_candidatas"] == 0
    assert report["preguntas_descartadas_duplicadas"] == 5
    assert not report["lote_completo"]
    assert snapshot(settings) == before


def test_round_exhaustion_keeps_preview_candidates_without_any_publication(v3):
    _, database, initial = v3
    settings = Settings(_env_file=None, **{**initial.model_dump(), "bank_generation_max_rounds": 2})
    provider = ScriptedProvider([[fresh(i) for i in range(4)] + old(database, 1), [fresh(0)]])
    before = snapshot(settings)
    report = asyncio.run(preview(settings, provider, complete_batch=True))
    assert [r.parameters.cantidad for r in provider.requests] == [5, 1]
    assert report["numero_llamadas"] == 2
    assert report["preguntas_candidatas"] == 4
    assert report["estado"] == "lote_incompleto_sin_publicar"
    assert snapshot(settings) == before


@pytest.mark.parametrize("complete", [True, False])
def test_worker_publishes_exact_batch_or_nothing_and_persists_call_budget(v3, complete):
    client, database, initial = v3
    settings = Settings(
        _env_file=None,
        **{
            **initial.model_dump(),
            "bank_generation_max_rounds": 2,
            "bank_refill_batch_size": 5,
        },
    )
    second = [fresh(100)] if complete else [fresh(0)]
    provider = ScriptedProvider([[fresh(i) for i in range(4)] + old(database, 1), second])
    refiller = BankRefiller(client.app.state.database, settings, provider)
    bank_id = database.Bancos.find_one()["_id"]
    before_progress = list(database.Progreso.find())
    client.portal.call(refiller.request, bank_id, 10)
    claimed = client.portal.call(refiller.claim)
    client.portal.call(refiller.process, claimed)
    bank = database.Bancos.find_one()
    assert bank["reposicion"]["rondas_consumidas"] == len(provider.requests) == 2
    assert [r.parameters.cantidad for r in provider.requests] == [5, 1]
    assert bank["ultima_generacion"]["preguntas_validas"] == (5 if complete else 4)
    assert bank["ultima_generacion"]["tokens_entrada"] == 20
    assert (
        bank["publicado_hasta"]
        == database.BancoEjercicios.count_documents({})
        == (15 if complete else 10)
    )
    assert list(database.Progreso.find()) == before_progress
    if complete:
        assert bank["reposicion"]["estado"] == "completada"
        assert list(database.BancoEjercicios.distinct("secuencia")) == list(range(1, 16))
    else:
        assert bank["reposicion"]["error_codigo"] == "lote_incompleto"
        assert bank["reposicion"]["reintento_en"] is None
        assert client.portal.call(refiller.claim) is None


def test_provider_retries_share_a_durable_round_budget(v3):
    client, database, initial = v3
    settings = Settings(
        _env_file=None,
        **{
            **initial.model_dump(),
            "bank_generation_max_rounds": 2,
            "bank_refill_retry_seconds": 0,
        },
    )

    class FailingProvider:
        model = ScriptedProvider.model
        calls = 0

        async def generate(self, request):
            self.calls += 1
            raise RuntimeError("Fallo simulado")

    provider = FailingProvider()
    refiller = BankRefiller(client.app.state.database, settings, provider)
    bank_id = database.Bancos.find_one()["_id"]
    client.portal.call(refiller.request, bank_id, 10)
    for _ in range(2):
        claimed = client.portal.call(refiller.claim)
        assert claimed is not None
        client.portal.call(refiller.process, claimed)
    assert provider.calls == 2
    assert client.portal.call(refiller.claim) is None
    bank = database.Bancos.find_one()
    assert bank["reposicion"]["rondas_consumidas"] == 2
    assert bank["reposicion"]["reintento_en"] is None
    assert bank["publicado_hasta"] == database.BancoEjercicios.count_documents({}) == 10


def test_new_duplicate_at_publication_prevents_partial_batch(v3, monkeypatch):
    client, database, initial = v3
    settings = Settings(_env_file=None, **{**initial.model_dump(), "bank_refill_batch_size": 5})
    provider = ScriptedProvider([[fresh(i) for i in range(5)]])
    refiller = BankRefiller(client.app.state.database, settings, provider)
    original_publish = refiller.publish
    bank_id = database.Bancos.find_one()["_id"]

    async def concurrent_publication(bank, output, metrics):
        # Otro publicador inserta una candidata después del filtro y antes de la transacción.
        question = dict(database.BancoEjercicios.find_one())
        question.update(_id=ObjectId(), secuencia=11, **output.preguntas[0].model_dump())
        database.BancoEjercicios.insert_one(question)
        database.Bancos.update_one({"_id": bank_id}, {"$set": {"publicado_hasta": 11}})
        return await original_publish(bank, output, metrics)

    monkeypatch.setattr(refiller, "publish", concurrent_publication)
    client.portal.call(refiller.request, bank_id, 10)
    claimed = client.portal.call(refiller.claim)
    client.portal.call(refiller.process, claimed)
    bank = database.Bancos.find_one()
    assert bank["reposicion"]["error_codigo"] == "lote_incompleto"
    assert bank["publicado_hasta"] == database.BancoEjercicios.count_documents({}) == 11
    assert database.BancoEjercicios.count_documents({"origen": "llm"}) == 0
