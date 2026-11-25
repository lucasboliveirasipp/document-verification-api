"""Transporte HTTP comum da Gateway.

Única classe que conhece ``requests``. Os adaptadores (Gerador de Texto, File
Manager, OCR) recebem uma instância dela.

Responsabilidades:

* ``timeout=(connect, read)`` e TLS conforme :class:`~src.settings.TLSSettings`;
* ``Authorization: Bearer`` com o token do Azure AD, sanitizado contra CRLF;
* ``connection_mode`` só quando configurado (a Curadoria não envia);
* retry com backoff apenas em GET e upload, nunca em ``start-workflow``;
* classificar falhas em erros do BFF sem carregar o corpo da resposta externa,
  registrando o detalhe técnico no log;
* guarda de sucesso aparente: HTTP 200 com corpo nulo, não-JSON ou sem o campo
  esperado é :class:`~src.domain.errors.ContractViolationError`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping, Optional
from urllib.parse import urlsplit

import requests

from src.domain.errors import (
    ContractViolationError,
    DependencyUnavailableError,
    DomainError,
    ResourceNotFoundError,
)
from src.observability.logging import get_logger
from src.observability.sanitization import sanitize_header_value
from src.settings import AppSettings

_logger = get_logger("gateway.transport")

#: Status que justificam nova tentativa.
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class GatewayResponse:
    """Resposta 2xx já validada como objeto JSON."""

    status_code: int
    payload: Mapping[str, Any]


class GatewayTransport:
    """Cliente HTTP de vida longa para a Gateway."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        session: Optional[requests.Session] = None,
        service_auth: Optional[Any] = None,
        sleep=time.sleep,
    ) -> None:
        """Cria o transporte.

        Args:
            settings: configuração validada (timeouts, retry, TLS).
            session: sessão HTTP reaproveitada entre chamadas (e fake em teste).
            service_auth: objeto com ``get_service_token()`` e ``invalidate()``.
                Sem ele, nenhuma chamada leva ``Authorization``.
            sleep: função de espera entre tentativas (injetável em teste).
        """
        self._settings = settings
        self._session = session or requests.Session()
        self._service_auth = service_auth
        self._sleep = sleep

    # ─── API pública ─────────────────────────────────────────────────────────

    def get_json(self, url: str, *, required_field: Optional[str] = None) -> GatewayResponse:
        """``GET`` com retry; ``required_field`` ausente no corpo é quebra de contrato."""
        return self._send("GET", url, retryable=True, required_field=required_field)

    def post_json(
        self,
        url: str,
        *,
        json_body: Mapping[str, Any],
        required_field: Optional[str] = None,
        idempotent: bool = False,
    ) -> GatewayResponse:
        """``POST`` JSON. Só repete quando ``idempotent=True`` (start-workflow não é)."""
        return self._send(
            "POST", url, retryable=idempotent, required_field=required_field, json=json_body
        )

    def post_multipart(
        self,
        url: str,
        *,
        files: Mapping[str, Any],
        data: Optional[Mapping[str, str]] = None,
        required_field: Optional[str] = None,
    ) -> GatewayResponse:
        """Upload ``multipart/form-data`` (idempotente com ``overwrite=true``).

        ``data`` são os campos de formulário que acompanham o arquivo.
        """
        return self._send(
            "POST", url, retryable=True, required_field=required_field, files=files, data=data
        )

    def delete(self, url: str) -> None:
        """``DELETE`` com retry (é idempotente); aceita resposta sem corpo."""
        self._send("DELETE", url, retryable=True, required_field=None, body_expected=False)

    # ─── Núcleo ──────────────────────────────────────────────────────────────

    def _send(
        self,
        method: str,
        url: str,
        *,
        retryable: bool,
        required_field: Optional[str],
        body_expected: bool = True,
        **request_kwargs: Any,
    ) -> GatewayResponse:
        max_attempts = self._settings.retry.max_attempts if retryable else 1
        attempt = 1
        while True:
            try:
                return self._attempt(method, url, required_field, body_expected, request_kwargs)
            except DependencyUnavailableError as exc:
                if not exc.retryable or attempt >= max_attempts:
                    raise
                self._backoff(attempt, exc.retry_after_seconds)
                attempt += 1

    def _attempt(
        self,
        method: str,
        url: str,
        required_field: Optional[str],
        body_expected: bool,
        request_kwargs: Mapping[str, Any],
    ) -> GatewayResponse:
        path = urlsplit(url).path
        try:
            response = self._session.request(
                method,
                url,
                params=self._query_params(),
                headers=self._headers(),
                timeout=(
                    self._settings.timeouts.connect_seconds,
                    self._settings.timeouts.read_seconds,
                ),
                verify=self._settings.tls.requests_verify,
                **request_kwargs,
            )
        except requests.exceptions.SSLError as exc:
            # SSLError herda de ConnectionError, mas repetir não conserta uma
            # cadeia de certificado inválida: falha na hora e com log próprio.
            _logger.error("gateway_tls_error", error=exc, method=method, path=path)
            raise DependencyUnavailableError(
                "Falha de validacao TLS com a Gateway.", retryable=False
            ) from exc
        except (requests.Timeout, requests.ConnectionError) as exc:
            _logger.warning(
                "gateway_unreachable", method=method, path=path, reason=type(exc).__name__
            )
            raise DependencyUnavailableError(
                "Gateway indisponivel ou lenta.", retryable=True
            ) from exc
        except requests.RequestException as exc:
            _logger.error("gateway_request_error", error=exc, method=method, path=path)
            raise DependencyUnavailableError(
                "Falha de comunicacao com a Gateway.", retryable=False
            ) from exc
        except OSError as exc:
            # O requests levanta OSError puro quando o TLS_CA_BUNDLE_PATH aponta
            # para um arquivo que não existe: é configuração, não a Gateway.
            _logger.error("tls_ca_bundle_invalido", error=exc, method=method, path=path)
            raise DependencyUnavailableError(
                "Falha de validacao TLS com a Gateway.", retryable=False
            ) from exc

        if response.status_code >= 400:
            campos = {"method": method, "path": path, "status_code": response.status_code}
            if 400 <= response.status_code < 500 and response.status_code != 422:
                # O motivo da recusa (ex.: "Missing claims: roles.", ou por que o
                # File Manager não aceitou um arquivo) só existe no corpo. O 422
                # fica de fora: ele repete a entrada, com o texto dos documentos.
                detalhe = self._motivo_da_recusa(response)
                if detalhe:
                    campos["detail"] = detalhe
            _logger.warning("gateway_http_error", **campos)
            raise self._error_for_status(response)
        if not body_expected:
            return GatewayResponse(status_code=response.status_code, payload={})
        return self._validate_body(response, required_field)

    # ─── Montagem de requisição ──────────────────────────────────────────────

    def _query_params(self) -> Optional[MutableMapping[str, str]]:
        # A Curadoria (referência em produção) não envia connection_mode; só
        # entra quando GATEWAY_CONNECTION_MODE estiver configurado.
        mode = self._settings.gateway_connection_mode
        return {"connection_mode": mode} if mode else None

    def _headers(self) -> MutableMapping[str, str]:
        if self._service_auth is None:
            return {}
        token = self._service_auth.get_service_token()
        return {"Authorization": f"Bearer {sanitize_header_value(token.value)}"}

    # ─── Classificação e guarda de contrato ──────────────────────────────────

    def _error_for_status(self, response: requests.Response) -> DomainError:
        code = response.status_code
        if code in _RETRYABLE_STATUS:
            return DependencyUnavailableError(
                "Gateway indisponivel ou sob limitacao de uso.",
                retryable=True,
                retry_after_seconds=self._parse_retry_after(response),
            )
        if code in (401, 403):
            # Descarta o token para a próxima chamada autenticar de novo.
            if self._service_auth is not None:
                self._service_auth.invalidate()
            return DependencyUnavailableError(
                "Credencial de servico rejeitada pela Gateway.", retryable=False
            )
        if code == 404:
            # Separado porque o polling tolera 404 logo após o start-workflow.
            return ResourceNotFoundError("Recurso nao encontrado na Gateway (HTTP 404).")
        # Demais 4xx: quem montou a requisição foi o BFF (payload, URL ou código
        # de workflow), então é quebra de contrato, não erro do usuário.
        return ContractViolationError(f"Gateway recusou a requisicao (HTTP {code}).")

    @staticmethod
    def _motivo_da_recusa(response: requests.Response) -> Optional[str]:
        """``detail`` ou ``message`` em texto; listas e objetos ficam de fora."""
        try:
            corpo = response.json()
        except ValueError:
            return None
        if not isinstance(corpo, Mapping):
            return None
        for chave in ("detail", "message"):
            if isinstance(corpo.get(chave), str):
                return corpo[chave]
        return None

    @staticmethod
    def _validate_body(
        response: requests.Response, required_field: Optional[str]
    ) -> GatewayResponse:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ContractViolationError("Resposta da Gateway nao e JSON valido.") from exc
        if not isinstance(payload, Mapping):
            raise ContractViolationError("Resposta da Gateway sem corpo utilizavel.")
        if required_field is not None and not payload.get(required_field):
            raise ContractViolationError("Resposta da Gateway sem o campo esperado.")
        return GatewayResponse(status_code=response.status_code, payload=payload)

    # ─── Backoff ─────────────────────────────────────────────────────────────

    def _backoff(self, attempt: int, retry_after: Optional[float]) -> None:
        retry = self._settings.retry
        delay = min(retry.backoff_base_seconds * (2 ** (attempt - 1)), retry.backoff_max_seconds)
        if retry_after is not None:
            delay = max(delay, min(retry_after, retry.backoff_max_seconds))
        _logger.warning("gateway_transport_retry", attempt=attempt, delay_seconds=delay)
        self._sleep(delay)

    @staticmethod
    def _parse_retry_after(response: requests.Response) -> Optional[float]:
        try:
            value = float(response.headers.get("Retry-After"))
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None
