from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

import pytest
from bson import ObjectId
from pydantic import ValidationError

from app.bank_contracts import ContentKey, GenerationOutput
from app.content import EducationalSheet, load_educational_sheet
from app.prompts import (
    GenerationParameters,
    load_generation_prompt,
    render_generation_prompt,
    validate_generation_output,
    validate_generation_template,
)
from app.seed import SUBJECT_ID, initial_generation_prompt, seed_database


@pytest.fixture
def parameters():
    return GenerationParameters(
        codigo_tema="present-simple", tema="Present Simple", nivel_mcer="A1", cantidad=1
    )


@pytest.mark.parametrize("version", [1, 2])
def test_language_and_legacy_rendering(parameters, version):
    assert parameters.idioma_objetivo == "Inglés"
    prompt = initial_generation_prompt(version)
    rendered = render_generation_prompt(
        prompt, parameters, load_educational_sheet(parameters.content_key())
    )
    assert "{idioma_objetivo}" not in rendered
    if version == 2:
        assert "idioma_objetivo" in prompt.variables_requeridas
        assert "Idioma objetivo: Inglés" in rendered
        assert "y los dos distractores en Inglés." in rendered
    else:
        assert "idioma_objetivo" not in prompt.variables_requeridas
        assert rendered.startswith("Eres un autor de ejercicios de inglés")


def test_language_is_internal_and_mvp_is_limited_to_english(parameters):
    from app.schemas import CreateTestRequest

    with pytest.raises(ValidationError):
        GenerationParameters(**{**parameters.model_dump(), "idioma_objetivo": "otro"})
    with pytest.raises(ValidationError):
        CreateTestRequest(tema_id=ObjectId(), idioma_objetivo="Inglés")


@pytest.mark.parametrize("version", [1, 2])
def test_variables_must_match_the_declared_version(version):
    prompt = initial_generation_prompt(version)
    prompt.variables_requeridas = initial_generation_prompt(3 - version).variables_requeridas
    with pytest.raises(ValueError, match="declaradas"):
        validate_generation_template(prompt)


def test_unknown_template_version_requires_an_explicit_contract():
    prompt = initial_generation_prompt()
    prompt.version = 3
    with pytest.raises(ValueError, match="no soportada"):
        validate_generation_template(prompt)


def test_full_seed_template_renders_with_separate_sheet_and_matches_output_contract(parameters):
    prompt = initial_generation_prompt()
    sheet = load_educational_sheet(parameters.content_key())
    rendered = render_generation_prompt(prompt, parameters, sheet)
    assert len(prompt.contenido_prompt) > 3000
    assert sheet.contenido in rendered
    assert "Cantidad exacta de preguntas: 1" in rendered
    for requirement in ["inequívocamente", "distractores", "metadatos internos", "MCER", "JSON"]:
        assert requirement in rendered
    assert all("{" + key + "}" not in rendered for key in prompt.variables_requeridas)
    # El objeto de ejemplo de la plantilla también debe cumplir el esquema real.
    start = rendered.index('{\n  "preguntas"')
    end = rendered.index("\nLÍMITES DEL CONTRATO")
    example = GenerationOutput.model_validate_json(rendered[start:end].strip())
    assert validate_generation_output(example.model_dump(), parameters) == example


def test_sheet_is_versioned_hashed_and_does_not_assign_school_year(parameters):
    sheet = load_educational_sheet(parameters.content_key())
    assert sheet.referencia.version == 1 and sheet.referencia.nivel_mcer == "A1"
    assert sheet.referencia.sha256 == sha256(sheet.contenido.encode()).hexdigest()
    for heading in ["Objetivos", "Reglas", "Ejemplos", "Errores", "Restricciones"]:
        assert heading.lower() in sheet.contenido.lower()
    assert "no una equivalencia" in sheet.contenido


@pytest.mark.parametrize(
    "topic,level,version",
    [("past-simple", "A1", 1), ("present-simple", "B2", 1), ("present-simple", "A1", 2)],
)
def test_unknown_sheet_has_no_fallback(topic, level, version):
    with pytest.raises(ValueError, match="No existe"):
        load_educational_sheet(ContentKey(codigo_tema=topic, nivel_mcer=level, version=version))


@pytest.mark.parametrize("topic", ["../present-simple", "/etc/passwd", "present-simple/A1", "a.b"])
def test_content_keys_cannot_be_file_paths(topic):
    with pytest.raises(ValidationError):
        ContentKey(codigo_tema=topic, nivel_mcer="A1", version=1)


