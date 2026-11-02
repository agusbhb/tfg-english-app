"""Entradas y respuestas públicas; los documentos privados no son respuestas API."""

from typing import Annotated

from pydantic import BeforeValidator, EmailStr, Field

from app.models import MCER, AcademicProfile, Level, Model, MongoId, UTCDate


def normalize_email(value: object) -> object:
    return value.strip().lower() if isinstance(value, str) else value


NormalizedEmail = Annotated[EmailStr, BeforeValidator(normalize_email)]
Password = Annotated[str, Field(min_length=12, max_length=128)]


class RegisterRequest(Model):
    email: NormalizedEmail
    password: Password
    nombre: str | None = Field(default=None, min_length=1, max_length=100)
    asignatura_id: MongoId
    nivel_id: MongoId


class LoginRequest(Model):
    email: NormalizedEmail
    password: Password


class UserResponse(Model):
    id: MongoId
    email: EmailStr
    nombre: str | None
    perfil_academico: AcademicProfile
    nivel_mcer: MCER | None
    creado_en: UTCDate
    actualizado_en: UTCDate


class SubjectResponse(Model):
    id: MongoId
    codigo: str
    nombre: str
    niveles: list[Level]


class TokenResponse(Model):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
