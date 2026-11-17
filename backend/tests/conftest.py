import os
import secrets
from uuid import uuid4

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient
from pymongo import MongoClient

from app.config import Settings
from app.main import create_app
from app.seed import seed_database


@pytest.fixture(scope="session")
def mongo_client():
    uri = os.environ.get("TEST_MONGODB_URI")
    if not uri:
        message = "Define TEST_MONGODB_URI para una instancia MongoDB exclusiva de pruebas"
        if os.environ.get("REQUIRE_MONGODB_TESTS") == "1":
            pytest.fail(message)
        pytest.skip(message)
    client = MongoClient(uri, tz_aware=True, serverSelectionTimeoutMS=5000)
    try:
        client.admin.command("ping")
        yield client
    finally:
        client.close()


@pytest.fixture
def api(mongo_client):
    # Nunca leer MONGODB_DB de desarrollo. Cada prueba crea su propia BD.
    name = f"trainer_test_{uuid4().hex}"
    settings = Settings(
        _env_file=None,
        mongodb_uri=os.environ["TEST_MONGODB_URI"],
        mongodb_db=name,
        jwt_secret=secrets.token_hex(32),
        access_token_expire_minutes=30,
        allowed_origins=["http://localhost:5173"],
    )
    app = create_app(settings)
    try:
        with TestClient(app) as client:
            client.portal.call(seed_database, app.state.database)
            yield client, mongo_client[name], settings
    finally:
        assert name.startswith("trainer_test_")
        mongo_client.drop_database(name)


@pytest.fixture
def registration(api):
    client, _, _ = api
    subject = client.get("/asignaturas").json()[0]
    return {
        "email": "alumno@example.com",
        "password": "Una contraseña de prueba 123",
        "nombre": "Alumno de prueba",
        "asignatura_id": subject["id"],
        "nivel_id": subject["niveles"][0]["nivel_id"],
    }


@pytest.fixture
def publish_bank(api):
    """Banco sintético ampliado solo en la BD aleatoria de cada prueba."""
    database = api[1]

    def publish(total=20):
        bank = database.Bancos.find_one()
        template = database.BancoEjercicios.find_one({"banco_id": bank["_id"]})
        for sequence in range(11, total + 1):
            question = dict(template)
            question.update(
                _id=ObjectId(),
                secuencia=sequence,
                enunciado=f"Example {sequence}: {template['enunciado']}",
            )
            database.BancoEjercicios.update_one(
                {"banco_id": bank["_id"], "secuencia": sequence},
                {"$setOnInsert": question},
                upsert=True,
            )
        database.Bancos.update_one({"_id": bank["_id"]}, {"$set": {"publicado_hasta": total}})
        return database.Bancos.find_one({"_id": bank["_id"]})

    return publish
