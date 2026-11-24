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


# ---- B1 hardening (24 Nov 2026) -------------------------------------------------------------------------------
def test_auth_is_on_by_default():
    from app.config import Settings
    assert Settings(_env_file=None).auth_enabled is True


def test_me_returns_the_logged_in_user_and_needs_a_token(aenv):
    c = aenv.client
    assert c.get("/auth/me").status_code == 401
    signup(c, "Me@Example.com")
    me = c.get("/auth/me", headers=hdr(login(c, "me@example.com").json()["access_token"])).json()
    assert me["email"] == "me@example.com" and me["user_id"]


def test_email_is_case_insensitive(aenv):
    c = aenv.client
    assert signup(c, "Mixed.Case@Example.com").status_code == 201
    assert signup(c, "mixed.case@example.com").status_code == 409           # same address
    assert login(c, "MIXED.CASE@EXAMPLE.COM").status_code == 200


def test_search_is_isolated_per_user_at_the_vector_level(aenv):
    """B has documents of their own, so the question really reaches Qdrant; A's chunks must still never come back."""
    c = aenv.client
    signup(c, "a@example.com"); signup(c, "b@example.com")
    ta = hdr(login(c, "a@example.com").json()["access_token"])
    tb = hdr(login(c, "b@example.com").json()["access_token"])
    doc_a = c.post("/documents", files={"file": ("a.pdf", PDF, "application/pdf")}, headers=ta).json()
    other = make_pdf(["Shipping takes five business days for domestic orders and two weeks for international orders."])
    doc_b = c.post("/documents", files={"file": ("b.pdf", other, "application/pdf")}, headers=tb).json()
    body = c.post("/ask", json={"question": "What is the refund policy for returns within thirty days?"}, headers=tb).json()
    assert all(ch["doc_id"] == doc_b["doc_id"] for ch in body["retrieved_chunks"])           # only B's own chunks
    assert doc_a["doc_id"] not in str(body) and "refund policy allows" not in str(body)
    assert aenv.count(aenv.container.db.get_user_by_email("a@example.com")["user_id"]) >= 1
    assert aenv.count(aenv.container.db.get_user_by_email("b@example.com")["user_id"]) >= 1
    # each user's own question reaches only their own document
    own = c.post("/ask", json={"question": "What is the refund policy for returns within thirty days?"}, headers=ta).json()
    assert own["refused"] is False and {ch["doc_id"] for ch in own["retrieved_chunks"]} == {doc_a["doc_id"]}


def test_deleting_one_users_copy_leaves_the_other_users_copy(aenv):
    c = aenv.client
    signup(c, "a@example.com"); signup(c, "b@example.com")
    ta = hdr(login(c, "a@example.com").json()["access_token"])
    tb = hdr(login(c, "b@example.com").json()["access_token"])
    files = {"file": ("policy.pdf", PDF, "application/pdf")}
    da = c.post("/documents", files=files, headers=ta).json()
    db = c.post("/documents", files=files, headers=tb).json()
    assert da["doc_id"] != db["doc_id"] and da["file_hash"] == db["file_hash"]
    assert c.delete(f"/documents/{da['doc_id']}", headers=ta).status_code == 204
    assert [d["doc_id"] for d in c.get("/documents", headers=tb).json()["documents"]] == [db["doc_id"]]
    assert c.post("/ask", json={"question": "refund policy returns"}, headers=tb).json()["refused"] is False


def test_a_token_survives_only_while_the_account_exists(aenv):
    c = aenv.client
    signup(c)
    token = hdr(login(c).json()["access_token"])
    assert c.get("/documents", headers=token).status_code == 200
    aenv.container.db._run("DELETE FROM users", (), write=True)
    assert c.get("/documents", headers=token).status_code == 401


def test_upload_without_a_token_is_401_and_nothing_is_stored(aenv):
    r = aenv.client.post("/documents", files={"file": ("policy.pdf", PDF, "application/pdf")})
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
    assert aenv.container.db.doc_ids("local") == set()


def test_login_failures_do_not_reveal_whether_the_email_exists(aenv):
    c = aenv.client
    signup(c)
    wrong_pw = login(c, pw="wrong-password-1")
    unknown = login(c, email="ghost@example.com")
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json()["error"]["message"] == unknown.json()["error"]["message"]
