import secrets
from datetime import UTC, datetime, timedelta

import jwt
from bson import ObjectId
from jwt import InvalidTokenError
from pwdlib import PasswordHash
from starlette.concurrency import run_in_threadpool

from app.config import Settings

PASSWORD_HASH = PasswordHash.recommended()
JWT_ALGORITHM = "HS256"


async def hash_password(password: str) -> str:
    return await run_in_threadpool(PASSWORD_HASH.hash, password)


async def dummy_password_hash() -> str:
    return await hash_password(secrets.token_urlsafe(32))


async def verify_password(password: str, hashed: str) -> bool:
    return await run_in_threadpool(PASSWORD_HASH.verify, password, hashed)


def create_access_token(user_id: ObjectId, settings: Settings) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
        },
        settings.jwt_secret.get_secret_value(),
        algorithm=JWT_ALGORITHM,
    )


def decode_access_token(token: str, settings: Settings) -> ObjectId:
    claims = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[JWT_ALGORITHM],
        options={"require": ["sub", "iat", "exp"]},
    )
    if (
        not isinstance(claims["sub"], str)
        or not ObjectId.is_valid(claims["sub"])
        or type(claims["iat"]) is not int
        or type(claims["exp"]) is not int
        or claims["exp"] <= claims["iat"]
    ):
        raise InvalidTokenError("Claims inválidos")
    return ObjectId(claims["sub"])
