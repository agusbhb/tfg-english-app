"""Ensayo de restauración mediante la aplicación, limitado a BDs de prueba nuevas."""

import argparse
import os
import re
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bson import json_util  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pymongo import MongoClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.seed import seed_database  # noqa: E402


def image(database):
    content = {
        "documents": {
            name: list(database[name].find().sort("_id", 1))
            for name in sorted(database.list_collection_names())
        },
        "indexes": {
            name: database[name].index_information()
            for name in sorted(database.list_collection_names())
        },
    }
    return json_util.dumps(content, sort_keys=True)


def application(uri, name):
    return create_app(
        Settings(
            _env_file=None,
            mongodb_uri=uri,
            mongodb_db=name,
            jwt_secret=secrets.token_hex(32),
        )
    )


def prepare(uri, source, target, state):
    with MongoClient(uri, tz_aware=True) as mongo:
        if source in mongo.list_database_names() or target in mongo.list_database_names():
            raise ValueError("El ensayo requiere dos nombres de base nuevos")
        password = secrets.token_urlsafe(24)
        with TestClient(application(uri, source)) as client:
            client.portal.call(seed_database, client.app.state.database)
            subject = client.get("/asignaturas").json()[0]
            body = dict(
                email="backup@example.com",
                password=password,
                nombre="Ensayo backup",
                asignatura_id=subject["id"],
                nivel_id=subject["niveles"][0]["nivel_id"],
            )
            registered = client.post("/auth/register", json=body)
            assert registered.status_code == 201
            token = client.post(
                "/auth/login", json={"email": body["email"], "password": password}
            ).json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}
            topic = subject["niveles"][0]["temas"][0]["tema_id"]
            first = client.post("/tests", json={"tema_id": topic}, headers=headers).json()
            answers = {
                "respuestas": [
                    {"pregunta_id": q["pregunta_id"], "respuesta_dada": ""}
                    for q in first["preguntas"]
                ]
            }
            completed = client.post(
                f"/tests/{first['id']}/respuestas", json=answers, headers=headers
            )
            assert completed.status_code == 200
            pending = client.post("/tests", json={"tema_id": topic}, headers=headers).json()
            draft = {
                "pregunta_id": pending["preguntas"][0]["pregunta_id"],
                "respuesta_dada": "play",
            }
            assert (
                client.patch(
                    f"/tests/{pending['id']}/borrador", json=draft, headers=headers
                ).status_code
                == 200
            )
            pending = client.get(f"/tests/{pending['id']}", headers=headers).json()
            expected = dict(
                source=source,
                target=target,
                password=password,
                user=registered.json(),
                completed=completed.json(),
                pending=pending,
                image=image(mongo[source]),
            )
        state.write_text(json_util.dumps(expected), encoding="utf-8")
        state.chmod(0o600)
    print("Origen preparado mediante registro, login, entrega y borrador; destino vacío.")


def verify(uri, source, target, state):
    expected = json_util.loads(state.read_text(encoding="utf-8"))
    assert (expected["source"], expected["target"]) == (source, target)
    with MongoClient(uri, tz_aware=True) as mongo:
        # Comprobar índices ANTES del lifespan, para que la aplicación no los repare.
        assert image(mongo[target]) == expected["image"], (
            "Documentos o índices restaurados distintos"
        )
        assert image(mongo[source]) == expected["image"], "El origen ha cambiado"
        with TestClient(application(uri, target)) as client:
            login = client.post(
                "/auth/login",
                json={"email": "backup@example.com", "password": expected["password"]},
            )
            assert login.status_code == 200
            headers = {"Authorization": "Bearer " + login.json()["access_token"]}
            assert client.get("/users/me", headers=headers).json() == expected["user"]
            for kind in ("completed", "pending"):
                attempt = expected[kind]
                response = client.get(f"/tests/{attempt['id']}", headers=headers)
                assert response.status_code == 200 and response.json() == attempt
            pending = expected["pending"]
            answers = {
                "respuestas": [
                    {"pregunta_id": q["pregunta_id"], "respuesta_dada": ""}
                    for q in pending["preguntas"]
                ]
            }
            result = client.post(
                f"/tests/{pending['id']}/respuestas", json=answers, headers=headers
            )
            assert result.status_code == 200 and result.json()["estado"] == "completado"
        assert image(mongo[source]) == expected["image"]
    print("Restauración verificada: documentos e índices idénticos antes del arranque.")
    print("Login, perfil, resultado, borrador y entrega operativos en la base restaurada.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("source")
    parser.add_argument("target")
    parser.add_argument("state", type=Path)
    args = parser.parse_args()
    for name in (args.source, args.target):
        if not re.fullmatch(r"trainer_test_backup_[a-f0-9]{32}_(source|restored)", name):
            raise ValueError("Base fuera del ámbito aislado de ensayo")
    if args.source == args.target:
        raise ValueError("Origen y destino deben ser distintos")
    uri = os.environ["TEST_MONGODB_URI"]
    if os.environ.get("REQUIRE_MONGODB_TESTS") != "1":
        raise ValueError("Utiliza el servicio tests del Compose aislado")
    if args.action == "prepare":
        prepare(uri, args.source, args.target, args.state)
    else:
        verify(uri, args.source, args.target, args.state)


if __name__ == "__main__":
    main()
