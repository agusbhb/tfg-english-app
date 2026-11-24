import asyncio
import copy

import pytest
from bson import ObjectId
from pydantic import ValidationError

from app.bank_contracts import (
    BankMetadataDocument,
    GeneratedBankQuestion,
    GenerationMetrics,
    GenerationOutput,
    ModalBankQuestion,
    modality_counts,
    modality_distribution,
)
from app.generation import GenerationResponse, prepare_generation_context
from app.models import AIModel, BlankQuestion
from app.practice import grade
from app.prompts import generation_output_schema, validate_generation_output
from app.replenishment import BankRefiller, IncompleteBatch, now
from app.schemas import AnswerInput
from scripts.activate_generation_prompt import activate
from scripts.data_snapshot import snapshot
from scripts.preview_generation import preview


def question(mode, i):
    if mode == "afirmativa":
        sentence, answer, accepted, distractors = (
            f"Student {i} ___ (play) football.",
            "plays",
            ["plays"],
            ["play", "playing"],
        )
    elif mode == "negativa":
        sentence, answer, accepted, distractors = (
            f"Student {i} ___ (not like) fish.",
            "doesn't like",
            ["doesn't like", "does not like"],
            ["don't like", "doesn't likes"],
        )
    else:
        sentence, answer, accepted, distractors = (
            f"___ student {i} play football?",
            "Does",
            ["Does"],
            ["Is", "Have"],
        )
    return dict(
        enunciado=sentence,
        respuesta_principal=answer,
        modalidad=mode,
        solucion={"respuestas_aceptadas": accepted, "sensible_mayusculas": False},
        distractores=distractors,
    )


def test_deterministic_distribution_rounding():
    assert modality_distribution(30, "present-simple", "A1") == {
        "afirmativa": 21,
        "negativa": 6,
        "interrogativa": 3,
    }
    assert modality_distribution(5, "present-simple", "A1") == {
        "afirmativa": 4,
        "negativa": 1,
        "interrogativa": 0,
    }
    assert modality_distribution(1, "present-simple", "A1") == {
        "afirmativa": 1,
        "negativa": 0,
        "interrogativa": 0,
    }
    for quantity in range(1, 51):
        assert sum(modality_distribution(quantity, "present-simple", "A1").values()) == quantity


@pytest.mark.parametrize(
    "label",
    [
        "(affirmative)",
        "(negative)",
        "(interrogative)",
        "(question)",
        "[NEGATIVE]",
        "afirmativa:",
        "negativa:",
        "interrogativa:",
        "(play; affirmative)",
    ],
)
def test_v4_rejects_modality_labels_but_legacy_remains_readable(label):
    payload = question("afirmativa", 1)
    payload["enunciado"] += " " + label
    assert GeneratedBankQuestion.model_validate(payload)
    with pytest.raises(ValidationError, match="etiquetas de modalidad"):
        ModalBankQuestion.model_validate(payload)


@pytest.mark.integration
def test_explicit_v4_refresh_preserves_previous_versions_and_updates_sheet_reference(api):
    from app.bank_contracts import ContentKey
    from app.content import load_educational_sheet
    from app.seed import initial_generation_prompt

    client, database, _ = api
    old = list(database.Prompts.find({"version": {"$lt": 4}}).sort("_id", 1))
    v4 = database.Prompts.find_one({"version": 4})
    database.Prompts.update_one(
        {"_id": v4["_id"]}, {"$set": {"contenido_prompt": "texto anterior"}}
    )
    client.portal.call(activate, client.app.state.database, 4, True)
    updated = database.Prompts.find_one({"_id": v4["_id"]})
    assert updated["contenido_prompt"] == initial_generation_prompt(4).contenido_prompt
    assert updated["creado_en"] == v4["creado_en"]
    assert all(
        {k: v for k, v in before.items() if k != "activo"}
        == {k: v for k, v in after.items() if k != "activo"}
        for before, after in zip(
            old, database.Prompts.find({"version": {"$lt": 4}}).sort("_id", 1), strict=True
        )
    )
    sheet = load_educational_sheet(
        ContentKey(codigo_tema="present-simple", nivel_mcer="A1", version=2)
    )
    assert database.Bancos.find_one()["ficha"] == sheet.referencia.model_dump()
    assert database.Prompts.count_documents({"activo": True, "tipo": "generacion"}) == 1
    with pytest.raises(ValueError):
        client.portal.call(activate, client.app.state.database, 3, True)


