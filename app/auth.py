"""B1: signup / login / JWT (PLAN.md §3 Auth row, §16).

Argon2id hashes, HS256 JWT (pinned), OAuth2 password form login so Swagger's Authorize works.
With ``AUTH_ENABLED=false`` the dependency returns the fixed default owner and no token is needed.
"""
from __future__ import annotations

import logging
import sqlite3
import time

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.security import OAuth2PasswordBearer

from app.config import Settings
from app.db import Database
from app.errors import EmailTaken, SignupClosed, Unauthorized

logger = logging.getLogger("rag.auth")
ALGORITHM = "HS256"  # pinned: not configurable
_hasher = PasswordHasher()
_DUMMY_HASH = _hasher.hash("dummy-password-for-timing")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def validate_auth_config(settings: Settings) -> None:
    if not settings.auth_enabled:
        return
    secret = settings.jwt_secret
    if len(secret) < 32 or secret.lower() in {"changeme", "change-me", "secret", "your-secret-here"}:
        raise RuntimeError("AUTH_ENABLED=true requires JWT_SECRET to be set to a random value of at least 32 characters.")


def create_token(user_id: str, settings: Settings) -> str:
    now = int(time.time())
    payload = {"sub": user_id, "iat": now, "exp": now + settings.jwt_expire_minutes * 60}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str, settings: Settings) -> str | None:
    """Return the user id for a valid token, else None. Only HS256 is accepted."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None


async def signup(db: Database, settings: Settings, email: str, password: str) -> dict:
    if not settings.signup_enabled:
        raise SignupClosed()
    count = await run_in_threadpool(db.count_users)
    if count >= settings.max_users:
        raise SignupClosed("The maximum number of users has been reached.")
    password_hash = await run_in_threadpool(_hasher.hash, password)
    try:
        return await run_in_threadpool(db.create_user, email, password_hash)
    except sqlite3.IntegrityError as exc:
        raise EmailTaken() from exc


async def login(db: Database, settings: Settings, email: str, password: str) -> str:
    user = await run_in_threadpool(db.get_user_by_email, email)
    stored = user["password_hash"] if user else _DUMMY_HASH  # always verify: no timing leak for unknown emails
    try:
        ok = await run_in_threadpool(_hasher.verify, stored, password)
    except (VerificationError, InvalidHashError):
        ok = False
    if not user or not ok:
        raise Unauthorized("Incorrect email or password.")
    return create_token(user["user_id"], settings)


async def get_owner(request: Request, token: str | None = Depends(oauth2_scheme)) -> str:
    """FastAPI dependency: the owner id for this request."""
    settings: Settings = request.app.state.container.settings
    if not settings.auth_enabled:
        return settings.default_owner_id
    user_id = decode_token(token, settings) if token else None
    if not user_id:
        raise Unauthorized()
    user = await run_in_threadpool(request.app.state.container.db.get_user, user_id)
    if not user:
        raise Unauthorized()
    return user_id
