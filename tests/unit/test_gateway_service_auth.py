"""Testes do adaptador de identidade de serviço (Task 5.1) com fakes.

Cobrem o contrato ``client_credentials`` observado no projeto de referência
``api-agente-orientador-curadoria``: forma do formulário, scope com
``/.default``, reuso de token em cache, renovação por margem de segurança,
invalidação e falha fechada quando a identidade não está provisionada.

Nenhuma chamada real de rede e nenhum segredo real: o provedor de segredo é
injetado e os tokens são JWTs sintéticos.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import jwt
import pytest
import requests

from src.adapters.gateway.service_auth import AzureAdServiceAuthAdapter
from src.domain.errors import (
    CapabilityUnavailableError,
    ContractViolationError,
    DependencyUnavailableError,
)
from src.settings import load_settings

_IDENTITY_ENV = {
    "ENV": "test",
    "APP_ROOT_PATH": "/x",
    "TENANTID": "tenant-sintetico",
    "GATEWAYAPPID": "client-sintetico",
    "GATEWAYAPPIDURI": "api://recurso-sintetico",
}


# ─── Fakes ────────────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status_code=200, json_body=None, *, raise_json=False):
        self.status_code = status_code
        self._json_body = json_body
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("no json")
        return self._json_body


class FakeSession:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, "kwargs": kwargs})
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _jwt_expiring_in(seconds: float) -> str:
    """JWT sintético cujo único conteúdo relevante é o ``exp``."""
    exp = time.time() + seconds
    return jwt.encode({"exp": int(exp)}, "chave-sintetica", algorithm="HS256")


def _adapter(outcomes, *, env=None, secret="segredo-sintetico"):
    settings = load_settings(env or _IDENTITY_ENV)
    session = FakeSession(outcomes)
    adapter = AzureAdServiceAuthAdapter(
        settings,
        session=session,
        secret_provider=lambda: secret,
    )
    return adapter, session


# ─── Contrato da requisição de token ─────────────────────────────────────────

def test_token_request_uses_client_credentials_contract():
    token = _jwt_expiring_in(3600)
    adapter, session = _adapter([FakeResponse(200, {"access_token": token})])

    adapter.get_service_token()

    call = session.calls[0]
    assert call["url"] == (
        "https://login.microsoftonline.com/tenant-sintetico/oauth2/v2.0/token"
    )
    form = call["kwargs"]["data"]
    assert form["grant_type"] == "client_credentials"
    assert form["client_id"] == "client-sintetico"
    assert form["client_secret"] == "segredo-sintetico"
    # O scope recebe o sufixo /.default, como no contrato real.
    assert form["scope"] == "api://recurso-sintetico/.default"


def test_token_request_uses_connect_and_read_timeout_pair():
    adapter, session = _adapter(
        [FakeResponse(200, {"access_token": _jwt_expiring_in(3600)})]
    )
    adapter.get_service_token()
    assert session.calls[0]["kwargs"]["timeout"] == (5.0, 30.0)


def test_token_is_sanitized_against_header_injection():
    # Um token com CRLF não pode virar cabeçalho: as quebras são removidas.
    adapter, _ = _adapter(
        [FakeResponse(200, {"access_token": "abc\r\nX-Injetado: mal", "expires_in": 600})]
    )
    token = adapter.get_service_token()
    assert "\r" not in token.value
    assert "\n" not in token.value


# ─── Cache, renovação e invalidação ──────────────────────────────────────────

def test_valid_token_is_reused_without_new_request():
    adapter, session = _adapter(
        [FakeResponse(200, {"access_token": _jwt_expiring_in(3600)})]
    )
    first = adapter.get_service_token()
    second = adapter.get_service_token()
    assert first.value == second.value
    assert len(session.calls) == 1


def test_token_within_safety_margin_is_renewed():
    # Expira em 30s, dentro da margem de 60s: precisa reautenticar.
    near = _jwt_expiring_in(30)
    fresh = _jwt_expiring_in(3600)
    adapter, session = _adapter(
        [
            FakeResponse(200, {"access_token": near}),
            FakeResponse(200, {"access_token": fresh}),
        ]
    )
    adapter.get_service_token()
    renewed = adapter.get_service_token()
    assert len(session.calls) == 2
    assert renewed.value == fresh


def test_invalidate_forces_new_authentication():
    adapter, session = _adapter(
        [
            FakeResponse(200, {"access_token": _jwt_expiring_in(3600)}),
            FakeResponse(200, {"access_token": _jwt_expiring_in(3600)}),
        ]
    )
    adapter.get_service_token()
    adapter.invalidate()
    adapter.get_service_token()
    assert len(session.calls) == 2


# ─── Falha fechada e classificação de erro ───────────────────────────────────

def test_missing_identity_fails_closed():
    # Sem TENANTID/APPID/APPIDURI: capacidade indisponível, sem I/O.
    adapter, session = _adapter([], env={"ENV": "test", "APP_ROOT_PATH": "/x"})
    with pytest.raises(CapabilityUnavailableError):
        adapter.get_service_token()
    assert session.calls == []


def test_missing_secret_fails_closed_without_request():
    adapter, session = _adapter([], secret=None)
    with pytest.raises(CapabilityUnavailableError):
        adapter.get_service_token()
    assert session.calls == []


def test_response_without_access_token_is_contract_violation():
    # HTTP 200 sem credencial utilizável nunca é sucesso.
    adapter, _ = _adapter([FakeResponse(200, {})])
    with pytest.raises(ContractViolationError):
        adapter.get_service_token()


def test_non_json_response_is_contract_violation():
    adapter, _ = _adapter([FakeResponse(200, raise_json=True)])
    with pytest.raises(ContractViolationError):
        adapter.get_service_token()


def test_timeout_is_retryable_dependency_error():
    adapter, _ = _adapter([requests.Timeout("slow")])
    with pytest.raises(DependencyUnavailableError) as exc:
        adapter.get_service_token()
    assert exc.value.retryable is True


def test_tls_error_from_issuer_is_not_retryable():
    adapter, _ = _adapter([requests.exceptions.SSLError("cadeia invalida")])
    with pytest.raises(DependencyUnavailableError) as exc:
        adapter.get_service_token()
    assert exc.value.retryable is False


def test_ca_bundle_inexistente_no_token_vira_erro_de_dependencia():
    adapter, _ = _adapter([OSError("invalid path: ca-bundle-local.pem")])
    with pytest.raises(DependencyUnavailableError) as exc:
        adapter.get_service_token()
    assert exc.value.retryable is False


def test_token_request_uses_tls_policy():
    token = _jwt_expiring_in(3600)
    env = dict(_IDENTITY_ENV, TLS_CA_BUNDLE_PATH="/certs/ca.pem")
    adapter, session = _adapter([FakeResponse(200, {"access_token": token})], env=env)
    adapter.get_service_token()
    assert session.calls[0]["kwargs"]["verify"] == "/certs/ca.pem"


def test_client_error_from_issuer_is_not_retryable():
    adapter, _ = _adapter([FakeResponse(401, {})])
    with pytest.raises(DependencyUnavailableError) as exc:
        adapter.get_service_token()
    assert exc.value.retryable is False


def test_throttling_from_issuer_is_retryable():
    adapter, _ = _adapter([FakeResponse(429, {})])
    with pytest.raises(DependencyUnavailableError) as exc:
        adapter.get_service_token()
    assert exc.value.retryable is True


def test_error_message_never_carries_issuer_body():
    adapter, _ = _adapter([FakeResponse(400, {"error": "segredo-vazado"})])
    with pytest.raises(DependencyUnavailableError) as exc:
        adapter.get_service_token()
    assert "segredo-vazado" not in str(exc.value)


# ─── Expiração ───────────────────────────────────────────────────────────────

def test_opaque_token_falls_back_to_expires_in():
    # Token que não é JWT legível: usa expires_in em vez de falhar.
    adapter, _ = _adapter(
        [FakeResponse(200, {"access_token": "token-opaco", "expires_in": 3600})]
    )
    token = adapter.get_service_token()
    assert token.is_valid_at(datetime.now(timezone.utc), safety_margin_seconds=60)


def test_token_without_any_expiry_is_contract_violation():
    adapter, _ = _adapter([FakeResponse(200, {"access_token": "token-opaco"})])
    with pytest.raises(ContractViolationError):
        adapter.get_service_token()


def test_token_repr_never_exposes_value():
    adapter, _ = _adapter(
        [FakeResponse(200, {"access_token": "valor-secreto", "expires_in": 600})]
    )
    token = adapter.get_service_token()
    assert "valor-secreto" not in repr(token)


# ─── Diagnóstico da recusa pelo emissor ──────────────────────────────────────

def _registros(caplog, evento):
    return [
        r.structured for r in caplog.records
        if getattr(r, "structured", {}).get("event") == evento
    ]


def test_recusa_do_emissor_registra_status_e_codigos_aadsts(caplog):
    corpo = {
        "error": "invalid_client",
        "error_description": "AADSTS7000215: Invalid client secret provided. Trace ID: x",
        "error_codes": [7000215],
    }
    adapter, _ = _adapter([FakeResponse(401, corpo)])
    with caplog.at_level(logging.INFO), pytest.raises(DependencyUnavailableError):
        adapter.get_service_token()
    registro = _registros(caplog, "service_token_rejected")[0]
    assert registro["status_code"] == 401
    assert registro["error_codes"] == [7000215]
    assert "Invalid client secret" not in str(registro)


@pytest.mark.parametrize(
    "resposta, codigos",
    [
        (FakeResponse(400, raise_json=True), []),
        (FakeResponse(400, ["nao-objeto"]), []),
        # Só números entram no log: texto ali pode ser qualquer coisa.
        (FakeResponse(400, {"error_codes": ["texto", 90002, True]}), [90002]),
    ],
)
def test_recusa_do_emissor_registra_so_codigos_numericos(caplog, resposta, codigos):
    adapter, _ = _adapter([resposta])
    with caplog.at_level(logging.INFO), pytest.raises(DependencyUnavailableError):
        adapter.get_service_token()
    registro = _registros(caplog, "service_token_rejected")[0]
    assert registro["status_code"] == 400
    assert registro["error_codes"] == codigos
