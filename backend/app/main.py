from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from pymongo.errors import DuplicateKeyError, PyMongoError

from app.config import Settings
from app.database import create_client, initialize_database
from app.models import (
    AcademicProfile,
    SubjectDocument,
    UserDocument,
    validate_academic_reference,
    validate_catalog,
)
from app.schemas import (
    LoginRequest,
    RegisterRequest,
    SubjectResponse,
    TokenResponse,
    UserResponse,
)
from app.security import (
    create_access_token,
    decode_access_token,
    dummy_password_hash,
    hash_password,
    verify_password,
)

bearer = HTTPBearer(auto_error=False)


def unauthorized(detail: str = "Token inválido o ausente") -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def public_user(document: dict) -> UserResponse:
    user = UserDocument.model_validate(document)
    return UserResponse.model_validate(user.model_dump(exclude={"password_hash"}))


async def current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> UserResponse:
    if credentials is None:
        raise unauthorized()
    try:
        user_id = decode_access_token(credentials.credentials, request.app.state.settings)
    except (InvalidTokenError, TypeError, ValueError):
        raise unauthorized() from None
    user = await request.app.state.database.Usuarios.find_one({"_id": user_id})
    if user is None:
        raise unauthorized()
    return public_user(user)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = create_client(settings)
        app.state.database = client[settings.mongodb_db]
        try:
            await client.admin.command("ping")
            await initialize_database(app.state.database)
            app.state.dummy_hash = await dummy_password_hash()
            yield
        except PyMongoError:
            # No incluir el error original: puede contener datos de conexión.
            raise RuntimeError(
                "MongoDB no está disponible; revisa la configuración y el servicio"
            ) from None
        finally:
            await client.close()

    app = FastAPI(title="TrAIner", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # FastAPI incluye input por defecto, que puede contener la contraseña.
        errors = [
            {"loc": error["loc"], "msg": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    @app.exception_handler(PyMongoError)
    async def database_error(request: Request, exc: PyMongoError):
        return JSONResponse(status_code=503, content={"detail": "MongoDB no está disponible"})

    @app.get("/health", tags=["Estado"])
    async def health(request: Request):
        try:
            await request.app.state.database.command("ping")
        except PyMongoError:
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        return {"status": "ok"}

    @app.get("/asignaturas", response_model=list[SubjectResponse], tags=["Catálogo"])
    async def subjects(request: Request):
        documents = await request.app.state.database.Asignaturas.find().sort("codigo", 1).to_list()
        catalog = [SubjectDocument.model_validate(document) for document in documents]
        validate_catalog(catalog)
        return [SubjectResponse.model_validate(subject.model_dump()) for subject in catalog]

    @app.post(
        "/auth/register", response_model=UserResponse, status_code=201, tags=["Autenticación"]
    )
    async def register(payload: RegisterRequest, request: Request):
        database = request.app.state.database
        subject = await database.Asignaturas.find_one({"_id": payload.asignatura_id})
        if subject is None:
            raise HTTPException(status_code=422, detail="La selección académica no es válida")
        try:
            validate_academic_reference(SubjectDocument.model_validate(subject), payload.nivel_id)
        except ValueError:
            raise HTTPException(
                status_code=422, detail="La selección académica no es válida"
            ) from None
        now = datetime.now(UTC)
        # BSON Date conserva milisegundos; registro y perfil devuelven la misma fecha.
        now = now.replace(microsecond=(now.microsecond // 1000) * 1000)
        user = UserDocument(
            email=payload.email,
            nombre=payload.nombre,
            password_hash=await hash_password(payload.password),
            perfil_academico=AcademicProfile(
                asignatura_id=payload.asignatura_id, nivel_id=payload.nivel_id
            ),
            creado_en=now,
            actualizado_en=now,
        )
        document = user.model_dump(by_alias=True)
        try:
            await database.Usuarios.insert_one(document)
        except DuplicateKeyError:
            raise HTTPException(status_code=409, detail="El email ya está registrado") from None
        return public_user(document)

    @app.post("/auth/login", response_model=TokenResponse, tags=["Autenticación"])
    async def login(payload: LoginRequest, request: Request):
        user = await request.app.state.database.Usuarios.find_one({"email": payload.email})
        hashed = user["password_hash"] if user else request.app.state.dummy_hash
        valid = await verify_password(payload.password, hashed)
        if user is None or not valid:
            raise unauthorized("Credenciales inválidas")
        return TokenResponse(
            access_token=create_access_token(user["_id"], settings),
            expires_in=settings.access_token_expire_minutes * 60,
        )

    @app.get("/users/me", response_model=UserResponse, tags=["Usuarios"])
    async def me(user: UserResponse = Depends(current_user)):
        return user

    return app
