import copy
from datetime import UTC, datetime

import pytest
from bson import BSON, ObjectId
from pydantic import ValidationError

from app.bank_contracts import (
    AcademicBankBinding,
    BankMetadataDocument,
    BankProgressDocument,
    BankQuestionDocument,
    ContentKey,
    GenerationMetrics,
    GenerationOutput,
    PrivateQuestionSnapshot,
)
from app.content import load_educational_sheet


@pytest.fixture
def question():
    return {
        "enunciado": "She ___ (go) to school every day.",
        "respuesta_principal": "goes",
        "solucion": {"respuestas_aceptadas": ["goes"], "sensible_mayusculas": False},
        "distractores": ["go", "going"],
    }


@pytest.fixture
def trace():
    sheet = load_educational_sheet(
        ContentKey(codigo_tema="present-simple", nivel_mcer="A1", version=1)
    )
    return {
        "generacion_id": ObjectId(),
        "modelo": {"proveedor": "simulado", "modelo": "sin-llamadas"},
        "prompt_id": ObjectId(),
        "prompt_version": 1,
        "ficha": sheet.referencia.model_dump(),
    }


def manual_question(question):
    return {
        **question,
        "banco_id": ObjectId(),
        "secuencia": 1,
        "version": 1,
        "origen": "manual",
        "validacion": {
            "estructural": "validada",
            "pedagogica": "revisada",
            "apta_multiple_choice": True,
        },
    }


def test_individual_questions_use_bson_ids_and_preserve_alternatives(question, trace):
    data = manual_question(question)
    data.update(
        enunciado="She ___ (practise/practice) tennis every Monday.",
        respuesta_principal="practises",
        solucion={"respuestas_aceptadas": ["practises", "practices"], "sensible_mayusculas": False},
        distractores=["practise", "practising"],
    )
    document = BankQuestionDocument.model_validate(data)
    assert isinstance(document.id, ObjectId)
    assert BSON.encode(document.model_dump(by_alias=True))
    assert document.solucion.respuestas_aceptadas == ["practises", "practices"]
    data.update(origen="llm", generacion=trace)
    assert BankQuestionDocument.model_validate(data).generacion.prompt_version == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "no_gap",
        "two_gaps",
        "four_underscores",
        "principal",
        "duplicate_accepted",
        "duplicate_distractor",
        "accepted_distractor",
        "one_distractor",
        "empty_answer",
        "blank_distractor",
        "long_answer",
        "string_boolean",
        "internal_id",
        "internal_state",
    ],
)
def test_generated_questions_reject_invalid_content_and_internal_metadata(question, mutation):
    if mutation == "no_gap":
        question["enunciado"] = "She goes to school."
    elif mutation == "two_gaps":
        question["enunciado"] += " ___"
    elif mutation == "four_underscores":
        question["enunciado"] = "She ____ (go)."
    elif mutation == "principal":
        question["respuesta_principal"] = "went"
    elif mutation == "duplicate_accepted":
        question["solucion"]["respuestas_aceptadas"].append(" GOES ")
    elif mutation == "duplicate_distractor":
        question["distractores"] = [" go ", "GO"]
    elif mutation == "accepted_distractor":
        question["distractores"][0] = " GOES "
    elif mutation == "one_distractor":
        question["distractores"].pop()
    elif mutation == "empty_answer":
        question["respuesta_principal"] = ""
    elif mutation == "blank_distractor":
        question["distractores"][0] = "  "
    elif mutation == "long_answer":
        question["respuesta_principal"] = "a" * 201
    elif mutation == "string_boolean":
        question["solucion"]["sensible_mayusculas"] = "false"
    elif mutation == "internal_id":
        question["_id"] = str(ObjectId())
    else:
        question["validacion"] = {"pedagogica": "revisada"}
    with pytest.raises(ValidationError):
        GenerationOutput(preguntas=[question])


def test_batch_rejects_duplicate_sentences_and_extra_root_metadata(question):
    duplicate = copy.deepcopy(question)
    duplicate["enunciado"] = "  SHE   ___ (GO) TO SCHOOL EVERY DAY. "
    with pytest.raises(ValidationError, match="repetidos"):
        GenerationOutput(preguntas=[question, duplicate])
    with pytest.raises(ValidationError):
        GenerationOutput(preguntas=[question], tokens=123)


def test_manual_fill_blanks_can_lack_distractors_without_being_choice_ready(question):
    data = manual_question(question)
    data["distractores"] = []
    data["validacion"].update(apta_multiple_choice=False, pedagogica="pendiente")
    assert not BankQuestionDocument.model_validate(data).validacion.apta_multiple_choice
    data["distractores"] = ["go"]
    with pytest.raises(ValidationError, match="cero o dos"):
        BankQuestionDocument.model_validate(data)
    data["distractores"] = []
    data["validacion"]["apta_multiple_choice"] = True
    with pytest.raises(ValidationError, match="revisión"):
        BankQuestionDocument.model_validate(data)


def test_choice_readiness_requires_review_not_only_different_strings(question):
    data = manual_question(question)
    data["validacion"]["pedagogica"] = "pendiente"
    with pytest.raises(ValidationError, match="revisión"):
        BankQuestionDocument.model_validate(data)


