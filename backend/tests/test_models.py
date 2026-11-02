import copy
import secrets
from datetime import datetime

import pytest
from bson import ObjectId
from pydantic import ValidationError

from app.config import Settings
from app.models import (
    ExerciseBankDocument,
    SolvedExample,
    SubjectDocument,
    validate_academic_reference,
    validate_catalog,
)
from app.models import (
    TestDocument as StoredTest,
)
from app.seed import SEED_DATE, initial_documents


def shared_example():
    return {
        "ejemplo_id": ObjectId(),
        "objetivo": "gramatica",
        "presentacion": "texto_compartido",
        "texto_base": {"contenido": "Marta [h1] to school every day.", "origen": "manual"},
        "preguntas": [
            {
                "pregunta_id": "q1",
                "formato": "fill_blanks",
                "enunciado": "Complete gap [h1] using the present simple of 'go'.",
                "hueco_id": "h1",
                "solucion": {"respuestas_aceptadas": ["goes"], "sensible_mayusculas": False},
            }
        ],
    }


def test_initial_catalog_and_bank_are_valid_and_use_bson_ids():
    subject, bank, prompts = initial_documents()
    assert len(bank.ejemplos_resueltos) == 10
    assert isinstance(subject.model_dump(by_alias=True)["_id"], ObjectId)
    assert subject.model_dump(mode="json", by_alias=True)["_id"] == str(subject.id)
    assert subject.niveles[0].nombre_curso == "1.º ESO"
    validate_academic_reference(subject, bank.nivel_id, bank.tema_id)
    assert all(not prompt.activo and prompt.version == 1 for prompt in prompts)


def test_global_ids_and_catalog_membership_are_checked():
    subject, _, _ = initial_documents()
    second = subject.model_copy(deep=True)
    second.id = ObjectId()
    second.codigo = "otra"
    with pytest.raises(ValueError, match="únicos"):
        validate_catalog([subject, second])
    invalid = subject.model_dump(by_alias=True)
    invalid["niveles"][0]["temas"][0]["tema_id"] = subject.niveles[0].nivel_id
    with pytest.raises(ValidationError, match="únicos"):
        SubjectDocument.model_validate(invalid)
    with pytest.raises(ValueError, match="pertenecer"):
        validate_academic_reference(subject, ObjectId())
    with pytest.raises(ValueError, match="pertenecer"):
        validate_academic_reference(subject, subject.niveles[0].nivel_id, ObjectId())


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_text",
        "blank_text",
        "missing_marker",
        "extra_marker",
        "repeated_marker",
        "missing_gap",
        "duplicate_question",
        "duplicate_gap",
        "empty_answers",
        "blank_answer",
        "independent_text",
        "independent_gap",
    ],
)
def test_activity_rejects_inconsistent_shapes(mutation):
    data = shared_example()
    if mutation == "missing_text":
        data["texto_base"] = None
    elif mutation == "blank_text":
        data["texto_base"]["contenido"] = "   "
    elif mutation == "missing_marker":
        data["texto_base"]["contenido"] = "Marta goes to school."
    elif mutation == "extra_marker":
        data["texto_base"]["contenido"] += " [h2]"
    elif mutation == "repeated_marker":
        data["texto_base"]["contenido"] += " [h1]"
    elif mutation == "missing_gap":
        data["preguntas"][0]["hueco_id"] = None
    elif mutation in {"duplicate_question", "duplicate_gap"}:
        data["preguntas"].append(copy.deepcopy(data["preguntas"][0]))
        if mutation == "duplicate_gap":
            data["preguntas"][1]["pregunta_id"] = "q2"
    elif mutation == "empty_answers":
        data["preguntas"][0]["solucion"]["respuestas_aceptadas"] = []
    elif mutation == "blank_answer":
        data["preguntas"][0]["solucion"]["respuestas_aceptadas"] = ["   "]
    elif mutation == "independent_text":
        data["presentacion"] = "frases_independientes"
    elif mutation == "independent_gap":
        data["presentacion"] = "frases_independientes"
        data["texto_base"] = None
    with pytest.raises(ValidationError):
        SolvedExample.model_validate(data)


