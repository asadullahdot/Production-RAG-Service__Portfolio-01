"""Domain exceptions mapped to (HTTP status, error code) — PLAN.md §7.2."""
from __future__ import annotations

from typing import Any


class AppError(Exception):
    status_code = 500
    code = "internal_error"
    default_message = "Internal server error."

    def __init__(self, message: str | None = None, details: dict[str, Any] | None = None):
        self.message = message or self.default_message
        self.details = details
        super().__init__(self.message)


class EmptyFile(AppError):
    status_code, code, default_message = 400, "empty_file", "The uploaded file is empty."


class NoText(AppError):
    status_code, code, default_message = 400, "no_text", "No extractable text found (scanned or image-only document?)."


class CorruptFile(AppError):
    status_code, code, default_message = 400, "corrupt_file", "The file is corrupt or not a valid document."


class EncryptedFile(AppError):
    status_code, code, default_message = 400, "encrypted_file", "Encrypted or password-protected files are not supported."


class MaliciousFile(AppError):
    status_code, code, default_message = 400, "malicious_file", "The file was rejected by document safety checks."


class DuplicateDocument(AppError):
    status_code, code, default_message = 409, "duplicate_document", "This document has already been uploaded."


class FileTooLarge(AppError):
    status_code, code, default_message = 413, "file_too_large", "The file exceeds the allowed size, page count or chunk count."


class UnsupportedType(AppError):
    status_code, code, default_message = 415, "unsupported_type", "Unsupported file type."


class InvalidRequest(AppError):
    status_code, code, default_message = 422, "invalid_request", "Invalid request."


class Unauthorized(AppError):
    status_code, code, default_message = 401, "unauthorized", "Authentication required or credentials invalid."


class EmailTaken(AppError):
    status_code, code, default_message = 409, "email_taken", "That email is already registered."


class SignupClosed(AppError):
    status_code, code, default_message = 403, "signup_closed", "Signup is currently closed."


class NotFound(AppError):
    status_code, code, default_message = 404, "not_found", "Not found."


class RateLimited(AppError):
    status_code, code, default_message = 429, "rate_limited", "Too many requests. Please slow down."


class UpstreamUnavailable(AppError):
    status_code, code = 503, "upstream_unavailable"
    default_message = "A required upstream service is unavailable. Please try again shortly."