def test_sheet_hash_rejects_changed_content(parameters):
    sheet = load_educational_sheet(parameters.content_key())
    with pytest.raises(ValidationError, match="hash"):
        EducationalSheet(referencia=sheet.referencia, contenido=sheet.contenido + "modificado")


def test_sheet_variables_are_not_interpreted_recursively(parameters):
    original = load_educational_sheet(parameters.content_key())
    content = original.contenido + "\nTexto literal: {otra_variable} y {{json}}."
    reference = original.referencia.model_copy(
        update={"sha256": sha256(content.encode()).hexdigest()}
    )
    sheet = EducationalSheet(referencia=reference, contenido=content)
    assert "{otra_variable} y {{json}}" in render_generation_prompt(
        initial_generation_prompt(), parameters, sheet
    )


@pytest.mark.parametrize(
    "field", ["{desconocida}", "{tema.__class__}", "{tema[0]}", "{tema!r}", "{tema:>10}", "{", "{}"]
)
def test_template_rejects_unknown_variables_attributes_and_format_operations(field):
    prompt = initial_generation_prompt()
    prompt.contenido_prompt += "\n" + field
    with pytest.raises(ValueError):
        validate_generation_template(prompt)


def test_declared_variables_must_match_template(parameters):
    prompt = initial_generation_prompt()
    prompt.variables_requeridas.append("tema")
    with pytest.raises(ValueError, match="declaradas"):
        validate_generation_template(prompt)
    prompt = initial_generation_prompt()
    prompt.contenido_prompt = prompt.contenido_prompt.replace("{tema}", "tema fijo")
    with pytest.raises(ValueError, match="todas"):
        validate_generation_template(prompt)
    prompt = initial_generation_prompt()
    prompt.activo = False
    with pytest.raises(ValueError, match="activa"):
        render_generation_prompt(
            prompt, parameters, load_educational_sheet(parameters.content_key())
        )


def test_ficha_scope_must_match_generation_parameters(parameters):
    sheet = load_educational_sheet(parameters.content_key())
    parameters.nivel_mcer = "B2"
    with pytest.raises(ValueError, match="parámetros"):
        render_generation_prompt(initial_generation_prompt(), parameters, sheet)


@pytest.mark.parametrize("count", [0, 51, True, "10"])
def test_generation_parameters_have_bounded_strict_counts(count):
    with pytest.raises(ValidationError):
        GenerationParameters(
            codigo_tema="present-simple", tema="Present Simple", nivel_mcer="A1", cantidad=count
        )


def test_output_must_match_requested_count(parameters):
    with pytest.raises(ValueError, match="cantidad"):
        validate_generation_output(
            {
                "preguntas": [
                    {
                        "enunciado": f"{subject} ___ (go) to school.",
                        "respuesta_principal": "goes",
                        "solucion": {
                            "respuestas_aceptadas": ["goes"],
                            "sensible_mayusculas": False,
                        },
                        "distractores": ["go", "going"],
                    }
                    for subject in ["She", "He"]
                ]
            },
            parameters,
        )


@pytest.mark.integration
def test_runtime_reads_database_prompt_and_never_falls_back_to_seed(api, parameters):
    client, db, _ = api
    with pytest.raises(ValueError, match="identificador"):
        client.portal.call(load_generation_prompt, client.app.state.database, {"$ne": None})
    prompt = client.portal.call(load_generation_prompt, client.app.state.database, SUBJECT_ID)
    assert prompt.nombre == "generacion_banco" and prompt.version == 2
    db.Prompts.update_one(
        {"_id": prompt.id},
        {
            "$set": {
                "contenido_prompt": prompt.contenido_prompt + "\nEdición conservada en MongoDB."
            }
        },
    )
    loaded = client.portal.call(load_generation_prompt, client.app.state.database, SUBJECT_ID)
    rendered = render_generation_prompt(
        loaded, parameters, load_educational_sheet(parameters.content_key())
    )
    assert rendered.endswith("Edición conservada en MongoDB.")
    db.Prompts.delete_one({"_id": prompt.id})
    with pytest.raises(ValueError, match="única"):
        client.portal.call(load_generation_prompt, client.app.state.database, SUBJECT_ID)


@pytest.mark.integration
def test_seed_preserves_edits_and_disabled_template(api):
    client, db, _ = api
    prompt = db.Prompts.find_one({"nombre": "generacion_banco"})
    db.Prompts.update_one(
        {"_id": prompt["_id"]},
        {"$set": {"activo": False, "contenido_prompt": "Edición manual; no sobrescribir"}},
    )
    before = list(db.Prompts.find().sort("_id", 1))
    client.portal.call(seed_database, client.app.state.database)
    client.portal.call(seed_database, client.app.state.database)
    assert list(db.Prompts.find().sort("_id", 1)) == before
    with pytest.raises(ValueError, match="única"):
        client.portal.call(load_generation_prompt, client.app.state.database, SUBJECT_ID)


