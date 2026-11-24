"""FastAPI app: routes, middleware and exception handlers ONLY — no business logic (PLAN.md §4, §7)."""
from __future__ import annotations

import asyncio
import logging
import re
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, File, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import auth, metrics, telemetry
from app.config import Settings, get_settings
from app.container import Container, build_container
from app.errors import AppError, NotFound, RateLimited, UpstreamUnavailable
from app.logging_setup import log_event, new_request_id, request_id_var, setup_logging
from app.llm_guard import deadline_var
from app.ratelimit import rate_key
from app.upload_limit import UploadSizeLimit
from app.schemas import (AskRequest, AskResponse, DocumentList, DocumentOut, ErrorResponse, HealthOut, MetricsSummary,
                         RequestItem, RequestList, SignupRequest, SpanOut, TokenOut, TraceOut, UserOut)

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


def _owner_hint(request: Request, settings: Settings) -> str | None:
    if not settings.auth_enabled:
        return settings.default_owner_id
    header = request.headers.get("authorization", "")
    return auth.decode_token(header[7:], settings) if header.lower().startswith("bearer ") else None


_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    setup_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        auth.validate_auth_config(settings)
        app.state.container = container or build_container(settings)
        try:
            await asyncio.wait_for(app.state.container.store.ensure_collection(),
                                   timeout=settings.qdrant_timeout_s)  # never hold startup longer than one timeout
        except Exception as exc:  # the UI/docs should still start; requests will return 503
            log_event(logger, "vector collection not ready at startup", logging.WARNING, error=str(exc)[:200])
        yield
        for owner_obj in (app.state.container.db, app.state.container.store):  # release the SQLite file and Qdrant client/lock
            try:
                closer = getattr(owner_obj, "close", None)
                result = closer() if closer else None
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                log_event(logger, "shutdown cleanup failed", logging.WARNING, error=type(exc).__name__)

    app = FastAPI(
        title="Production RAG Service",
        version="1.0.0",
        description=("Upload documents and ask questions answered **only** from them, with citations. "
                     "Refusals are HTTP 200 with `refused=true` and a `reason`; upstream failures are 503."),
        lifespan=lifespan,
    )
    # An injected container (tests) is usable without running the lifespan.
    app.state.container = container

    # ------------------------------------------------------------------ middleware
    app.add_middleware(UploadSizeLimit, settings=settings)  # inner: runs after request_context
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if _SAFE_REQUEST_ID.fullmatch(supplied) else new_request_id()  # never trust header text
        token_ctx = request_id_var.set(request_id)
        started = time.monotonic()
        key = (request.method, request.url.path)
        factor = 3 if key == ("POST", "/documents") else 1
        dl_ctx = deadline_var.set(started + settings.request_deadline_s * factor)
        status = 500  # what is logged if an exception escapes
        c: Container = request.app.state.container
        route = telemetry.route_label(*key)
        trace = c.telemetry.request(route, request_id, request.method) if (c.telemetry and route) else telemetry.NO_TRACE
        if route:
            request.state.owner_id = _owner_hint(request, settings)
        with trace as tr:  # B3: the whole request is one trace; its steps are spans inside it
            try:
                response = None
                if key in RATE_LIMITED_ROUTES:
                    # one bucket per route group, so a burst of uploads does not use up the questions (and vice versa)
                    allowed, retry_after = c.limiter.check(f"{key[1].split('/')[1]}:{rate_key(request, settings)}")
                    if not allowed:
                        request.state.error_code = "rate_limited"
                        response = _error(request_id, 429, "rate_limited", "Too many requests. Please slow down.",
                                          headers={"Retry-After": str(retry_after)})
                if response is None:
                    try:
                        response = await call_next(request)
                    except Exception:  # built here, while the request id is still set (the outer handler would lose it)
                        logger.exception("unhandled error")  # traceback stays in server logs only
                        request.state.error_code = "internal_error"
                        response = _error(request_id, 500, "internal_error", "Internal server error.")
                status = response.status_code
                response.headers["X-Request-ID"] = request_id
                return response
            finally:  # every request is logged, including 429s and crashes
                latency_ms = int((time.monotonic() - started) * 1000)
                log_event(logger, "request", method=request.method, path=request.url.path, status=status,
                          latency_ms=latency_ms, user_id=getattr(request.state, "owner_id", None))
                tr.set(status=status, owner_id=getattr(request.state, "owner_id", None),
                       error_code=getattr(request.state, "error_code", None))
                deadline_var.reset(dl_ctx)
                request_id_var.reset(token_ctx)

    # ------------------------------------------------------------------ error handlers
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        if exc.status_code >= 500:
            logger.error("application error: %s", exc.code)
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        request.state.error_code = exc.code
        return _error(request_id_var.get(), exc.status_code, exc.code, exc.message, exc.details, headers)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        details = {"errors": [{"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
                              for e in exc.errors()]}
        request.state.error_code = "invalid_request"
        return _error(request_id_var.get(), 422, "invalid_request", "The request is invalid.", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "invalid_request", 401: "unauthorized", 413: "file_too_large"}.get(exc.status_code, "invalid_request")
        request.state.error_code = code
        return _error(request_id_var.get(), exc.status_code, code, str(exc.detail), headers=dict(exc.headers or {}))

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        logger.exception("unhandled error")  # traceback stays in server logs only
        request.state.error_code = "internal_error"
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

    if settings.auth_enabled:
        @app.get("/auth/me", response_model=UserOut, tags=["auth"], responses=ERRORS,
                 summary="Who am I? (checks that the token is still valid)")
        async def me(request: Request, owner_id: str = Depends(owner)):
            c: Container = request.app.state.container
            user = await run_in_threadpool(c.db.get_user, owner_id)
            return UserOut(user_id=user["user_id"], email=user["email"])

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

    # ---- monitoring (B3): your own requests only, like everything else
    @app.get("/metrics/summary", response_model=MetricsSummary, tags=["monitoring"], responses=ERRORS,
             summary="Latency p50/p95, error rate, tokens and cost for your requests",
             description="Computed from the stored traces of your own requests. `window` is the last hour, 24 hours, 7 days or everything kept. "
                         "Cost is an estimate from published prices; embedding tokens are estimated from text length.")
    async def metrics_summary(request: Request, window: Literal["1h", "24h", "7d", "all"] = "24h",
                              owner_id: str = Depends(owner)):
        c: Container = request.app.state.container
        return await run_in_threadpool(metrics.build_summary, c.db, owner_id, window)

    @app.get("/metrics/requests", response_model=RequestList, tags=["monitoring"], responses=ERRORS,
             summary="Your most recent traced requests")
    async def metrics_requests(request: Request, limit: int = 50, owner_id: str = Depends(owner)):
        c: Container = request.app.state.container
        rows = await run_in_threadpool(c.db.trace_requests, owner_id, None, max(1, min(limit, 200)))
        return RequestList(requests=[RequestItem(**metrics.request_item(r)) for r in rows])

    @app.get("/metrics/requests/{request_id}", response_model=TraceOut, tags=["monitoring"], responses=ERRORS,
             summary="The trace of one request: every step with its start offset and duration")
    async def metrics_trace(request_id: str, request: Request, owner_id: str = Depends(owner)):
        c: Container = request.app.state.container
        found = await run_in_threadpool(c.db.get_trace, owner_id, request_id)
        if found is None:
            raise NotFound("No trace with that request id.")
        row, spans = found
        return TraceOut(request=RequestItem(**metrics.request_item(row)),
                        spans=[SpanOut(**{**s, "attrs": json.loads(s["attrs"])}) for s in spans])

    @app.get("/health", response_model=HealthOut, tags=["system"], summary="Liveness and dependency check",
             responses={503: {"model": HealthOut}})
    async def health(request: Request):
        c: Container = request.app.state.container
        sqlite_ok = c.db.ping()
        vector_db_ok = await c.store.healthy()  # cached inside the store so probes don't hammer the vector DB
        ok = sqlite_ok and vector_db_ok
        body = HealthOut(status="ok" if ok else "degraded", sqlite=sqlite_ok, qdrant=vector_db_ok,
                         auth_enabled=c.settings.auth_enabled, file_types=sorted(c.ingestion.allowed_types))
        return JSONResponse(body.model_dump(), status_code=200 if ok else 503)

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()
