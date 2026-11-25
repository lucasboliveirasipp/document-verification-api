import os
"""Testes de logging estruturado e sanitização.

Somente valores sintéticos. Verificam que dados proibidos (tokens, PII, URLs
assinadas, conteúdo extenso) nunca chegam ao log.
"""
import json
import logging

import pytest

from src.observability.logging import configure_logging, get_logger
from src.observability.sanitization import (
    REDACTED,
    redact_mapping,
    redact_text,
    safe_error_summary,
)


# ─── sanitization: valores ────────────────────────────────────────────────────

def test_redact_text_removes_bearer_and_jwt():
    jwt = "aaaaaaaaaa.bbbbbbbbbb.cccccccccc"
    assert REDACTED in redact_text(f"Authorization: Bearer {jwt}")
    assert jwt not in redact_text(f"token={jwt}")


def test_redact_text_removes_cpf_and_cnpj():
    assert "123.456.789-09" not in redact_text("cliente 123.456.789-09")
    assert "12.345.678/0001-95" not in redact_text("empresa 12.345.678/0001-95")


def test_redact_text_removes_signed_url():
    url = "https://store.example.com/file.pdf?sig=TOKEN&se=2026-01-01"
    assert "sig=ABC123" not in redact_text(f"download {url}")
    assert REDACTED in redact_text(url)


def test_redact_text_truncates_long_content():
    long = "x" * 5000
    out = redact_text(long)
    assert out.endswith("...[truncated]")
    assert len(out) < 400


def test_redact_text_handles_non_string_safely():
    assert isinstance(redact_text(12345), str)
    assert redact_text(None) == "None"


# ─── sanitization: mapeamentos ────────────────────────────────────────────────

def test_redact_mapping_redacts_sensitive_keys():
    data = {
        "authorization": "Bearer secret",
        "client_secret": "shhh",
        "cpf": "123.456.789-09",
        "analysis_id": "abc-123",
        "nested": {"password": "p", "count": 3},
    }
    out = redact_mapping(data)
    assert out["authorization"] == REDACTED
    assert out["client_secret"] == REDACTED
    assert out["cpf"] == REDACTED
    assert out["analysis_id"] == "abc-123"  # técnico, preservado
    assert out["nested"]["password"] == REDACTED
    assert out["nested"]["count"] == 3


def test_redact_mapping_limits_depth():
    # Estrutura profunda não deve estourar recursão.
    node = {"k": "v"}
    for _ in range(20):
        node = {"child": node, "token": "x"}
    out = redact_mapping(node)
    assert isinstance(out, dict)


def test_redact_mapping_redacts_values_in_lists():
    data = {"items": ["Bearer aaaaaaaaaa.bbbbbbbbbb.cccccccccc", "ok"]}
    out = redact_mapping(data)
    assert REDACTED in out["items"][0]
    assert out["items"][1] == "ok"


# ─── safe_error_summary ───────────────────────────────────────────────────────

def test_safe_error_summary_sanitizes_message():
    err = RuntimeError("falha com token=aaaaaaaaaa.bbbbbbbbbb.cccccccccc")
    summary = safe_error_summary(err)
    assert summary.startswith("RuntimeError")
    assert "aaaaaaaaaa.bbbbbbbbbb.cccccccccc" not in summary


def test_safe_error_summary_without_message():
    assert safe_error_summary(ValueError()) == "ValueError"







# ─── logger estruturado ───────────────────────────────────────────────────────

def _parse_records(caplog) -> list[dict]:
    records = []
    for rec in caplog.records:
        structured = getattr(rec, "structured", None)
        if isinstance(structured, dict):
            records.append(structured)
    return records


def test_structured_logger_emits_sanitized_fields(caplog):
    logger = get_logger("test-component")
    with caplog.at_level(logging.INFO):
        logger.info(
            "upload_started",
            authorization="Bearer aaaaaaaaaa.bbbbbbbbbb.cccccccccc",
            file_count=2,
            cpf="123.456.789-09",
        )
    records = _parse_records(caplog)
    assert len(records) == 1
    rec = records[0]
    assert rec["event"] == "upload_started"
    assert rec["component"] == "test-component"
    assert rec["authorization"] == REDACTED
    assert rec["cpf"] == REDACTED
    assert rec["file_count"] == 2




def test_structured_logger_error_summary(caplog):
    logger = get_logger("test-component")
    with caplog.at_level(logging.ERROR):
        logger.error("dependency_failed", error=RuntimeError("boom secret token=x"))
    rec = _parse_records(caplog)[0]
    assert rec["error"].startswith("RuntimeError")


def test_reserved_fields_cannot_be_overwritten(caplog):
    logger = get_logger("test-component")
    with caplog.at_level(logging.INFO):
        logger.info("evt", component="fake", level="fake", timestamp="fake")
    rec = _parse_records(caplog)[0]
    assert rec["component"] == "test-component"
    assert rec["event"] == "evt"
    assert rec["level"] == "INFO"


def test_configure_logging_emits_valid_json(capsys):
    configure_logging("INFO")
    logger = get_logger("json-component")
    logger.info("configured_event", detail="ok")
    captured = capsys.readouterr()
    line = captured.err.strip().splitlines()[-1]
    parsed = json.loads(line)
    assert parsed["event"] == "configured_event"
    assert parsed["component"] == "json-component"
    # Restaura logging padrão para não afetar outros testes.
    logging.getLogger().handlers.clear()


# ─── sanitize_header_value (anti CRLF/header injection) ──────────────────────

def test_sanitize_header_value_removes_crlf():
    from src.observability.sanitization import sanitize_header_value

    # Vetor clássico de header injection: CRLF seguido de cabeçalho falso.
    sanitized = sanitize_header_value("token\r\nX-Admin: true")
    assert "\r" not in sanitized and "\n" not in sanitized
    assert sanitized == "tokenX-Admin: true"


def test_sanitize_header_value_removes_control_characters():
    from src.observability.sanitization import sanitize_header_value

    assert sanitize_header_value("abc\x00\x07def") == "abcdef"


def test_sanitize_header_value_preserves_unicode():
    from src.observability.sanitization import sanitize_header_value

    assert sanitize_header_value("análise-çã") == "análise-çã"


def test_sanitize_header_value_handles_none_and_non_string():
    from src.observability.sanitization import sanitize_header_value

    assert sanitize_header_value(None) == ""
    assert sanitize_header_value(123) == "123"


def test_sanitize_header_value_truncates_oversized_input():
    from src.observability.sanitization import MAX_HEADER_LEN, sanitize_header_value

    assert len(sanitize_header_value("a" * (MAX_HEADER_LEN + 500))) == MAX_HEADER_LEN


def test_sanitize_header_value_preserves_secret_value():
    # Diferente de redact_text: aqui o objetivo é transporte seguro, não log.
    from src.observability.sanitization import sanitize_header_value

    assert sanitize_header_value("valor-de-token") == "valor-de-token"
