"""B1: signup/login/JWT and per-user isolation (AUTH_ENABLED=true)."""
from __future__ import annotations

import time

import jwt
import pytest

from tests.conftest import Env
from tests.make_files import make_pdf

PDF = make_pdf(["The refund policy allows returns within thirty days of purchase with a receipt."])


@pytest.fixture
def aenv(tmp_path):
    return Env(tmp_path, auth_enabled=True)


def signup(c, email="a@example.com", pw="correct-horse-1"):
    return c.post("/auth/signup", json={"email": email, "password": pw})


def login(c, email="a@example.com", pw="correct-horse-1"):
    return c.post("/auth/login", data={"username": email, "password": pw})


def hdr(token):
    return {"Authorization": f"Bearer {token}"}


def test_signup_login_and_protected_routes(aenv):
    c = aenv.client
    assert c.get("/health").json()["auth_enabled"] is True
    assert c.get("/documents").status_code == 401
    assert c.post("/ask", json={"question": "hi there"}).status_code == 401
    r = signup(c)
    assert r.status_code == 201 and r.json()["email"] == "a@example.com"
    assert signup(c).status_code == 409
    assert login(c, pw="wrong-password").status_code == 401
    assert login(c, email="nobody@example.com").status_code == 401
    tok = login(c).json()
    assert tok["token_type"] == "bearer"
    assert c.get("/documents", headers=hdr(tok["access_token"])).status_code == 200
    assert c.get("/documents", headers=hdr("garbage")).status_code == 401


def test_password_validation_and_hashing(aenv):
    c = aenv.client
    assert c.post("/auth/signup", json={"email": "b@example.com", "password": "short"}).status_code == 422
    assert c.post("/auth/signup", json={"email": "b@example.com", "password": "x" * 129}).status_code == 422
    assert c.post("/auth/signup", json={"email": "not-an-email", "password": "long-enough-1"}).status_code == 422
    signup(c)
    stored = aenv.container.db.get_user_by_email("a@example.com")["password_hash"]
    assert stored.startswith("$argon2id$") and "correct-horse-1" not in stored


def test_users_cannot_see_each_others_documents(aenv):
    c = aenv.client
    signup(c, "a@example.com"); signup(c, "b@example.com")
    ta = hdr(login(c, "a@example.com").json()["access_token"])
    tb = hdr(login(c, "b@example.com").json()["access_token"])
    files = {"file": ("policy.pdf", PDF, "application/pdf")}
    doc = c.post("/documents", files=files, headers=ta).json()
    assert c.get("/documents", headers=tb).json()["documents"] == []
    assert c.delete(f"/documents/{doc['doc_id']}", headers=tb).status_code == 404
    assert c.post("/ask", json={"question": "refund policy returns"}, headers=tb).json()["reason"] == "no_documents"
    assert c.post("/ask", json={"question": "refund", "doc_ids": [doc["doc_id"]]}, headers=tb).status_code == 404
    assert c.post("/ask", json={"question": "refund policy returns"}, headers=ta).json()["refused"] is False
    # the same file may be uploaded by the second user
    assert c.post("/documents", files=files, headers=tb).status_code == 201


def test_bad_tokens_are_rejected(aenv):
    c = aenv.client
    signup(c)
    uid = aenv.container.db.get_user_by_email("a@example.com")["user_id"]
    secret = aenv.settings.jwt_secret
    now = int(time.time())
    expired = jwt.encode({"sub": uid, "iat": now - 7200, "exp": now - 3600}, secret, algorithm="HS256")
    wrong_secret = jwt.encode({"sub": uid, "exp": now + 3600}, "y" * 40, algorithm="HS256")
    none_alg = jwt.encode({"sub": uid, "exp": now + 3600}, key=None, algorithm="none")
    no_sub = jwt.encode({"exp": now + 3600}, secret, algorithm="HS256")
    unknown_user = jwt.encode({"sub": "ghost", "exp": now + 3600}, secret, algorithm="HS256")
    for bad in (expired, wrong_secret, none_alg, no_sub, unknown_user):
        assert c.get("/documents", headers=hdr(bad)).status_code == 401
    good = jwt.encode({"sub": uid, "exp": now + 3600}, secret, algorithm="HS256")
    assert c.get("/documents", headers=hdr(good)).status_code == 200


def test_login_uses_oauth2_form_for_swagger(aenv):
    schema = aenv.client.get("/openapi.json").json()
    assert "OAuth2PasswordBearer" in schema["components"]["securitySchemes"]
    assert "application/x-www-form-urlencoded" in schema["paths"]["/auth/login"]["post"]["requestBody"]["content"]


def test_signup_can_be_closed_or_capped(tmp_path):
    closed = Env(tmp_path, auth_enabled=True, signup_enabled=False)
    assert signup(closed.client).status_code == 403
    (tmp_path / "cap").mkdir()
    capped = Env(tmp_path / "cap", auth_enabled=True, max_users=1)
    assert signup(capped.client, "one@example.com").status_code == 201
    assert signup(capped.client, "two@example.com").status_code == 403


def test_startup_refuses_weak_jwt_secret(tmp_path):
    from app.auth import validate_auth_config
    with pytest.raises(RuntimeError):
        validate_auth_config(Env(tmp_path, auth_enabled=True, jwt_secret="short").settings)
    (tmp_path / "ok").mkdir()
    validate_auth_config(Env(tmp_path / "ok", auth_enabled=True).settings)  # 40-char secret passes


def test_rate_limit_is_keyed_per_user(tmp_path):
    e = Env(tmp_path, auth_enabled=True, rate_limit_per_min=2)
    c = e.client
    signup(c, "a@example.com", "correct-horse-1"); login(c, "a@example.com", "correct-horse-1")
    signup(c, "b@example.com", "correct-horse-1")
    # /auth/* is keyed by IP: 5 requests above already exceed the limit of 2 for this IP
    assert signup(c, "c@example.com").status_code == 429
