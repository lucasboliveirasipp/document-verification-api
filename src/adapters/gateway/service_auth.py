"""Identidade de serviço — ``client_credentials`` no Azure AD.

Implementa o contrato **real e funcional** observado no projeto de referência
``api-agente-orientador-curadoria`` (publicado no portal).

Contrato observado:

* a autenticação acontece no **Azure AD**, não na Gateway::

      POST https://login.microsoftonline.com/{TENANTID}/oauth2/v2.0/token
      Content-Type: application/x-www-form-urlencoded

      grant_type=client_credentials
      client_id={GATEWAYAPPID}
      client_secret={GATEWAYCLIENTSECRET}
      scope={GATEWAYAPPIDURI}/.default

* a resposta traz ``access_token``; o token vira ``Authorization: Bearer`` em
  todas as chamadas à plataforma;
* a expiração é lida decodificando o JWT **sem verificar assinatura** (só para
  obter ``exp``), e o token é renovado com margem de segurança.

Diferenças deliberadas em relação à referência:

* o token é **sanitizado** contra CRLF/header injection (a referência também o
  faz) e nunca aparece em log, métrica ou mensagem de erro;
* TLS segue :class:`~src.settings.TLSSettings` — a referência usa
  ``verify=False``, o que nossa política proíbe em HML/PRD;
* a margem de renovação vem de :class:`~src.settings.TokenCacheSettings`
  (padrão 60s, igual ao observado) em vez de constante no código;
* nenhuma mensagem de erro carrega o corpo da resposta do emissor;
* ``verify_signature=False`` é usado **apenas** para ler ``exp`` de um token que
  acabamos de receber por canal TLS do próprio emissor. Isso não é validação de
  identidade e não deve ser replicado na validação do JWT do usuário (G5), onde
  a verificação de assinatura é obrigatória.

O ``client_secret`` **não** trafega por :class:`~src.settings.AppSettings`: é
lido do ambiente/cofre no momento da requisição e não é retido em atributo.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Mapping, Optional

import jwt
import requests

from src.domain.errors import (
    CapabilityUnavailableError,
    ContractViolationError,
    DependencyUnavailableError,
)
from src.domain.models import ServiceAccessToken
from src.observability.logging import get_logger
from src.observability.sanitization import sanitize_header_value
from src.settings import AppSettings

_logger = get_logger("gateway.service_auth")

#: Nome da variável que carrega o segredo do cliente. Apenas o **nome** vive no
#: código; o valor é injetado por cofre/pipeline.
_CLIENT_SECRET_ENV = "GATEWAYCLIENTSECRET"


class AzureAdServiceAuthAdapter:
    """Obtém e reutiliza a credencial de serviço via ``client_credentials``."""

    def __init__(
        self,
        settings: AppSettings,
        *,
        session: Optional[requests.Session] = None,
        secret_provider: Optional[Callable[[], Optional[str]]] = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        """Cria o adaptador.

        Args:
            settings: configuração validada (identidade, TLS, timeouts, margem
                de renovação do token).
            session: sessão HTTP injetável, para reuso de conexão e testes.
            secret_provider: função que devolve o ``client_secret``. O padrão lê
                do ambiente no momento do uso, mantendo o segredo fora de
                atributos de longa duração.
            now: relógio injetável, para testes determinísticos.
        """
        self._settings = settings
        self._session = session or requests.Session()
        self._secret_provider = secret_provider or self._secret_from_environment
        self._now = now
        self._cached: Optional[ServiceAccessToken] = None
        # Múltiplas tarefas podem pedir token simultaneamente; sem o lock,
        # todas disparariam autenticação ao mesmo tempo quando o token expira.
        self._lock = threading.Lock()

    # ─── Porta ───────────────────────────────────────────────────────────────

    def get_service_token(self) -> ServiceAccessToken:
        """Devolve uma credencial válida, reautenticando quando necessário."""
        margin = self._settings.token_cache.safety_margin_seconds
        with self._lock:
            cached = self._cached
            if cached is not None and cached.is_valid_at(
                self._now(), safety_margin_seconds=margin
            ):
                return cached
            token = self._authenticate()
            self._cached = token
            return token

    def invalidate(self) -> None:
        """Descarta a credencial em cache (usado quando a Gateway a rejeita)."""
        with self._lock:
            self._cached = None

    # ─── Núcleo ──────────────────────────────────────────────────────────────

    def _authenticate(self) -> ServiceAccessToken:
        identity = self._settings.service_identity
        token_endpoint = identity.token_endpoint()
        scope = identity.scope()
        secret = self._secret_provider()

        if not token_endpoint or not identity.client_id or not scope or not secret:
            # Gate G1: identidade não provisionada neste ambiente. Falha fechada,
            # sem indicar qual valor específico faltou.
            raise CapabilityUnavailableError(
                "Identidade de servico nao configurada neste ambiente."
            )

        payload = self._request_token(
            token_endpoint,
            {
                "grant_type": "client_credentials",
                "client_id": identity.client_id,
                "client_secret": secret,
                "scope": scope,
            },
        )

        raw_token = payload.get("access_token")
        if not raw_token or not isinstance(raw_token, str):
            # Guarda de contrato: 200 sem token utilizável não é sucesso.
            raise ContractViolationError(
                "Emissor de identidade nao retornou credencial utilizavel."
            )

        token_value = sanitize_header_value(raw_token)
        return ServiceAccessToken(
            value=token_value,
            expires_at=self._expiry_from(token_value, payload),
        )

    def _request_token(
        self, token_endpoint: str, form: Mapping[str, str]
    ) -> Mapping[str, object]:
        try:
            response = self._session.post(
                token_endpoint,
                data=dict(form),
                timeout=(
                    self._settings.timeouts.connect_seconds,
                    self._settings.timeouts.read_seconds,
                ),
                verify=self._settings.tls.requests_verify,
            )
        except requests.exceptions.SSLError as exc:
            # SSLError herda de ConnectionError, mas não é transitório.
            _logger.error("service_token_tls_error", error=exc)
            raise DependencyUnavailableError(
                "Falha de validacao TLS com o emissor de identidade.", retryable=False
            ) from exc
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise DependencyUnavailableError(
                "Emissor de identidade indisponivel ou lento.", retryable=True
            ) from exc
        except requests.RequestException as exc:
            raise DependencyUnavailableError(
                "Falha de comunicacao com o emissor de identidade.",
                retryable=False,
            ) from exc
        except OSError as exc:
            # CA bundle inexistente (TLS_CA_BUNDLE_PATH): configuração local.
            _logger.error("tls_ca_bundle_invalido", error=exc)
            raise DependencyUnavailableError(
                "Falha de validacao TLS com o emissor de identidade.", retryable=False
            ) from exc

        if response.status_code >= 400:
            # Nenhuma mensagem carrega o corpo da resposta: em fluxo de
            # credencial ele pode conter detalhe sensível. O log leva só o
            # status e os códigos AADSTS, que bastam para achar a causa
            # (secret inválido, app inexistente, scope errado).
            _logger.warning(
                "service_token_rejected",
                status_code=response.status_code,
                error_codes=self._aadsts_codes(response),
            )
            raise DependencyUnavailableError(
                "Emissor de identidade recusou a solicitacao de credencial.",
                retryable=response.status_code in (408, 429, 500, 502, 503, 504),
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise ContractViolationError(
                "Resposta do emissor de identidade nao e JSON valido."
            ) from exc

        if not isinstance(payload, Mapping):
            raise ContractViolationError(
                "Resposta do emissor de identidade sem corpo utilizavel."
            )
        return payload

    @staticmethod
    def _aadsts_codes(response: requests.Response) -> List[int]:
        try:
            corpo = response.json()
        except ValueError:
            return []
        codigos = corpo.get("error_codes") if isinstance(corpo, Mapping) else None
        if not isinstance(codigos, list):
            return []
        return [c for c in codigos if isinstance(c, int) and not isinstance(c, bool)]

    def _expiry_from(
        self, token_value: str, payload: Mapping[str, object]
    ) -> datetime:
        """Resolve a expiração pelo ``exp`` do JWT, com fallback em ``expires_in``.

        A referência decodifica o JWT para ler ``exp``. Mantemos isso, mas com
        fallback: se o token não for um JWT legível, usamos ``expires_in``, e só
        falhamos fechado se nenhuma das duas formas resolver.
        """
        try:
            # NOSONAR python:S6779 - decodificação SEM verificar assinatura é
            # deliberada e segura aqui: este token não é uma credencial de
            # entrada a ser confiada, é um token que NÓS acabamos de receber do
            # próprio emissor por canal TLS validado. O único campo lido é
            # ``exp``, usado para agendar a renovação; nenhuma decisão de
            # autorização depende deste conteúdo. Verificar a assinatura exigiria
            # as chaves públicas do emissor sem qualquer ganho de segurança.
            # Esta abordagem NÃO deve ser replicada na validação do JWT do
            # usuário (gate G5), onde a verificação é obrigatória.
            decoded = jwt.decode(
                token_value,
                options={"verify_signature": False, "verify_exp": False},
                algorithms=None,
            )
            exp = decoded.get("exp")
            if exp is not None:
                return datetime.fromtimestamp(float(exp), tz=timezone.utc)
        except Exception:  # noqa: BLE001 - token opaco: tenta o fallback
            _logger.warning("service_token_exp_unreadable")

        expires_in = payload.get("expires_in")
        if isinstance(expires_in, (int, float)) and expires_in > 0:
            return self._now() + timedelta(seconds=float(expires_in))
        if isinstance(expires_in, str) and expires_in.strip().isdigit():
            return self._now() + timedelta(seconds=float(expires_in))

        raise ContractViolationError(
            "Credencial de servico sem expiracao determinavel."
        )

    @staticmethod
    def _secret_from_environment() -> Optional[str]:
        value = os.environ.get(_CLIENT_SECRET_ENV)
        return value.strip() if value else None
