"""Observabilidade do agente Reconhecimento de Documentação de Cadastro.

Reúne logging estruturado e sanitização/redaction.
Nenhum componente deste pacote registra conteúdo documental, OCR, tokens,
segredos, PII (CPF/CNPJ, conta bancária, nome completo) ou URLs assinadas.
"""
from src.observability.logging import (
    StructuredLogger,
    configure_logging,
    get_logger,
)
from src.observability.sanitization import (
    redact_mapping,
    redact_text,
    safe_error_summary,
)

__all__ = [
    "StructuredLogger",
    "configure_logging",
    "get_logger",
    "redact_mapping",
    "redact_text",
    "safe_error_summary",
]