@pytest.mark.parametrize("origin,has_trace", [("llm", False), ("manual", True)])
def test_generation_provenance_is_required_only_for_llm(question, trace, origin, has_trace):
    data = manual_question(question)
    data.update(origen=origin, generacion=trace if has_trace else None)
    with pytest.raises(ValidationError, match="trazabilidad"):
        BankQuestionDocument.model_validate(data)


def test_bank_scope_and_curricular_binding_are_explicit(trace):
    bank = BankMetadataDocument(
        codigo_tema="present-simple",
        nivel_mcer_objetivo="A1",
        tema_id=ObjectId(),
        ficha=trace["ficha"],
    )
    binding = AcademicBankBinding(
        asignatura_id=ObjectId(), nivel_id=ObjectId(), tema_id=ObjectId(), banco_id=bank.id
    )
    assert binding.banco_id == bank.id and bank.publicado_hasta == 0
    with pytest.raises(ValidationError, match="ámbito"):
        BankMetadataDocument(
            codigo_tema="present-simple",
            nivel_mcer_objetivo="B2",
            tema_id=ObjectId(),
            ficha=trace["ficha"],
        )


@pytest.mark.parametrize("cursor", [-1, True, "10", 1.5])
def test_progress_cursor_is_a_nonnegative_integer(cursor):
    with pytest.raises(ValidationError):
        BankProgressDocument(
            usuario_id=ObjectId(), banco_id=ObjectId(), ultima_secuencia_reservada=cursor
        )


def test_progress_is_independent_per_user_and_bank():
    user, bank = ObjectId(), ObjectId()
    a = BankProgressDocument(usuario_id=user, banco_id=bank, ultima_secuencia_reservada=20)
    b = BankProgressDocument(usuario_id=ObjectId(), banco_id=bank)
    c = BankProgressDocument(usuario_id=user, banco_id=ObjectId())
    assert a.ultima_secuencia_reservada == 20
    assert b.ultima_secuencia_reservada == c.ultima_secuencia_reservada == 0


def test_metrics_include_failed_attempts_unknown_cost_and_bson_roundtrip(trace):
    data = {
        **trace,
        "creado_en": datetime.now(UTC),
        "latencia_ms": 150,
        "preguntas_solicitadas": 10,
        "preguntas_recibidas": 12,
        "preguntas_validas": 0,
        "errores": [{"codigo": "cantidad_incorrecta", "mensaje": "Se rechazó el lote"}],
    }
    metrics = GenerationMetrics.model_validate(data)
    assert metrics.coste_estimado_usd is metrics.tokens_entrada is None
    assert BSON.encode(metrics.model_dump())
    data.update(tokens_entrada=100, tokens_salida=500, coste_estimado_usd=0.002)
    assert GenerationMetrics.model_validate(data).coste_estimado_usd == 0.002


@pytest.mark.parametrize(
    "changes",
    [
        {"latencia_ms": -1},
        {"tokens_entrada": True},
        {"prompt_version": 0},
        {"coste_estimado_usd": float("nan")},
        {"coste_estimado_usd": -1.0},
        {"preguntas_validas": 3},
        {"errores": [{"codigo": "bad", "payload": "secret"}]},
    ],
)
def test_metrics_reject_incoherent_or_unsafe_shapes(trace, changes):
    data = dict(
        **trace,
        creado_en=datetime.now(UTC),
        latencia_ms=1,
        preguntas_solicitadas=2,
        preguntas_recibidas=2,
        preguntas_validas=2,
    )
    data.update(changes)
    with pytest.raises(ValidationError):
        GenerationMetrics.model_validate(data)


def test_private_snapshot_preserves_source_id_and_solution(question):
    source = ObjectId()
    data = {
        "origen": {
            "pregunta_id": str(source),
            "banco_id": ObjectId(),
            "pregunta_banco_id": source,
            "version_pregunta": 1,
            "secuencia": 11,
        },
        "pregunta": {
            "pregunta_id": str(source),
            "formato": "fill_blanks",
            "enunciado": question["enunciado"],
            "solucion": question["solucion"],
        },
    }
    snapshot = PrivateQuestionSnapshot.model_validate(data)
    assert snapshot.pregunta.solucion.respuestas_aceptadas == ["goes"]
    assert BSON.encode(snapshot.model_dump())
    data["pregunta"].update(
        formato="multiple_choice",
        opciones=[
            {"opcion_id": "b", "texto": "go"},
            {"opcion_id": "a", "texto": "goes"},
            {"opcion_id": "c", "texto": "going"},
        ],
        solucion={"opcion_id": "a"},
    )
    assert len(PrivateQuestionSnapshot.model_validate(data).pregunta.opciones) == 3
    data["pregunta"]["opciones"].pop()
    with pytest.raises(ValidationError, match="tres opciones"):
        PrivateQuestionSnapshot.model_validate(data)
    data["pregunta"]["opciones"].append({"opcion_id": "c", "texto": "going"})
    data["origen"]["pregunta_id"] = "q1"
    with pytest.raises(ValidationError, match="identificador"):
        PrivateQuestionSnapshot.model_validate(data)
