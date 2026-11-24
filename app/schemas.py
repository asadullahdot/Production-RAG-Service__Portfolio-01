"""All Pydantic models (PLAN.md §7.2). Field names are canonical."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Reason = Literal[
    "no_documents", "no_relevant_context", "not_in_documents",
    "prompt_injection", "jailbreak", "output_blocked",
]


class UserOut(BaseModel):
    user_id: str
    email: str


class SignupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", examples=["student@example.com"])
    password: str = Field(min_length=8, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class DocumentOut(BaseModel):
    doc_id: str
    file_name: str
    file_type: str
    num_chunks: int
    num_pages: int
    uploaded_at: str
    file_hash: str

    model_config = {"json_schema_extra": {"examples": [{
        "doc_id": "0b8f6d0e-6f0a-4f6c-9a53-1f2f6d7a9c11", "file_name": "policy.pdf", "file_type": "pdf",
        "num_chunks": 42, "num_pages": 9, "uploaded_at": "2026-11-24T10:15:00+00:00",
        "file_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}]}}


class DocumentList(BaseModel):
    documents: list[DocumentOut]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=10_000, examples=["What is the refund policy?"])  # exact cap: MAX_QUESTION_CHARS
    top_k: int | None = Field(default=None, ge=1, le=100)  # clamped to TOP_K_MAX in retrieval
    doc_ids: list[str] | None = Field(default=None, max_length=100)


class RetrievedChunk(BaseModel):
    n: int
    chunk_id: str
    doc_id: str
    file_name: str
    page: int
    chunk_idx: int
    text: str
    score: float


class Citation(BaseModel):
    n: int
    doc_id: str
    file_name: str
    page: int


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class AskResponse(BaseModel):
    answer: str
    citations: list[Citation] = []
    retrieved_chunks: list[RetrievedChunk] = []
    num_retrieved: int = 0
    refused: bool = False
    reason: Reason | None = None
    request_id: str = ""
    latency_ms: int = 0
    usage: Usage = Usage()


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str = ""
    details: dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class HealthOut(BaseModel):
    status: str
    sqlite: bool
    qdrant: bool
    auth_enabled: bool
