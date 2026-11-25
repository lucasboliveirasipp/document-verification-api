"""Logging estruturado (JSON) com sanitização automática.

Cada evento é emitido como uma linha JSON contendo campos técnicos estáveis
(timestamp, nível, evento, componente). Todos os campos extras passam por
sanitização/redaction antes da emissão, de modo que payloads, tokens, PII ou URLs assinadas nunca cheguem ao
log, mesmo se um chamador os fornecer por engano.

Este módulo não decide destino de logs (arquivo, coletor): usa o ``logging``
padrão do Python, que a plataforma configura conforme o ambiente.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from src.observability.sanitization import redact_mapping, redact_text, safe_error_summary

#: Campos reservados que não podem ser sobrescritos por dados do chamador.
_RESERVED_FIELDS = frozenset({"timestamp", "level", "event", "component", "error"})


class _JsonFormatter(logging.Formatter):
    """Formata registros já estruturados como uma única linha JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload = getattr(record, "structured", None)
        if isinstance(payload, dict):
            return json.dumps(payload, ensure_ascii=False, sort_keys=True)
        # Fallback seguro para registros não estruturados de terceiros.
        return json.dumps(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "event": "unstructured_log",
                "message": redact_text(record.getMessage()),
            },
            ensure_ascii=False,
            sort_keys=True,
        )


def configure_logging(level: str = "INFO") -> None:
    """Configura o handler raiz para emitir JSON sanitizado.

    Idempotente: substitui handlers existentes por um único handler JSON.

    Args:
        level: nível mínimo textual (ex.: ``"INFO"``, ``"WARNING"``).
    """
    root = logging.getLogger()
    root.setLevel(level.upper())
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    root.handlers = [handler]


class StructuredLogger:
    """Logger de eventos estruturados, sanitizados e correlacionados.

    Cada método de nível recebe o **nome do evento** (estável, sem espaços) e
    campos extras opcionais. Os extras são sanitizados; identificadores de
    correlação correntes são anexados automaticamente.
    """

    def __init__(self, component: str) -> None:
        """Cria o logger vinculado a um componente lógico (ex.: ``"gateway"``)."""
        self._component = component
        self._logger = logging.getLogger(component)

    def _emit(
        self,
        level: int,
        event: str,
        *,
        error: Optional[BaseException] = None,
        fields: Optional[dict] = None,
    ) -> None:
        record: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": logging.getLevelName(level),
            "event": redact_text(event),
            "component": self._component,
        }

        if fields:
            safe_fields = redact_mapping(fields)
            for key, value in safe_fields.items():
                if key not in _RESERVED_FIELDS:
                    record[key] = value

        if error is not None:
            record["error"] = safe_error_summary(error)

        self._logger.log(level, event, extra={"structured": record})

    def debug(self, event: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, event, fields=fields)

    def info(self, event: str, **fields: Any) -> None:
        self._emit(logging.INFO, event, fields=fields)

    def warning(self, event: str, **fields: Any) -> None:
        self._emit(logging.WARNING, event, fields=fields)

    def error(
        self, event: str, *, error: Optional[BaseException] = None, **fields: Any
    ) -> None:
        self._emit(logging.ERROR, event, error=error, fields=fields)


def get_logger(component: str) -> StructuredLogger:
    """Fábrica de :class:`StructuredLogger` para um componente lógico."""
    return StructuredLogger(component)
