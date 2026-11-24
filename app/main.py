"""FastAPI app: routes, middleware and exception handlers ONLY — no business logic (PLAN.md §4, §7)."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import auth
from app.config import Settings, get_settings
from app.container import Container, build_container
from app.errors import AppError, RateLimited, UpstreamUnavailable
from app.logging_setup import log_event, new_request_id, request_id_var, setup_logging
from app.llm_guard import deadline_var
from app.upload_limit import UploadSizeLimit
from app.schemas import (AskRequest, AskResponse, DocumentList, DocumentOut, ErrorResponse, HealthOut,
                         SignupRequest, TokenOut, UserOut)

logger = logging.getLogger("rag.api")
STATIC_DIR = Path(__file__).parent / "static"
RATE_LIMITED_ROUTES = {("POST", "/ask"), ("POST", "/documents"), ("POST", "/auth/signup"), ("POST", "/auth/login")}

ERRORS = {c: {"model": ErrorResponse} for c in (400, 401, 404, 409, 413, 415, 422, 429, 503)}


def _error(request_id: str, status: int, code: str, message: str, details: dict | None = None,
           headers: dict | None = None) -> JSONResponse:
    body: dict = {"error": {"code": code, "message": message, "request_id": request_id}}
    if details:
        body["error"]["details"] = details
    resp = JSONResponse(body, status_code=status, headers=headers)
    resp.headers["X-Request-ID"] = request_id
    return resp


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        auth.validate_auth_config(settings)
        app.state.container = container or build_container(settings)
        app.state.vector_db_checked = (0.0, False)
        try:
            await asyncio.wait_for(app.state.container.store.ensure_collection(),
                                   timeout=settings.qdrant_timeout_s)  # never hold startup longer than one timeout
        except Exception as exc:  # the UI/docs should still start; requests will return 503
            log_event(logger, "vector collection not ready at startup", logging.WARNING, error=str(exc)[:200])
        yield

    app = FastAPI(
        title="Production RAG Service",
        version="1.0.0",
        description=("Upload documents and ask questions answered **only** from them, with citations. "
                     "Refusals are HTTP 200 with `refused=true` and a `reason`; upstream failures are 503."),
        lifespan=lifespan,
    )
    # An injected container (tests) is usable without running the lifespan.
    app.state.container = container
    app.state.vector_db_checked = (0.0, False)

    # ------------------------------------------------------------------ middleware
    app.add_middleware(UploadSizeLimit, max_bytes=settings.max_upload_bytes, max_mb=settings.max_upload_mb)  # inner: runs after request_context
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or new_request_id()
        token_ctx = request_id_var.set(request_id)
        started = time.monotonic()
        key = (request.method, request.url.path)
        factor = 3 if key == ("POST", "/documents") else 1
        dl_ctx = deadline_var.set(started + settings.request_deadline_s * factor)
        status = 500  # what is logged if an exception escapes
        try:
            c: Container = request.app.state.container
            response = None
            if key in RATE_LIMITED_ROUTES:
                allowed, retry_after = c.limiter.check(_rate_key(request, settings))
                if not allowed:
                    response = _error(request_id, 429, "rate_limited", "Too many requests. Please slow down.",
                                      headers={"Retry-After": str(retry_after)})
            if response is None:
                response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:  # every request is logged, including 429s and crashes
            log_event(logger, "request", method=request.method, path=request.url.path, status=status,
                      latency_ms=int((time.monotonic() - started) * 1000),
                      user_id=getattr(request.state, "owner_id", None))
            deadline_var.reset(dl_ctx)
            request_id_var.reset(token_ctx)

    # ------------------------------------------------------------------ error handlers
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        if exc.status_code >= 500:
            logger.error("application error: %s", exc.code)
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return _error(request_id_var.get(), exc.status_code, exc.code, exc.message, exc.details, headers)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        details = {"errors": [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
                              for e in exc.errors()]}
        return _error(request_id_var.get(), 422, "invalid_request", "The request is invalid.", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "invalid_request", 401: "unauthorized", 413: "file_too_large"}.get(exc.status_code, "invalid_request")
        return _error(request_id_var.get(), exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        logger.exception("unhandled error")  # traceback stays in server logs only
        return _error(request_id_var.get(), 500, "internal_error", "Internal server error.")

    # ------------------------------------------------------------------ owner dependency
    async def owner(request: Request, owner_id: str = Depends(auth.get_owner)) -> str:
        request.state.owner_id = owner_id
        return owner_id

    # ------------------------------------------------------------------ routes
    if settings.auth_enabled:
        @app.post("/auth/signup", response_model=UserOut, status_code=201, tags=["auth"], responses=ERRORS,
                  summary="Create an account")
        async def signup(body: SignupRequest, request: Request):
            c: Container = request.app.state.container
            user = await auth.signup(c.db, c.settings, body.email, body.password)
            return UserOut(**user)

        @app.post("/auth/login", response_model=TokenOut, tags=["auth"], responses=ERRORS,
                  summary="Log in (OAuth2 password form: username = email)")
        async def login(request: Request, form: OAuth2PasswordRequestForm = Depends()):
            c: Container = request.app.state.container
            token = await auth.login(c.db, c.settings, form.username, form.password)
            return TokenOut(access_token=token)

    @app.post("/documents", response_model=DocumentOut, status_code=201, tags=["documents"], responses=ERRORS,
              summary="Upload and ingest a document",
              description="Multipart upload. Validates type/size, extracts text, chunks, embeds and stores it.")
    async def upload_document(request: Request, file: UploadFile = File(...), owner_id: str = Depends(owner)):
        c: Container = request.app.state.container
        limit = c.settings.max_upload_bytes
        data = await file.read(limit + 1)  # the body is already capped by UploadSizeLimit while it streams in
        row = await c.ingestion.upload(owner_id, file.filename or "", data, file.content_type)
        return DocumentOut(**row)

    @app.get("/documents", response_model=DocumentList, tags=["documents"], responses=ERRORS,
             summary="List your documents")
    async def list_documents(request: Request, owner_id: str = Depends(owner)):
        c: Container = request.app.state.container
        return DocumentList(documents=[DocumentOut(**r) for r in await c.ingestion.list(owner_id)])

    @app.delete("/documents/{doc_id}", status_code=204, tags=["documents"], responses=ERRORS,
                summary="Delete a document and its vectors")
    async def delete_document(doc_id: str, request: Request, owner_id: str = Depends(owner)):
        c: Container = request.app.state.container
        await c.ingestion.delete(owner_id, doc_id)
        return Response(status_code=204)

    @app.post("/ask", response_model=AskResponse, tags=["ask"], responses=ERRORS,
              summary="Ask a question about your documents",
              description="Returns a cited answer, or `refused=true` with a `reason` (HTTP 200).")
    async def ask(body: AskRequest, request: Request, owner_id: str = Depends(owner)):
        c: Container = request.app.state.container
        return await c.ask.ask(owner_id, body)

    @app.get("/health", response_model=HealthOut, tags=["system"], summary="Liveness and dependency check",
             responses={503: {"model": HealthOut}})
    async def health(request: Request):
        c: Container = request.app.state.container
        sqlite_ok = c.db.ping()
        checked_at, vector_db_ok = request.app.state.vector_db_checked
        if time.monotonic() - checked_at > 60:  # cached so probes don't hit the vector DB
            vector_db_ok = await c.store.ping()
            request.app.state.vector_db_checked = (time.monotonic(), vector_db_ok)
        ok = sqlite_ok and vector_db_ok
        body = HealthOut(status="ok" if ok else "degraded", sqlite=sqlite_ok, qdrant=vector_db_ok,
                         auth_enabled=c.settings.auth_enabled)
        return JSONResponse(body.model_dump(), status_code=200 if ok else 503)

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def _rate_key(request: Request, settings: Settings) -> str:
    """User id when a valid token is present (no DB hit), otherwise the client IP (always IP for /auth/*)."""
    if settings.auth_enabled and not request.url.path.startswith("/auth/"):
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            user_id = auth.decode_token(header[7:], settings)
            if user_id:
                return f"user:{user_id}"
    ip = request.client.host if request.client else "unknown"
    if settings.behind_proxy:
        # Proxies append the real peer on the right; the left side is whatever the client sent (spoofable).
        parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
        if len(parts) >= settings.trusted_proxies:
            ip = parts[-settings.trusted_proxies]
    return f"ip:{ip}"


app = create_app()
