"""Tipos de dados trocados com os adaptadores da Gateway.

Identificadores externos continuam opacos (``str``) até a homologação do
contrato de cada recurso.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Tuple


@dataclass(frozen=True)
class ServiceAccessToken:
    """Credencial de serviço. O valor é sensível e nunca vai para log."""

    value: str
    expires_at: datetime

    def is_valid_at(self, moment: datetime, *, safety_margin_seconds: float = 0.0) -> bool:
        """Indica se o token ainda é utilizável em ``moment``, com margem."""
        return (self.expires_at - moment).total_seconds() > safety_margin_seconds

    def __repr__(self) -> str:  # pragma: no cover - proteção contra log acidental
        return f"ServiceAccessToken(value='***', expires_at={self.expires_at!r})"


@dataclass(frozen=True)
class DocumentUpload:
    """Documento validado, pronto para envio ao File Manager."""

    safe_name: str
    media_type: str
    content: bytes
    size_bytes: int
    checksum: Optional[str] = None


@dataclass(frozen=True)
class DocumentReference:
    """Referência devolvida pelo File Manager (id e path são obrigatórios no OCR)."""

    external_document_id: str
    safe_name: str
    media_type: str
    size_bytes: int
    storage_path: Optional[str] = None
    checksum: Optional[str] = None
    received_at: Optional[datetime] = None


@dataclass(frozen=True)
class WorkflowSubmission:
    """Pedido de início do workflow de OCR para um lote já enviado."""

    analysis_type: str
    documents: Tuple[DocumentReference, ...]
    parameters: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowExecutionReference:
    """Execução iniciada na Gateway."""

    external_execution_id: str
    accepted_at: Optional[datetime] = None
