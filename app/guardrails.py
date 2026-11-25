"""Upload, input, output and document guardrails (PLAN.md §10).

Everything here is deterministic (no LLM). Input checks are English pattern/heuristic only.
"""
from __future__ import annotations

import io
import re
import struct
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass

from app.config import (API_KEY_REGEXES, DOCUMENT_INSTRUCTION_PATTERNS, INJECTION_PATTERNS,
                        JAILBREAK_PATTERNS, PROMPT_CANARY, SOFT_PATTERNS, Settings)
from app.errors import CorruptFile, EmptyFile, EncryptedFile, FileTooLarge, InvalidRequest, MaliciousFile, UnsupportedType

# ----------------------------------------------------------------------------- upload
EXT_TO_TYPE = {".pdf": "pdf", ".docx": "docx", ".pptx": "pptx", ".txt": "txt", ".md": "md", ".markdown": "md"}
MIME_OK = {
    "pdf": {"application/pdf"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
    "pptx": {"application/vnd.openxmlformats-officedocument.presentationml.presentation"},
    "txt": {"text/plain"},
    "md": {"text/markdown", "text/x-markdown", "text/plain"},
}
_GENERIC_MIME = {"application/octet-stream", "", None}
_ZIP_REQUIRED = {"docx": "word/document.xml", "pptx": "ppt/presentation.xml"}


def validate_upload(filename: str, content_type: str | None, data: bytes, settings: Settings,
                    allowed_types: set[str]) -> str:
    """Return the detected file type or raise (415 / 413 / 400)."""
    if len(data) > settings.max_upload_bytes:
        raise FileTooLarge(f"File exceeds the {settings.max_upload_mb} MB limit.")
    if not data:
        raise EmptyFile()
    name = (filename or "").lower()
    ext = next((e for e in EXT_TO_TYPE if name.endswith(e)), None)
    file_type = EXT_TO_TYPE.get(ext) if ext else None
    if file_type is None or file_type not in allowed_types:
        raise UnsupportedType(f"Unsupported file type. Allowed: {', '.join(sorted(allowed_types))}.")
    mime = (content_type or "").split(";")[0].strip().lower() or None
    if mime not in _GENERIC_MIME and mime not in MIME_OK[file_type]:
        raise UnsupportedType(f"Content type '{mime}' does not match a .{file_type} file.")
    _check_magic(file_type, data)
    if file_type in _ZIP_REQUIRED:
        _check_zip(file_type, data, settings)
    return file_type


# Names a PDF uses for active content, checked on the raw bytes (objects compressed into streams are not inspected, and
# neither are names written with #-escapes). Only long names are used: a short one such as "/JS" appears by chance inside
# compressed image data, which would reject harmless PDFs. A JavaScript action always carries "/S /JavaScript".
_PDF_ACTIVE = re.compile(rb"/(JavaScript|Launch)(?![A-Za-z])")


def _check_magic(file_type: str, data: bytes) -> None:
    if file_type == "pdf":
        if not data.lstrip()[:5].startswith(b"%PDF-"):
            raise UnsupportedType("File content is not a PDF.")
        if _PDF_ACTIVE.search(data):  # B6: JavaScript or a launch action is active content, not a document
            raise MaliciousFile("PDF files with embedded JavaScript or launch actions are not accepted.")
    elif file_type in _ZIP_REQUIRED:
        if not data.startswith(b"PK"):
            raise UnsupportedType(f"File content is not a valid .{file_type} file.")
    else:  # txt / md
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise UnsupportedType("Text files must be UTF-8 encoded.") from exc
        if b"\x00" in data:
            raise UnsupportedType("File content is binary, not text.")


def _check_zip(file_type: str, data: bytes, settings: Settings) -> None:
    """Corrupt-archive and zip-bomb checks for DOCX/PPTX (core), plus macro rejection (B6)."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, ValueError, OSError, struct.error) as exc:
        raise CorruptFile() from exc
    names = {i.filename for i in infos}
    if "[Content_Types].xml" not in names or _ZIP_REQUIRED[file_type] not in names:
        raise CorruptFile("The file is missing required document parts.")
    total = sum(i.file_size for i in infos)
    compressed = max(1, sum(i.compress_size for i in infos))
    if (len(infos) > 5000 or total > settings.max_zip_uncompressed_mb * 1024 * 1024
            or total / compressed > settings.max_zip_ratio):
        raise MaliciousFile("The archive is too large when decompressed.")
    if any(n.lower().endswith("vbaproject.bin") for n in names):
        raise MaliciousFile("Files containing macros are not accepted.")
    # The sizes above are only what the archive claims. Decompress with a hard cap to see what it really holds.
    budget = settings.max_zip_uncompressed_mb * 1024 * 1024
    try:
        for info in infos:
            with zf.open(info) as member:
                while chunk := member.read(1024 * 1024):
                    budget -= len(chunk)
                    if budget < 0:
                        raise MaliciousFile("The archive is too large when decompressed.")
    except RuntimeError as exc:  # an encrypted member: "password required"
        raise EncryptedFile() from exc
    except (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, EOFError, NotImplementedError, ValueError, OSError, struct.error) as exc:
        raise CorruptFile() from exc


# ----------------------------------------------------------------------------- input
@dataclass
class InputVerdict:
    allowed: bool
    reason: str | None = None  # "prompt_injection" | "jailbreak"
    soft: bool = False  # only soft (ambiguous) matches may be released by the model recheck


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")  # zero-width and other invisible characters
    return re.sub(r"\s+", " ", text).strip()


_INJ = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]
_JB = [re.compile(p, re.IGNORECASE if p != r"\bDAN\b" else 0) for p in JAILBREAK_PATTERNS]
_DOC = [re.compile(p, re.IGNORECASE) for p in DOCUMENT_INSTRUCTION_PATTERNS]
_KEY = [re.compile(p) for p in API_KEY_REGEXES]


def check_question(question: str | None, settings: Settings) -> str:
    """Empty / over-length → 422. Returns the stripped question."""
    q = (question or "").strip()
    if not q:
        raise InvalidRequest("The question must not be empty.")
    if len(q) > settings.max_question_chars:
        raise InvalidRequest(f"The question is too long (max {settings.max_question_chars} characters).")
    return q


def check_input(question: str) -> InputVerdict:
    text = _normalise(question)
    jb = [p for p in _JB if p.search(text)]
    inj = [p for p in _INJ if p.search(text)]
    hits = jb + inj
    if not hits:
        return InputVerdict(True)
    soft = all(p.pattern in SOFT_PATTERNS for p in hits)  # one hard match makes the whole refusal hard
    return InputVerdict(False, "jailbreak" if jb else "prompt_injection", soft)


# ----------------------------------------------------------------------------- output
def contains_key_like(text: str) -> bool:
    return any(p.search(text) for p in _KEY)


def _ngrams(text: str, n: int = 6) -> set[tuple[str, ...]]:
    words = re.findall(r"\w+", text.lower())
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def leaks_prompt(answer: str, system_prompt: str) -> bool:
    """Canary string, or ≥2 six-word sequences copied from the system prompt."""
    if PROMPT_CANARY.lower() in answer.lower():
        return True
    return len(_ngrams(answer) & _ngrams(system_prompt)) >= 2


def is_output_blocked(answer: str, system_prompt: str) -> bool:
    return contains_key_like(answer) or leaks_prompt(answer, system_prompt)


# ----------------------------------------------------------------------------- document (B6)
def scan_document_text(text: str) -> list[str]:
    """Return the instruction-like patterns found in a document's text (flag only; text stays data)."""
    normalised = _normalise(text)
    return [p.pattern for p in _DOC if p.search(normalised)]
