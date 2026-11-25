"""Single source of configuration (PLAN.md §6) plus fixed constants.

All tunables come from environment variables / ``.env``. Secrets are never logged.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REFUSAL_TEXT = "The provided documents do not contain the answer to this question."
NOT_IN_DOCUMENTS = "NOT_IN_DOCUMENTS"
# Embedded in the system prompt so output guardrails can detect a prompt leak.
PROMPT_CANARY = "CANARY-8c41f7d2-e9a0"
BLOCKED_TEXT = "I can't share that."
INJECTION_REFUSAL_TEXT = "I can't follow instructions that try to change my rules or reveal my configuration."

# English-only, pattern based (PLAN.md §10). Case-insensitive regexes.
# HARD patterns are always refused. SOFT patterns also match ordinary document questions ("show your rules for
# refunds", "how do I jailbreak ...", "developer mode"), so only those may be released by the model recheck.
HARD_INJECTION_PATTERNS = [
    r"(system_prompt|developer_instructions|security_rules|api_configuration|retrieved_context)\s*:",  # a dump template
    r"(output|reveal|print|show|display|include)\s+(me\s+)?your\s+(internal|hidden|exact|original|full)\s+(context|instructions|prompt|configuration|rules)",
    r"instructions\s+that\s+(controlled|governed|guided)\s+your\s+behaviou?r",
    r"ignore\s+(all\s+|any\s+|the\s+|your\s+|of\s+)*(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?)",
    r"disregard\s+(all\s+|any\s+|the\s+|your\s+|of\s+)*(previous|prior|above|earlier)\s+(instructions?|prompts?|rules?)",
    r"forget\s+(all\s+|everything\s+|your\s+)(previous\s+|prior\s+|above\s+|earlier\s+)?(instructions?|rules?)",
    r"forget\s+(previous|prior|above|earlier)\s+(instructions?|rules?)",
    r"ignore\s+(everything|anything)\s+(above|before|you\s+were\s+told)",
    r"override\s+(your|the)\s+(instructions|rules|guidelines|safety)",
    r"new\s+instructions?\s*:",
    r"(give|tell|show|reveal|share|print|send)\s+(me\s+)?(your|the\s+system'?s?|the\s+server'?s?)\s+(api[\s_-]?key|secret[\s_-]?key|access\s+token|password|credentials)",
]
SOFT_INJECTION_PATTERNS = [
    r"for\s+this\s+(diagnostic|debug(ging)?|audit)\s+session",
    r"authori[sz]ed\s+(internal\s+)?(audit|diagnostic|debug(ging)?)",
    r"what\s+(is|are)\s+your\s+(api[\s_-]?key|secret|password|credentials)",  # also "what is your password policy?"
    r"(reveal|show|print|repeat|display|output)\s+(me\s+)?(your\s+(system\s+)?|the\s+system\s+)(prompt|instructions|rules|configuration)",
    r"what\s+(is|are)\s+your\s+(system\s+)?(prompt|instructions)",
]
HARD_JAILBREAK_PATTERNS = [
    r"your\s+(previous|prior|former|old)\s+(identity|persona|self)\b",
    r"you\s+are\s+now\s+(an?\s+)?(ai|assistant|bot|model|chatbot)\b[^.?!]{0,60}\b(no|without|never)\b",
    r"do\s+anything\s+now",
    r"pretend\s+(that\s+)?you\s+(are|have)\s+no\s+(rules|restrictions|limits|guidelines)",
    r"(act|behave|respond)\s+as\s+(if\s+)?(you\s+(are|were)\s+)?(an?\s+)?(unrestricted|uncensored|unfiltered)",
    r"you\s+are\s+no\s+longer\s+bound",
    r"roleplay\s+as\s+[^.]{0,80}(no\s+rules|evil|unrestricted)",  # bounded: an open-ended .* was quadratic
]
SOFT_JAILBREAK_PATTERNS = [
    r"role-?\s?play(ing)?\s+(game|as)\b",
    r"\bstay\s+in\s+character\b",
    r"(has|have|with)\s+no\s+(rules|limitations|restrictions)",
    r"\bDAN\b",
    r"(^|[.!?]\s+)(please\s+)?(enable|enter|activate|switch\s+to|turn\s+on)\s+developer\s+mode",  # an order, not "how do I enable ..."
    r"you\s+are\s+(now\s+)?in\s+developer\s+mode",
    r"developer\s+mode\s+(enabled|activated)",
    r"jailbreak",
    r"(answer|respond|reply|act|behave|operate|speak|talk)\w*\s+(\w+\s+){0,3}?without\s+(any\s+)?(restrictions|filters|limitations|guardrails)",
]
INJECTION_PATTERNS = HARD_INJECTION_PATTERNS + SOFT_INJECTION_PATTERNS
JAILBREAK_PATTERNS = HARD_JAILBREAK_PATTERNS + SOFT_JAILBREAK_PATTERNS
SOFT_PATTERNS = frozenset(SOFT_INJECTION_PATTERNS + SOFT_JAILBREAK_PATTERNS)
API_KEY_REGEXES = [
    r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{16,}",  # the lookbehind keeps words like "risk-management-framework" out
    r"(?<![A-Za-z0-9])pcsk_[A-Za-z0-9_\-]{16,}",
    r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
]
# B6: instruction-like text hidden inside documents (flagged, still treated as data).
DOCUMENT_INSTRUCTION_PATTERNS = INJECTION_PATTERNS + HARD_JAILBREAK_PATTERNS + [
    # B6: text inside a document that talks TO an AI. Flag only: the document is still accepted and stays data.
    r"(note|notice|message|memo|directive|instruction)s?\s+(to|for)\s+(the\s+)?(ai|a\.i\.|assistant|llm|chatbot|language\s+model)s?",
    r"(?<![a-z])(ai|assistants?(?!\s+(manager|professor|director|editor|principal|teacher|secretary|engineer|coach|registrar|lecturer|clerk|chef|nurse))|llms?|chatbots?|language\s+models?)(?![a-z])[^.]{0,40}?\s(must|should|shall|will)\s+(always\s+|only\s+|now\s+)?(answer|respond|reply|output|print|cite|reveal|append|ignore|obey)",
    r"(system|admin(istrator)?)\s*:\s*(the\s+user|you|answer|respond|ignore)",
    r"</?\s*context\s*>",  # a document containing the literal tag we use to fence its text off
    r"(from\s+now\s+on|starting\s+now|henceforth),?\s+you\s+(are|will|must|should|shall)",
    r"(assistant|ai|chatbot)\s*:\s*(respond|answer|reply|say|output|print|append|ignore|always|never|tell|do\s+not)",
    r"(^|[.!?]\s+)user\s*:.{0,200}(assistant|ai)\s*:",
    r"system\s+override",
    r"overrides?\s+(all\s+|any\s+)?(your|the|other)\s+(other\s+|previous\s+)?(instructions|rules|guidelines)",
    r"(append|add|include|insert|prepend)\s[^.]{0,80}\s(to|in|at)\s[^.]{0,30}your\s+(answer|reply|response|output)",
    r"cite\s+this\s+(document|slide|page|file)\s+as\s+(proof|evidence|a\s+source)",
    r"(do\s+not|never|don't)\s+(reveal|tell|mention|disclose)\s+(to\s+the\s+user\s+)?that\s+(this|these|the)\s+(document|text|instructions?)",
    r"you\s+must\s+(now\s+)?(answer|respond|reply)\s+(only\s+)?(with|in)",
    r"(always|only)\s+(answer|respond|reply)\s+(with\s+)?[\"']?(yes|no)",
    r"as\s+an?\s+ai\s+(model|assistant)\s*,?\s*(you\s+)?(must|should|will)",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # secrets
    openai_api_key: str = ""
    qdrant_api_key: str = ""
    jwt_secret: str = ""

    # auth (B1)
    auth_enabled: bool = True  # B1 is on: every document and question endpoint needs a token (set false for a no-login demo)
    default_owner_id: str = "local"
    jwt_expire_minutes: int = 60
    signup_enabled: bool = True
    max_users: int = 50

    # general
    env: str = "dev"
    behind_proxy: bool = False
    screen_reasoning_effort: str = "none"  # a yes/no classifier needs no reasoning: ~2 s instead of 10-14 s (measured 24 Nov 2026)
    input_screen: bool = True  # a model screens every question the patterns passed, in parallel with the embedding (can only block)
    input_recheck: bool = True  # a model double-checks questions the regex guardrail blocked (releases false positives)
    trusted_proxies: int = Field(default=1, ge=1)  # proxies in front of us; the client IP is that many entries from the right of X-Forwarded-For
    demo_fault: str = "none"  # none | llm_timeout | llm_429 | embed_timeout | embed_429 | vector_db_down (demo only)
    request_deadline_s: float = 60.0

    # monitoring (B3). Prices are USD per 1M tokens from the OpenAI model pages (checked 25 Nov 2026): used only for cost ESTIMATES.
    telemetry_enabled: bool = True
    trace_keep: int = Field(default=5000, ge=100)  # newest traced requests kept in SQLite
    llm_price_in_per_m: float = 0.10   # gpt-6-luna input
    llm_price_out_per_m: float = 0.50  # gpt-6-luna output
    embed_price_per_m: float = 0.02    # text-embedding-3-small

    # models
    llm_model: str = "gpt-6-luna"
    judge_model: str = "gpt-6.1-terra"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    llm_reasoning_effort: str = "low"
    llm_max_output_tokens: int = 4000
    judge_reasoning_effort: str = "medium"

    # qdrant (PLAN.md §6): QDRANT_PATH = embedded mode (tests/notebooks), otherwise QDRANT_URL (+ key for the cloud)
    qdrant_url: str = ""
    qdrant_path: str = ""
    qdrant_collection: str = "rag_chunks"
    qdrant_timeout_s: float = 15.0
    qdrant_upsert_batch: int = 100

    # storage
    sqlite_path: str = "./data/app.db"

    # chunking / retrieval
    chunk_size: int = 1000
    chunk_overlap: int = 200
    top_k: int = 5
    top_k_max: int = 20
    similarity_threshold: float = 0.15  # provisional (24 Nov 2026, see PLAN.md §12); the eval tunes it per config on dev

    # ingestion limits
    max_upload_mb: int = 10
    max_pages: int = 200
    max_chunks_per_doc: int = 1500
    min_extracted_chars: int = 50
    max_question_chars: int = 1000
    max_context_chars: int = 12000
    embed_batch_size: int = 100
    allowed_file_types: str = "pdf,docx,pptx,txt,md"  # B5 types: pptx (page = slide), txt, md (page = heading section)
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

    @model_validator(mode="after")
    def _check_consistency(self):
        allowed_faults = {"none", "llm_timeout", "llm_429", "embed_timeout", "embed_429", "vector_db_down"}
        if self.demo_fault not in allowed_faults:
            raise ValueError(f"DEMO_FAULT must be one of {sorted(allowed_faults)}")
        if self.env == "production" and self.demo_fault != "none":
            raise ValueError("DEMO_FAULT must be 'none' when ENV=production")
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        if self.top_k > self.top_k_max:
            raise ValueError("TOP_K must not exceed TOP_K_MAX")
        return self

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
