"""Single source of configuration (PLAN.md §6) plus fixed constants.

All tunables come from environment variables / ``.env``. Secrets are never logged.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REFUSAL_TEXT = "The provided documents do not contain the answer to this question."
NOT_IN_DOCUMENTS = "NOT_IN_DOCUMENTS"
# Embedded in the system prompt so output guardrails can detect a prompt leak.
PROMPT_CANARY = "CANARY-8c41f7d2-e9a0"
BLOCKED_TEXT = "I can't share that."
INJECTION_REFUSAL_TEXT = "I can't follow instructions that try to change my rules or reveal my configuration."

# English-only, pattern based (PLAN.md §10). Case-insensitive regexes.
INJECTION_PATTERNS = [
    r"ignore\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?)",
    r"disregard\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?)",
    r"forget\s+(all\s+|everything\s+|your\s+)?(previous|prior|above|earlier)?\s*(instructions?|rules?)",
    r"(reveal|show|print|repeat|display|output)\s+(me\s+)?(your|the)\s+(system\s+)?(prompt|instructions|rules|configuration)",
    r"what\s+(is|are)\s+your\s+(system\s+)?(prompt|instructions)",
    r"override\s+(your|the)\s+(instructions|rules|guidelines|safety)",
    r"new\s+instructions?\s*:",
    r"(give|tell|show|reveal|share|print|send)\s+(me\s+)?(your|the\s+system'?s?|the\s+server'?s?)\s+(api[\s_-]?key|secret[\s_-]?key|access\s+token|password|credentials)",
    r"what\s+(is|are)\s+your\s+(api[\s_-]?key|secret|password|credentials)",
]
JAILBREAK_PATTERNS = [
    r"\bDAN\b",
    r"do\s+anything\s+now",
    r"developer\s+mode",
    r"jailbreak",
    r"pretend\s+(that\s+)?you\s+(are|have)\s+no\s+(rules|restrictions|limits|guidelines)",
    r"(act|behave|respond)\s+as\s+(if\s+)?(you\s+(are|were)\s+)?(an?\s+)?(unrestricted|uncensored|unfiltered)",
    r"without\s+(any\s+)?(restrictions|filters|limitations|guardrails)",
    r"you\s+are\s+no\s+longer\s+bound",
    r"roleplay\s+as\s+.*(no\s+rules|evil|unrestricted)",
]
API_KEY_REGEXES = [
    r"sk-[A-Za-z0-9_\-]{16,}",
    r"pcsk_[A-Za-z0-9_\-]{16,}",
    r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
]
# B6: instruction-like text hidden inside documents (flagged, still treated as data).
DOCUMENT_INSTRUCTION_PATTERNS = INJECTION_PATTERNS + [
    r"you\s+must\s+(now\s+)?(answer|respond|reply)\s+(only\s+)?(with|in)",
    r"(always|only)\s+(answer|respond|reply)\s+(with\s+)?[\"']?(yes|no)",
    r"as\s+an?\s+ai\s+(model|assistant)\s*,?\s*(you\s+)?(must|should|will)",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # secrets
    openai_api_key: str = ""
    pinecone_api_key: str = ""
    jwt_secret: str = ""

    # auth (B1)
    auth_enabled: bool = False
    default_owner_id: str = "local"
    jwt_expire_minutes: int = 60
    signup_enabled: bool = True
    max_users: int = 50

    # general
    env: str = "dev"
    behind_proxy: bool = False
    demo_fault: str = "none"  # none | llm_timeout | llm_429 | pinecone_down
    request_deadline_s: float = 60.0

    # models
    llm_model: str = "gpt-6-luna"
    judge_model: str = "gpt-6.1-terra"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    llm_reasoning_effort: str = "low"
    llm_max_output_tokens: int = 4000
    judge_reasoning_effort: str = "medium"

    # pinecone
    pinecone_index: str = "rag-service"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"
    pinecone_namespace_prefix: str = "user"
    pinecone_upsert_batch: int = 100
    pinecone_timeout_s: float = 15.0
    index_ready_timeout_s: float = 15.0

    # storage
    sqlite_path: str = "./data/app.db"

    # chunking / retrieval
    chunk_size: int = 1000
    chunk_overlap: int = 200
    top_k: int = 5
    top_k_max: int = 20
    similarity_threshold: float = 0.30

    # ingestion limits
    max_upload_mb: int = 10
    max_pages: int = 200
    max_chunks_per_doc: int = 1500
    min_extracted_chars: int = 50
    max_question_chars: int = 1000
    max_context_chars: int = 12000
    embed_batch_size: int = 100
    allowed_file_types: str = "pdf,docx"  # B5 adds pptx,txt,md
    max_zip_uncompressed_mb: int = 100
    max_zip_ratio: int = 100

    # resilience
    llm_timeout_s: float = 30.0
    embed_timeout_s: float = 30.0
    db_timeout_s: float = 10.0
    max_retries: int = 3
    retry_backoff_s: float = 1.0
    retry_backoff_max_s: float = 20.0
    llm_max_concurrency: int = Field(default=4, ge=1)
    embed_max_concurrency: int = Field(default=4, ge=1)
    judge_max_concurrency: int = Field(default=2, ge=1)
    rate_limit_per_min: int = 20

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
