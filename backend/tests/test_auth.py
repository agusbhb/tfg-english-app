from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from bson import ObjectId
from pymongo.errors import ConnectionFailure

from app.security import PASSWORD_HASH, create_access_token
from app.seed import seed_database

pytestmark = pytest.mark.integration


def login(client, registration):
    return client.post(
        "/auth/login",
        json={key: registration[key] for key in ("email", "password")},
    )


def test_registration_persistence_and_profile(api, registration):
    client, database, settings = api
    registration["email"] = "  Alumno+clase@EXAMPLE.COM  "
    response = client.post("/auth/register", json=registration)
    assert response.status_code == 201
    profile = response.json()
    assert profile["email"] == "alumno+clase@example.com"
    assert profile["nivel_mcer"] is None
    assert "password" not in response.text
    stored = database.Usuarios.find_one({"_id": ObjectId(profile["id"])})
    assert isinstance(stored["perfil_academico"]["nivel_id"], ObjectId)
    assert stored["creado_en"].utcoffset() == timedelta(0)
    assert stored["password_hash"].startswith("$argon2id$")
    assert PASSWORD_HASH.verify(registration["password"], stored["password_hash"])
    assert registration["password"] not in str(stored)
    token_response = login(client, registration)
    assert token_response.status_code == 200
    token = token_response.json()
    assert token["token_type"] == "bearer" and token["expires_in"] == 1800
    claims = jwt.decode(
        token["access_token"], settings.jwt_secret.get_secret_value(), algorithms=["HS256"]
    )
    assert set(claims) == {"sub", "iat", "exp"}
    me = client.get("/users/me", headers={"Authorization": f"Bearer {token['access_token']}"})
    assert me.status_code == 200
    assert me.json() == profile
    assert "password_hash" not in me.text


def test_duplicate_normalized_email(api, registration):
    client, database, _ = api
    assert client.post("/auth/register", json=registration).status_code == 201
    registration["email"] = "  ALUMNO@EXAMPLE.COM  "
    assert client.post("/auth/register", json=registration).status_code == 409
    assert database.Usuarios.count_documents({}) == 1


def test_concurrent_registration_uses_unique_index(api, registration):
    client, database, _ = api
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: client.post("/auth/register", json=registration), range(2))
        )
    assert sorted(response.status_code for response in results) == [201, 409]
    assert database.Usuarios.count_documents({}) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"email": "no-es-un-email"},
        {"password": "corta-928"},
        {"password": "x" * 129},
        {"password": {"password": "secreto-anidado"}},
        {"asignatura_id": "incorrecto"},
        {"asignatura_id": str(ObjectId())},
        {"nivel_id": "incorrecto"},
        {"nivel_id": str(ObjectId())},
        {"rol": "admin"},
        {"nivel_mcer": "C2"},
    ],
)
def test_invalid_register_never_reflects_password(api, registration, changes):
    client, database, _ = api
    registration.update(changes)
    response = client.post("/auth/register", json=registration)
    assert response.status_code == 422
    assert str(registration["password"]) not in response.text
    assert "secreto-anidado" not in response.text
    assert (
        all("input" not in error for error in response.json()["detail"])
        if isinstance(response.json()["detail"], list)
        else True
    )
    assert database.Usuarios.count_documents({}) == 0


def test_level_from_another_subject_is_rejected(api, registration):
    client, database, _ = api
    foreign_level = ObjectId()
    database.Asignaturas.insert_one(
        {
            "_id": ObjectId(),
            "codigo": "otra",
            "nombre": "Otra asignatura",
            "niveles": [{"nivel_id": foreign_level, "nombre_curso": "2.º ESO", "temas": []}],
        }
    )
    registration["nivel_id"] = str(foreign_level)
    assert client.post("/auth/register", json=registration).status_code == 422


@pytest.mark.parametrize("length", [12, 128])
def test_password_boundaries_and_spaces_are_preserved(api, registration, length):
    client, _, _ = api
    registration["password"] = " " + "p" * (length - 2) + " "
    assert client.post("/auth/register", json=registration).status_code == 201
    assert login(client, registration).status_code == 200


def test_invalid_login_is_identical_for_unknown_user(api, registration):
    client, _, _ = api
    client.post("/auth/register", json=registration)
    wrong = {"email": registration["email"], "password": "Otra contraseña incorrecta"}
    known = client.post("/auth/login", json=wrong)
    wrong["email"] = "desconocido@example.com"
    unknown = client.post("/auth/login", json=wrong)
    assert known.status_code == unknown.status_code == 401
    assert known.json() == unknown.json() == {"detail": "Credenciales inválidas"}
    assert known.headers["www-authenticate"] == "Bearer"


