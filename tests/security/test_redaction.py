from __future__ import annotations

from rewyn.security.redaction import REDACTED, Redactor, _luhn_valid


def test_secret_keys_are_redacted_by_name() -> None:
    redactor = Redactor()
    data = {"api_key": "abc", "Authorization": "Basic xyz", "name": "ok", "empty": ""}
    out = redactor.redact(data)
    assert out == {"api_key": REDACTED, "Authorization": REDACTED, "name": "ok", "empty": ""}


def test_known_secret_formats_are_redacted_inside_text() -> None:
    redactor = Redactor()
    samples = [
        "key sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456 here",
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
        "AKIAIOSFODNN7EXAMPLE",
        "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "Bearer abcdefghijklmnopqrstuvwxyz0123456789",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
    ]
    for sample in samples:
        assert REDACTED in redactor.redact_text(sample), sample


def test_registered_values_are_redacted_everywhere() -> None:
    redactor = Redactor()
    redactor.register_secret("hunter22")
    redactor.register_secret("ab")  # too short: ignored
    out = redactor.redact({"nested": ["password was hunter22", {"k": "ab"}]})
    assert out == {"nested": [f"password was {REDACTED}", {"k": "ab"}]}


def test_pii_is_opt_in() -> None:
    text = "mail jane@example.com card 4111 1111 1111 1111 phone +1 415 555 0134"
    assert Redactor().redact_text(text) == text
    out = Redactor(redact_pii=True).redact_text(text)
    assert out == f"mail {REDACTED} card {REDACTED} phone {REDACTED}"


def test_card_detection_requires_luhn_checksum() -> None:
    assert _luhn_valid("4111111111111111")
    assert not _luhn_valid("1234567890123456")


def test_non_json_values_pass_through() -> None:
    redactor = Redactor()
    assert redactor.redact(42) == 42
    assert redactor.redact(None) is None
    assert redactor.redact(("a", "sk-ant-api03-abcdefghijklmnopqrstuvwxyz")) == ["a", REDACTED]