def test_shared_cloze_and_reading_without_gaps_are_supported():
    assert SolvedExample.model_validate(shared_example()).preguntas[0].hueco_id == "h1"
    reading = shared_example()
    reading["objetivo"] = "comprension_lectora"
    reading["texto_base"]["contenido"] = "Marta takes the bus to school every day."
    reading["preguntas"] = [
        {
            "pregunta_id": "q1",
            "formato": "multiple_choice",
            "enunciado": "How does Marta travel to school?",
            "opciones": [
                {"opcion_id": "a", "texto": "By bus"},
                {"opcion_id": "b", "texto": "On foot"},
            ],
            "solucion": {"opcion_id": "a"},
        }
    ]
    SolvedExample.model_validate(reading)
    reading["preguntas"][0]["solucion"]["opcion_id"] = "c"
    with pytest.raises(ValidationError, match="opciones"):
        SolvedExample.model_validate(reading)
    reading["preguntas"][0]["solucion"]["opcion_id"] = "a"
    reading["preguntas"][0]["opciones"][1]["opcion_id"] = "a"
    with pytest.raises(ValidationError, match="opciones"):
        SolvedExample.model_validate(reading)


def test_bank_rejects_duplicate_examples():
    _, bank, _ = initial_documents()
    data = bank.model_dump(by_alias=True)
    data["ejemplos_resueltos"].append(copy.deepcopy(data["ejemplos_resueltos"][0]))
    with pytest.raises(ValidationError, match="ejemplo"):
        ExerciseBankDocument.model_validate(data)


def fixed_test():
    subject, bank, _ = initial_documents()
    return {
        "usuario_id": ObjectId(),
        "asignatura_id": subject.id,
        "nivel_id": bank.nivel_id,
        "tema_id": bank.tema_id,
        "objetivo": "gramatica",
        "presentacion": "frases_independientes",
        "preguntas_generadas": bank.ejemplos_resueltos[0].model_dump()["preguntas"],
        "fechas": {"generacion": SEED_DATE, "resolucion": None},
    }


def test_test_lifecycle_and_prompt_pairs():
    data = fixed_test()
    pending = StoredTest.model_validate(data)
    assert pending.estadisticas is None and pending.fechas.resolucion is None
    data["estado"] = "completado"
    with pytest.raises(ValidationError, match="completado"):
        StoredTest.model_validate(data)
    data["estadisticas"] = {"aciertos": 1, "total_preguntas": 1}
    data["fechas"]["resolucion"] = SEED_DATE
    data["respuestas_alumno"] = [
        {"pregunta_id": "q1", "respuesta_dada": "play", "es_correcta": True}
    ]
    StoredTest.model_validate(data)
    data["estadisticas"]["aciertos"] = 0
    with pytest.raises(ValidationError, match="estadísticas"):
        StoredTest.model_validate(data)
    data = fixed_test()
    data["prompt_generacion_id"] = ObjectId()
    with pytest.raises(ValidationError, match="versión"):
        StoredTest.model_validate(data)


def test_dates_must_be_timezone_aware():
    data = fixed_test()
    data["fechas"]["generacion"] = datetime(2026, 10, 7)
    with pytest.raises(ValidationError, match="zona horaria"):
        StoredTest.model_validate(data)


@pytest.mark.parametrize("secret", ["short", "CHANGE_ME_GENERATE_A_RANDOM_SECRET", "example" * 10])
def test_settings_reject_placeholder_secrets_without_exposing_values(secret):
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, mongodb_uri="mongodb://localhost", jwt_secret=secret)
    assert "JWT_SECRET" in str(error.value)
    assert secret not in str(error.value)


def test_settings_require_jwt_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ValidationError, match="jwt_secret"):
        Settings(_env_file=None, mongodb_uri="mongodb://localhost")


@pytest.mark.parametrize(
    "origin", ["*", "https://*.example.com", "http://localhost/path", "https://x/"]
)
def test_cors_settings_reject_wildcards_and_paths(origin):
    with pytest.raises(ValidationError, match="orígenes"):
        Settings(
            _env_file=None,
            mongodb_uri="mongodb://localhost",
            jwt_secret=secrets.token_hex(32),
            allowed_origins=[origin],
        )