def test_absent_and_malformed_tokens(api):
    client, _, _ = api
    for authorization in (None, "Basic abc", "Bearer invalid", "Bearer"):
        headers = {"Authorization": authorization} if authorization else {}
        response = client.get("/users/me", headers=headers)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "variation",
    [
        "expired",
        "signature",
        "missing_sub",
        "missing_iat",
        "missing_exp",
        "bad_sub",
        "string_iat",
        "string_exp",
        "float_exp",
        "bool_iat",
        "null_exp",
        "object_iat",
        "array_exp",
        "object_sub",
        "future_iat",
        "algorithm",
    ],
)
def test_invalid_signed_tokens(api, registration, variation):
    client, _, settings = api
    user = client.post("/auth/register", json=registration).json()
    now = int(datetime.now(UTC).timestamp())
    claims = {"sub": user["id"], "iat": now, "exp": now + 1800}
    key = settings.jwt_secret.get_secret_value()
    algorithm = "HS256"
    if variation == "expired":
        claims.update(iat=now - 3600, exp=now - 1800)
    elif variation == "signature":
        key = "otro-secreto-aleatorio-de-longitud-suficiente"
    elif variation.startswith("missing_"):
        claims.pop(variation.removeprefix("missing_"))
    elif variation == "bad_sub":
        claims["sub"] = "invalid"
    elif variation == "string_iat":
        claims["iat"] = str(now)
    elif variation == "string_exp":
        claims["exp"] = str(now + 1800)
    elif variation == "float_exp":
        claims["exp"] = float(now + 1800)
    elif variation == "bool_iat":
        claims["iat"] = True
    elif variation == "null_exp":
        claims["exp"] = None
    elif variation == "object_iat":
        claims["iat"] = {"value": now}
    elif variation == "array_exp":
        claims["exp"] = [now + 1800]
    elif variation == "object_sub":
        claims["sub"] = {"id": user["id"]}
    elif variation == "future_iat":
        claims["iat"] = now + 100
    elif variation == "algorithm":
        algorithm = "HS384"
    token = jwt.encode(claims, key, algorithm=algorithm)
    response = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_deleted_user_token_is_rejected(api, registration):
    client, database, settings = api
    profile = client.post("/auth/register", json=registration).json()
    token = create_access_token(ObjectId(profile["id"]), settings)
    database.Usuarios.delete_one({"_id": ObjectId(profile["id"])})
    assert client.get("/users/me", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_catalog_health_cors_and_openapi(api):
    client, database, _ = api
    assert client.get("/health").json() == {"status": "ok"}
    catalog = client.get("/asignaturas")
    assert catalog.status_code == 200
    assert catalog.json()[0]["nombre"] == "Inglés"
    assert catalog.json()[0]["niveles"][0]["temas"][0]["nombre"] == "Present Simple"
    assert "solucion" not in catalog.text and "contenido_prompt" not in catalog.text
    assert set(database.list_collection_names()) == {
        "Usuarios",
        "Asignaturas",
        "Prompts",
        "BancoEjercicios",
        "Tests",
    }
    spec = client.get("/openapi.json").json()
    assert spec["components"]["securitySchemes"]["HTTPBearer"]["scheme"] == "bearer"
    assert "application/json" in spec["paths"]["/auth/login"]["post"]["requestBody"]["content"]
    allowed = client.options(
        "/auth/login",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    denied = client.options(
        "/auth/login",
        headers={"Origin": "https://unexpected.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in denied.headers


def test_health_unavailable_returns_503(api, monkeypatch):
    client, _, _ = api

    async def unavailable(*args, **kwargs):
        raise ConnectionFailure("No mostrar datos de conexión")

    monkeypatch.setattr(type(client.app.state.database), "command", unavailable)
    response = client.get("/health")
    assert response.status_code == 503 and response.json() == {"status": "unavailable"}


def test_seed_repeated_preserves_ids_manual_changes_users_and_tests(api, registration):
    client, database, _ = api
    client.post("/auth/register", json=registration)
    database.Asignaturas.update_one({"codigo": "ingles"}, {"$set": {"nombre": "Inglés revisado"}})
    database.BancoEjercicios.update_one(
        {},
        {
            "$set": {
                "ejemplos_resueltos.0.preguntas.0.enunciado": "I ___ (play) tennis every Sunday."
            }
        },
    )
    before = {name: list(database[name].find()) for name in database.list_collection_names()}
    client.portal.call(seed_database, client.app.state.database)
    client.portal.call(seed_database, client.app.state.database)
    after = {name: list(database[name].find()) for name in database.list_collection_names()}
    assert before == after
    assert len(after["BancoEjercicios"][0]["ejemplos_resueltos"]) == 10
    assert len(after["Prompts"]) == 2 and all(not p["activo"] for p in after["Prompts"])
    assert after["Tests"] == []


def test_seed_rejects_global_id_collision_before_writes(api):
    client, database, _ = api
    existing = database.Asignaturas.find_one({"codigo": "ingles"})
    database.Asignaturas.insert_one(
        {
            "_id": ObjectId(),
            "codigo": "otra",
            "nombre": "Otra",
            "niveles": [
                {
                    "nivel_id": existing["niveles"][0]["nivel_id"],
                    "nombre_curso": "Otro curso",
                    "temas": [],
                }
            ],
        }
    )
    counts = {name: database[name].count_documents({}) for name in database.list_collection_names()}
    with pytest.raises(ValueError, match="únicos"):
        client.portal.call(seed_database, client.app.state.database)
    assert counts == {name: database[name].count_documents({}) for name in counts}
