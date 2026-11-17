import os
from uuid import uuid4

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo.errors import DuplicateKeyError

from app.config import Settings
from app.main import create_app
from app.seed import initial_generation_prompt, seed_database

pytestmark = pytest.mark.integration


def test_empty_database_seed_and_indexes_are_idempotent(api):
    client, db, _ = api
    assert db.Bancos.count_documents({}) == 1
    bank = db.Bancos.find_one()
    assert "edicion" not in bank
    assert bank["nivel_mcer_objetivo"] == "A1"
    assert [q["secuencia"] for q in db.BancoEjercicios.find().sort("secuencia", 1)] == list(
        range(1, 11)
    )
    assert all(q["origen"] == "manual" for q in db.BancoEjercicios.find())
    prompt = db.Prompts.find_one({"activo": True})
    expected = initial_generation_prompt()
    assert prompt["version"] == 2 and prompt["nombre"] == "generacion_banco"
    assert prompt["contenido_prompt"] == expected.contenido_prompt
    assert prompt["variables_requeridas"] == expected.variables_requeridas
    assert db.Prompts.count_documents({"nombre": "generacion_banco", "version": 1}) == 0
    before = {n: list(db[n].find().sort("_id", 1)) for n in db.list_collection_names()}
    indexes = {n: db[n].index_information() for n in before}
    client.portal.call(seed_database, client.app.state.database)
    assert before == {n: list(db[n].find().sort("_id", 1)) for n in before}
    assert indexes == {n: db[n].index_information() for n in before}


def test_unique_bank_scope_and_question_sequence(api):
    _, db, _ = api
    bank = db.Bancos.find_one()
    bank["_id"] = ObjectId()
    with pytest.raises(DuplicateKeyError):
        db.Bancos.insert_one(bank)
    question = db.BancoEjercicios.find_one()
    question["_id"] = ObjectId()
    with pytest.raises(DuplicateKeyError):
        db.BancoEjercicios.insert_one(question)


def test_seed_recovers_partial_initialization_without_overwriting(api):
    client, db, _ = api
    original = db.BancoEjercicios.find_one({"secuencia": 1})
    db.BancoEjercicios.delete_many({"secuencia": {"$gt": 4}})
    db.Bancos.delete_many({})
    client.portal.call(seed_database, client.app.state.database)
    assert db.BancoEjercicios.count_documents({}) == 10
    assert db.BancoEjercicios.find_one({"secuencia": 1}) == original
    assert db.Bancos.find_one()["publicado_hasta"] == 10


def test_bank_selection_uses_curricular_target_not_an_arbitrary_mcer(api, registration):
    client, db, _ = api
    client.post("/auth/register", json=registration)
    token = client.post(
        "/auth/login", json={k: registration[k] for k in ("email", "password")}
    ).json()["access_token"]
    bank = db.Bancos.find_one()
    topic = bank["tema_id"]
    bank.update(_id=ObjectId(), nivel_mcer_objetivo="B2")
    bank["ficha"]["nivel_mcer"] = "B2"
    db.Bancos.insert_one(bank)
    db.Bancos.delete_one({"tema_id": topic, "nivel_mcer_objetivo": "A1"})
    result = client.post(
        "/tests", json={"tema_id": str(topic)}, headers={"Authorization": f"Bearer {token}"}
    )
    assert result.status_code == 404
    assert db.Tests.count_documents({}) == 0


def test_legacy_snapshots_remain_readable_and_gradable(api, registration):
    client, db, _ = api
    client.post("/auth/register", json=registration)
    token = client.post(
        "/auth/login", json={k: registration[k] for k in ("email", "password")}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    topic = str(db.Bancos.find_one()["tema_id"])
    test = client.post("/tests", json={"tema_id": topic}, headers=headers).json()
    document = db.Tests.find_one({"_id": ObjectId(test["id"])})
    origins = []
    for i, question in enumerate(document["preguntas_generadas"], 1):
        question["pregunta_id"] = f"q{i}"
        origins.append(
            {
                "pregunta_id": f"q{i}",
                "banco_id": ObjectId(),
                "ejemplo_id": ObjectId(),
                "pregunta_original_id": f"original{i}",
            }
        )
    document["origenes_preguntas"] = origins
    document["respuestas_borrador"] = [{"pregunta_id": "q1", "respuesta_dada": "play"}]
    db.Tests.replace_one({"_id": document["_id"]}, document)
    db.BancoEjercicios.delete_many({})
    loaded = client.get(f"/tests/{test['id']}", headers=headers).json()
    assert loaded["respuestas_borrador"] == document["respuestas_borrador"]
    assert loaded["resultados"] is None
    answers = {
        "respuestas": [
            {
                "pregunta_id": q["pregunta_id"],
                "respuesta_dada": q["solucion"]["respuestas_aceptadas"][0],
            }
            for q in document["preguntas_generadas"]
        ]
    }
    result = client.post(f"/tests/{test['id']}/respuestas", json=answers, headers=headers)
    assert result.status_code == 200 and result.json()["aciertos"] == 10


@pytest.mark.parametrize("with_documents", [True, False])
def test_legacy_database_is_rejected_before_writes(mongo_client, with_documents):
    name = "trainer_test_legacy_" + uuid4().hex
    db = mongo_client[name]
    if with_documents:
        db.BancoEjercicios.insert_one({"ejemplos_resueltos": []})
    else:
        db.BancoEjercicios.create_index("tema_id", unique=True, name="tema_unique")
    before = list(db.BancoEjercicios.find())
    settings = Settings(
        _env_file=None,
        mongodb_uri=os.environ["TEST_MONGODB_URI"],
        mongodb_db=name,
        jwt_secret="isolated-test-secret-" + uuid4().hex,
    )
    try:
        with pytest.raises(ValueError, match="Banco antiguo"):
            with TestClient(create_app(settings)):
                pass
        assert list(db.BancoEjercicios.find()) == before
        assert db.list_collection_names() == ["BancoEjercicios"]
    finally:
        mongo_client.drop_database(name)
