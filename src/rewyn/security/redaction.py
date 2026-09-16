"""Secret and PII redaction (spec §37).

Nothing reaches disk or the network without passing through a
:class:`Redactor`. Three mechanisms cooperate:

1. Key-name matching: any mapping key that looks like a credential
   (``api_key``, ``password``, ``authorization``…) has its value replaced.
2. Pattern matching: well-known credential formats (OpenAI/Anthropic keys,
   AWS access keys, GitHub tokens, JWTs, bearer headers, PEM blocks) are
   replaced wherever they appear inside strings.
3. Registered values: exact secret values registered at runtime (for
   example, the API key a model adapter was constructed with) are replaced
   even when they do not match a known format.

PII patterns (email, phone, card numbers) are available but off by default
because they are application policy, not a safety floor.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from re import Pattern
from typing import Any

REDACTED = "[REDACTED]"

SECRET_KEY_NAMES: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "api-key",
        "x-api-key",
        "authorization",
        "auth_token",
        "access_token",
        "refresh_token",
        "id_token",
        "token",
        "secret",
        "client_secret",
        "password",
        "passwd",
        "private_key",
        "secret_key",
        "session_token",
        "cookie",
        "set-cookie",
    }
)

_SECRET_PATTERNS: tuple[tuple[str, Pattern[str]], ...] = (
    ("openai_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{16,}")),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]{16,}=*")),
    (
        "pem_block",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
)

# Order matters: card numbers are checked (with a Luhn test) before the looser
# phone pattern so a card is never partially consumed as a phone number.
_PII_PATTERNS: tuple[tuple[str, Pattern[str]], ...] = (
    ("card", re.compile(r"\b\d(?:[ \-]?\d){12,18}\b")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    (
        "phone",
        re.compile(r"(?<!\w)\+?\d{1,3}[\s\-.]?\(?\d{2,4}\)?[\s\-.]?\d{3,4}[\s\-.]?\d{3,4}\b"),
    ),
)


def _luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


@dataclass(slots=True)
class Redactor:
    """Recursively redact secrets (and optionally PII) from JSON-like data."""

    redact_pii: bool = False
    extra_patterns: list[Pattern[str]] = field(default_factory=list)
    secret_key_names: frozenset[str] = SECRET_KEY_NAMES
    _values: set[str] = field(default_factory=set, repr=False)

    def register_secret(self, value: str | None) -> None:
        """Register an exact secret value so it is redacted wherever it appears."""
        if value and len(value) >= 6:
            self._values.add(value)

    def register_secrets(self, values: Iterable[str | None]) -> None:
        for value in values:
            self.register_secret(value)

    def is_secret_key(self, key: str) -> bool:
        return key.strip().lower() in self.secret_key_names

    def redact_text(self, text: str) -> str:
        for value in sorted(self._values, key=len, reverse=True):
            text = text.replace(value, REDACTED)
        for _, pattern in _SECRET_PATTERNS:
            text = pattern.sub(REDACTED, text)
        for pattern in self.extra_patterns:
            text = pattern.sub(REDACTED, text)
        if self.redact_pii:
            text = self._redact_pii(text)
        return text

    def _redact_pii(self, text: str) -> str:
        for name, pattern in _PII_PATTERNS:
            if name == "card":
                text = pattern.sub(
                    lambda m: (
                        REDACTED if _luhn_valid(re.sub(r"\D", "", m.group(0))) else m.group(0)
                    ),
                    text,
                )
            else:
                text = pattern.sub(REDACTED, text)
        return text

    def redact(self, value: Any) -> Any:
        """Return a redacted deep copy of ``value``."""
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, Mapping):
            return {
                str(k): REDACTED
                if self.is_secret_key(str(k)) and v not in (None, "")
                else self.redact(v)
                for k, v in value.items()
            }
        if isinstance(value, list | tuple | set | frozenset):
            return [self.redact(item) for item in value]
        return value


_default = Redactor()


def default_redactor() -> Redactor:
    """Process-wide redactor. Model adapters register their credentials here."""
    return _default


def redact(value: Any) -> Any:
    return _default.redact(value)