@pytest.mark.integration
def test_seed_does_not_activate_another_template_when_existing_generation_is_active(api):
    client, db, _ = api
    db.Prompts.delete_one({"nombre": "generacion_banco"})
    draft = db.Prompts.find_one({"tipo": "generacion"})
    db.Prompts.update_one({"_id": draft["_id"]}, {"$set": {"activo": True}})
    client.portal.call(seed_database, client.app.state.database)
    assert db.Prompts.count_documents({"tipo": "generacion", "activo": True}) == 1
    assert db.Prompts.find_one({"nombre": "generacion_banco"})["activo"] is False
    with pytest.raises(ValueError, match="familia"):
        client.portal.call(load_generation_prompt, client.app.state.database, SUBJECT_ID)


@pytest.mark.integration
def test_ambiguous_active_versions_fail_closed(api):
    client, db, _ = api
    prompt = db.Prompts.find_one({"nombre": "generacion_banco"})
    prompt.update(_id=ObjectId(), version=3)
    db.Prompts.insert_one(prompt)
    client.portal.call(seed_database, client.app.state.database)
    with pytest.raises(ValueError, match="única"):
        client.portal.call(load_generation_prompt, client.app.state.database, SUBJECT_ID)


@pytest.mark.integration
def test_concurrent_seed_inserts_one_operational_version(api):
    client, db, _ = api
    db.Prompts.delete_one({"nombre": "generacion_banco"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(
            pool.map(
                lambda _: client.portal.call(seed_database, client.app.state.database), range(2)
            )
        )
    assert db.Prompts.count_documents({"nombre": "generacion_banco"}) == 1
    assert db.Prompts.count_documents({"tipo": "generacion", "activo": True}) == 1


@pytest.mark.integration
@pytest.mark.parametrize("active", [False, True])
def test_seed_preserves_existing_v1_text_and_state(api, parameters, active):
    client, db, _ = api
    db.Prompts.delete_many({"nombre": "generacion_banco"})
    legacy = initial_generation_prompt(1)
    legacy.activo = active
    legacy.contenido_prompt += "\nEdición previa conservada."
    db.Prompts.insert_one(legacy.model_dump(by_alias=True))
    before = db.Prompts.find_one({"_id": legacy.id})
    client.portal.call(seed_database, client.app.state.database)
    after_seed = list(db.Prompts.find().sort("_id", 1))
    client.portal.call(seed_database, client.app.state.database)
    assert list(db.Prompts.find().sort("_id", 1)) == after_seed
    assert db.Prompts.find_one({"_id": legacy.id}) == before
    latest = db.Prompts.find_one({"nombre": "generacion_banco", "version": 2})
    assert latest["activo"] is not active
    loaded = client.portal.call(load_generation_prompt, client.app.state.database, SUBJECT_ID)
    assert loaded.version == (1 if active else 2)
    assert "{idioma_objetivo}" not in render_generation_prompt(
        loaded, parameters, load_educational_sheet(parameters.content_key())
    )


@pytest.mark.integration
def test_controlled_activation_of_v2_and_repeated_seed(api):
    client, db, _ = api
    db.Prompts.delete_many({"nombre": "generacion_banco"})
    legacy = initial_generation_prompt(1)
    db.Prompts.insert_one(legacy.model_dump(by_alias=True))
    client.portal.call(seed_database, client.app.state.database)
    # Simula mantenimiento con escritores detenidos: desactivar antes de activar.
    db.Prompts.update_many(
        {"asignatura_id": SUBJECT_ID, "tipo": "generacion", "activo": True},
        {"$set": {"activo": False}},
    )
    with pytest.raises(ValueError, match="única"):
        client.portal.call(load_generation_prompt, client.app.state.database, SUBJECT_ID)
    result = db.Prompts.update_one(
        {"asignatura_id": SUBJECT_ID, "nombre": "generacion_banco", "version": 2},
        {"$set": {"activo": True}},
    )
    assert result.modified_count == 1
    before = list(db.Prompts.find().sort("_id", 1))
    client.portal.call(seed_database, client.app.state.database)
    assert list(db.Prompts.find().sort("_id", 1)) == before
    assert db.Prompts.count_documents({"tipo": "generacion", "activo": True}) == 1
    assert (
        client.portal.call(load_generation_prompt, client.app.state.database, SUBJECT_ID).version
        == 2
    )