def test_distribution_is_configured_per_topic_and_level(monkeypatch):
    from app.bank_contracts import MODALITY_WEIGHTS

    for topic, level in [("past-simple", "A1"), ("present-simple", "A2")]:
        with pytest.raises(ValueError, match="configurada"):
            modality_distribution(30, topic, level)
    monkeypatch.setitem(MODALITY_WEIGHTS, ("past-simple", "A2"), (20, 30, 50))
    assert modality_distribution(10, "past-simple", "A2") == {
        "afirmativa": 2,
        "negativa": 3,
        "interrogativa": 5,
    }
    assert modality_distribution(30, "present-simple", "A1") == {
        "afirmativa": 21,
        "negativa": 6,
        "interrogativa": 3,
    }
    monkeypatch.setitem(MODALITY_WEIGHTS, ("past-simple", "A2"), (20, 30, 30))
    with pytest.raises(ValueError, match="sumar 100"):
        modality_distribution(10, "past-simple", "A2")


@pytest.mark.integration
def test_legacy_generation_does_not_require_a_modality_policy(api, monkeypatch):
    from app.bank_contracts import MODALITY_WEIGHTS

    client, _, settings = api
    client.portal.call(activate, client.app.state.database, 3)
    monkeypatch.delitem(MODALITY_WEIGHTS, ("present-simple", "A1"))

    class LegacyProvider:
        model = AIModel(proveedor="simulado", modelo="legacy")

        async def generate(self, request):
            assert request.parameters.distribucion is None
            return GenerationResponse(
                payload={
                    "preguntas": [
                        question("afirmativa", i) for i in range(request.parameters.cantidad)
                    ]
                }
            )

    before = snapshot(settings)
    report = asyncio.run(preview(settings, LegacyProvider(), 5, complete_batch=True))
    assert report["distribucion_objetivo"] is None
    assert report["lote_completo"] and report["numero_llamadas"] == 1
    assert snapshot(settings) == before


@pytest.mark.parametrize("prefix", ["", "What ", "Where ", "When ", "How often "])
def test_a1_interrogatives_with_lexical_have(prefix):
    q = question("interrogativa", 1)
    q["enunciado"] = prefix + "___ your cousin have lunch?"
    assert ModalBankQuestion.model_validate(q).modalidad == "interrogativa"
    q["enunciado"] = "Why " + q["enunciado"]
    with pytest.raises(ValidationError):
        ModalBankQuestion.model_validate(q)


def test_lexical_have_answers():
    for mode, principal, accepted, distractors in [
        ("afirmativa", "has", ["has"], ["have", "having"]),
        (
            "negativa",
            "doesn't have",
            ["doesn't have", "does not have"],
            ["doesn't has", "don't have"],
        ),
    ]:
        q = question(mode, 1)
        q.update(
            respuesta_principal=principal,
            solucion={"respuestas_aceptadas": accepted, "sensible_mayusculas": False},
            distractores=distractors,
        )
        validated = ModalBankQuestion.model_validate(q)
        for answer in accepted:
            blank = BlankQuestion(
                pregunta_id="q",
                formato="fill_blanks",
                enunciado=q["enunciado"],
                solucion=validated.solucion,
            )
            assert grade([blank], [AnswerInput(pregunta_id="q", respuesta_dada=answer)])[
                0
            ].es_correcta


@pytest.mark.parametrize("mode", ["afirmativa", "negativa", "interrogativa"])
def test_v4_modality_contract_and_legacy_compatibility(mode):
    payload = question(mode, 1)
    assert ModalBankQuestion.model_validate(payload).modalidad == mode
    legacy = {k: v for k, v in payload.items() if k != "modalidad"}
    assert GeneratedBankQuestion.model_validate(legacy).modalidad is None
    with pytest.raises(ValidationError):
        ModalBankQuestion.model_validate(legacy)
    for invalid in (None, "otra", "AFIRMATIVA"):
        with pytest.raises(ValidationError):
            ModalBankQuestion.model_validate({**payload, "modalidad": invalid})


@pytest.mark.parametrize(
    "response,accepted,correct",
    [
        ("plays", ["plays"], True),
        (" doesn't like ", ["doesn't like", "does not like"], True),
        ("does not like", ["doesn't like", "does not like"], True),
        ("does not like", ["doesn't like"], False),
        ("doesn't likes", ["doesn't like", "does not like"], False),
        ("Does", ["Does"], True),
        ("does", ["Does"], True),
        ("Is", ["Does"], False),
        ("doesn’t like", ["doesn't like"], False),
    ],
)
def test_deterministic_grade_multword_and_contractions(response, accepted, correct):
    q = BlankQuestion(
        pregunta_id="q1",
        formato="fill_blanks",
        enunciado="Example ___ .",
        solucion={"respuestas_aceptadas": accepted, "sensible_mayusculas": False},
    )
    result = grade([q], [AnswerInput(pregunta_id="q1", respuesta_dada=response)])
    assert result[0].es_correcta is correct


