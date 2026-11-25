"""Erros do BFF e sua tradução para HTTP.

Cada erro carrega uma categoria estável (para log e alerta), o status HTTP e a
mensagem que o cliente pode ver. A mensagem interna (``message``) vai só para o
log, já sanitizado; o cliente recebe :meth:`DomainError.public_message`.

Regras:

* nenhuma mensagem transporta conteúdo documental, PII, token ou URL assinada;
* nenhum erro carrega o corpo bruto da resposta externa.
"""
from __future__ import annotations

import enum
from typing import Optional


class ErrorCategory(str, enum.Enum):
    """Categoria estável de falha, usada em logs e alertas."""

    VALIDATION = "VALIDATION"
    CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"
    UNEXPECTED = "UNEXPECTED"


class DomainError(Exception):
    """Base dos erros do BFF.

    Args:
        message: texto curto e sanitizado, seguro para log.
    """

    category: ErrorCategory = ErrorCategory.UNEXPECTED
    http_status: int = 500
    #: Mensagem genérica para o cliente. ``None`` indica que ``message`` já foi
    #: escrita para o usuário final e pode ser devolvida como está.
    client_message: Optional[str] = "Erro interno. Tente novamente mais tarde."

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message

    def public_message(self) -> str:
        """Mensagem que pode ser devolvida ao cliente."""
        return self.message if self.client_message is None else self.client_message


class ValidationError(DomainError):
    """Entrada rejeitada pelo BFF. A mensagem é escrita para o usuário final."""

    category = ErrorCategory.VALIDATION
    http_status = 400
    client_message = None


class CapabilityUnavailableError(DomainError):
    """Recurso da Gateway ainda não configurado neste ambiente (falha fechada)."""

    category = ErrorCategory.CAPABILITY_UNAVAILABLE
    http_status = 503
    client_message = "Funcionalidade ainda não configurada neste ambiente."


class DependencyUnavailableError(DomainError):
    """Gateway ou emissor de identidade indisponível, lento ou com erro.

    Args:
        retryable: se uma nova tentativa faz sentido.
        retry_after_seconds: espera sugerida pela dependência, quando houver.
    """

    category = ErrorCategory.DEPENDENCY_UNAVAILABLE
    http_status = 503
    client_message = "Serviço de IA indisponível no momento. Tente novamente em instantes."

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        retry_after_seconds: Optional[float] = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class ContractViolationError(DomainError):
    """Resposta da dependência fora do contrato (inclui HTTP 200 com corpo inválido)."""

    category = ErrorCategory.CONTRACT_VIOLATION
    http_status = 502
    client_message = "O serviço de IA respondeu em formato inesperado. Tente novamente."


class ResourceNotFoundError(ContractViolationError):
    """A dependência respondeu 404.

    Continua sendo quebra de contrato (502), mas o polling precisa reconhecê-lo:
    logo após o start, a execução pode ainda não estar registrada.
    """