@pytest.mark.integration
@pytest.mark.parametrize("quantity", [5, 30])
def test_v4_distribution_rounds_and_atomic_publication(api, quantity):
    client, database, settings = api
    previous = list(database.BancoEjercicios.find().sort("_id", 1))
    client.portal.call(activate, client.app.state.database, 4)
    assert database.Bancos.find_one()["ficha"]["version"] == 2
    bank = BankMetadataDocument.model_validate(database.Bancos.find_one())
    params, text, trace = client.portal.call(
        prepare_generation_context,
        client.app.state.database,
        bank,
        AIModel(proveedor="simulado", modelo="test"),
        quantity,
        ObjectId(),
    )
    target = modality_distribution(quantity, "present-simple", "A1")
    assert params.distribucion == target and trace.ficha.version == 2
    assert "doesn't like" in text and "interrogativa" in text
    assert "modalidad" in generation_output_schema(params)["$defs"]["ModalBankQuestion"]["required"]

    class Provider:
        model = AIModel(proveedor="simulado", modelo="test")
        calls = []

        async def generate(self, request):
            self.calls.append(request)
            payload = [
                question(mode, 100 * len(self.calls) + i)
                for mode, count in request.parameters.distribucion.items()
                for i in range(count)
            ]
            if len(self.calls) == 1:
                # Negativa válida ya presente: debe pedir SOLO una negativa en segunda ronda.
                duplicate = next(q for q in payload if q["modalidad"] == "negativa")
                duplicate["enunciado"] = "Old student ___ (not like) fish."
            return GenerationResponse(payload={"preguntas": payload})

    duplicate = question("negativa", 999)
    duplicate["enunciado"] = "Old student ___ (not like) fish."
    raw = copy.deepcopy(previous[0])
    raw.update(_id=ObjectId(), secuencia=11, **duplicate)
    database.BancoEjercicios.insert_one(raw)
    database.Bancos.update_one({"_id": bank.id}, {"$set": {"publicado_hasta": 11}})
    provider = Provider()
    before = snapshot(settings)
    report = asyncio.run(preview(settings, provider, quantity, complete_batch=True))
    assert snapshot(settings) == before
    assert provider.calls[1].parameters.distribucion == {
        "afirmativa": 0,
        "negativa": 1,
        "interrogativa": 0,
    }
    assert report["distribucion_final"] == target and report["lote_completo"]
    wrong = copy.deepcopy(report["preguntas"])
    wrong[0]["modalidad"] = "otra"
    with pytest.raises(ValidationError):
        validate_generation_output({"preguntas": wrong}, params)
    with pytest.raises(ValueError):
        validate_generation_output(
            {"preguntas": [question("afirmativa", i) for i in range(quantity)]}, params
        )
    refiller = BankRefiller(client.app.state.database, settings, provider)
    client.portal.call(refiller.request, bank.id, 11)
    claimed = client.portal.call(refiller.claim)
    # Configurar tamaño del trabajo para probar el control de publicación de esta cantidad.
    database.Bancos.update_one({"_id": bank.id}, {"$set": {"reposicion.cantidad": quantity}})
    claimed.reposicion.cantidad = quantity
    metrics = GenerationMetrics(
        **trace.model_dump(),
        creado_en=now(),
        latencia_ms=0,
        preguntas_solicitadas=quantity,
        preguntas_recibidas=quantity,
        preguntas_validas=quantity,
    )
    with pytest.raises(IncompleteBatch):
        client.portal.call(
            refiller.publish,
            claimed,
            GenerationOutput(preguntas=[question("afirmativa", i) for i in range(quantity)]),
            metrics,
        )
    assert database.BancoEjercicios.count_documents({}) == 11
    with pytest.raises(IncompleteBatch):
        client.portal.call(
            refiller.publish, claimed, GenerationOutput(preguntas=report["preguntas"][:-1]), metrics
        )
    assert database.BancoEjercicios.count_documents({}) == 11
    assert client.portal.call(
        refiller.publish, claimed, GenerationOutput(preguntas=report["preguntas"]), metrics
    )
    new = list(database.BancoEjercicios.find({"secuencia": {"$gt": 11}}).sort("secuencia", 1))
    expected_questions = {q["enunciado"]: q for q in report["preguntas"]}
    for document in new:
        content = GeneratedBankQuestion.model_validate(
            {k: document[k] for k in GeneratedBankQuestion.model_fields if k in document}
        )
        assert content.model_dump() == expected_questions[document["enunciado"]]
        assert document["generacion"] == trace.model_dump()
    assert (
        modality_counts(
            [
                GeneratedBankQuestion.model_validate(
                    {k: q[k] for k in GeneratedBankQuestion.model_fields if k in q}
                )
                for q in new
            ]
        )
        == target
    )
    assert (
        list(database.BancoEjercicios.find({"secuencia": {"$lte": 10}}).sort("_id", 1)) == previous
    )
