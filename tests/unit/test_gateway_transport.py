"""Testes do GatewayTransport com sessão fake (sem rede)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest
import requests

from src.adapters.gateway.transport import GatewayTransport
from src.domain.errors import (
    ContractViolationError,
    DependencyUnavailableError,
    ResourceNotFoundError,
)
from src.domain.models import ServiceAccessToken
from src.settings import load_settings


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, *, raise_json=False, headers=None):
        self.status_code = status_code
        self._json_body = json_body
        self._raise_json = raise_json
        self.headers = headers or {}

    def json(self):
        if self._raise_json:
            raise ValueError("no json")
        return self._json_body


class FakeSession:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, "kwargs": kwargs})
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeAuth:
    def __init__(self, value="token-sintetico"):
        self.value = value
        self.invalidated = 0

    def get_service_token(self):
        return ServiceAccessToken(
            value=self.value, expires_at=datetime.now(timezone.utc) + timedelta(hours=1)
        )

    def invalidate(self):
        self.invalidated += 1


def _settings(**overrides):
    env = {
        "ENV": "test",
        "APP_ROOT_PATH": "/x",
        "HTTP_RETRY_MAX_ATTEMPTS": "3",
        "HTTP_RETRY_BACKOFF_BASE_SECONDS": "0",
        "HTTP_RETRY_BACKOFF_MAX_SECONDS": "0",
    }
    env.update(overrides)
    return load_settings(env)


def _transport(outcomes, *, settings=None, auth=None, sleeps=None):
    session = FakeSession(outcomes)
    sleep = sleeps.append if sleeps is not None else (lambda _: None)
    transport = GatewayTransport(
        settings or _settings(), session=session, service_auth=auth, sleep=sleep
    )
    return transport, session


# ─── Sucesso e montagem ──────────────────────────────────────────────────────

def test_get_json_returns_payload_on_200():
    transport, _ = _transport([FakeResponse(200, {"status": "OK"})])
    assert transport.get_json("https://x/y", required_field="status").payload["status"] == "OK"


def test_connection_mode_is_absent_by_default():
    transport, session = _transport([FakeResponse(200, {"ok": 1})])
    transport.get_json("https://x/y")
    assert session.calls[0]["kwargs"]["params"] is None


def test_connection_mode_is_sent_when_configured():
    transport, session = _transport(
        [FakeResponse(200, {"ok": 1})], settings=_settings(GATEWAY_CONNECTION_MODE="APIM")
    )
    transport.get_json("https://x/y")
    assert session.calls[0]["kwargs"]["params"] == {"connection_mode": "APIM"}


def test_timeout_and_tls_are_applied():
    transport, session = _transport([FakeResponse(200, {"ok": 1})])
    transport.get_json("https://x/y")
    kwargs = session.calls[0]["kwargs"]
    assert kwargs["timeout"] == (5.0, 30.0)
    assert kwargs["verify"] is True


def test_bearer_header_uses_sanitized_service_token():
    transport, session = _transport(
        [FakeResponse(200, {"ok": 1})], auth=FakeAuth("abc\r\nX-Injetado: 1")
    )
    transport.get_json("https://x/y")
    assert session.calls[0]["kwargs"]["headers"]["Authorization"] == "Bearer abcX-Injetado: 1"


def test_no_authorization_header_without_service_auth():
    transport, session = _transport([FakeResponse(200, {"ok": 1})])
    transport.get_json("https://x/y")
    assert "Authorization" not in session.calls[0]["kwargs"]["headers"]


# ─── Guarda de sucesso aparente ──────────────────────────────────────────────

def test_http_200_with_null_body_is_contract_violation():
    transport, _ = _transport([FakeResponse(200, None)])
    with pytest.raises(ContractViolationError):
        transport.get_json("https://x/y")


def test_http_200_with_non_json_is_contract_violation():
    transport, _ = _transport([FakeResponse(200, raise_json=True)])
    with pytest.raises(ContractViolationError):
        transport.get_json("https://x/y")


def test_http_200_missing_required_field_is_contract_violation():
    transport, _ = _transport([FakeResponse(200, {"other": 1})])
    with pytest.raises(ContractViolationError):
        transport.get_json("https://x/y", required_field="status")


# ─── Classificação de status ─────────────────────────────────────────────────

@pytest.mark.parametrize("code", [400, 404, 409, 422])
def test_gateway_4xx_is_contract_violation_not_client_error(code):
    transport, session = _transport([FakeResponse(code, {"detail": "corpo-da-gateway"})])
    with pytest.raises(ContractViolationError) as exc:
        transport.get_json("https://x/y")
    assert exc.value.http_status == 502
    assert "corpo-da-gateway" not in exc.value.message
    assert len(session.calls) == 1


def test_404_is_resource_not_found():
    # O polling precisa distinguir "execução ainda não registrada".
    transport, session = _transport([FakeResponse(404, {})])
    with pytest.raises(ResourceNotFoundError) as exc:
        transport.get_json("https://x/y")
    assert exc.value.http_status == 502
    assert len(session.calls) == 1


def test_401_invalidates_token_and_does_not_retry():
    auth = FakeAuth()
    transport, session = _transport([FakeResponse(401, {})], auth=auth)
    with pytest.raises(DependencyUnavailableError) as exc:
        transport.get_json("https://x/y")
    assert exc.value.retryable is False
    assert auth.invalidated == 1
    assert len(session.calls) == 1


def _registros(caplog, evento):
    return [
        r.structured for r in caplog.records
        if getattr(r, "structured", {}).get("event") == evento
    ]


@pytest.mark.parametrize("code", [401, 403])
def test_recusa_de_credencial_registra_o_motivo_do_apim(caplog, code):
    # Sem o "detail" o "Missing claims: roles" fica invisível no primeiro teste.
    corpo = {
        "message": "Ocorreu um erro na validação do token.",
        "httpcode": f"{code} - Unauthorized",
        "detail": "Missing claims: roles.",
    }
    transport, _ = _transport([FakeResponse(code, corpo)], auth=FakeAuth())
    with caplog.at_level(logging.INFO), pytest.raises(DependencyUnavailableError):
        transport.get_json("https://x/y")
    registro = _registros(caplog, "gateway_http_error")[0]
    assert registro["status_code"] == code
    assert registro["detail"] == "Missing claims: roles."


@pytest.mark.parametrize(
    "corpo",
    [{"detail": [{"input": "CPF do cliente"}]}, {"detail": "CPF do cliente"}],
)
def test_422_nao_registra_o_corpo(caplog, corpo):
    # Um 422 pode repetir a entrada, que carrega o texto dos documentos.
    transport, _ = _transport([FakeResponse(422, corpo)])
    with caplog.at_level(logging.INFO), pytest.raises(ContractViolationError):
        transport.get_json("https://x/y")
    registro = _registros(caplog, "gateway_http_error")[0]
    assert "detail" not in registro
    assert "CPF do cliente" not in str(registro)


@pytest.mark.parametrize(
    "code, corpo, esperado",
    [
        (406, {"detail": "Extensao nao permitida"}, "Extensao nao permitida"),
        (400, {"message": "Arquivo invalido"}, "Arquivo invalido"),
        (409, {"detail": "Ja existe", "message": "Conflito"}, "Ja existe"),
    ],
)
def test_recusa_da_gateway_registra_o_motivo(caplog, code, corpo, esperado):
    # Sem o motivo, um 406 no upload não diz o que a Gateway recusou.
    transport, _ = _transport([FakeResponse(code, corpo)])
    with caplog.at_level(logging.INFO), pytest.raises(ContractViolationError):
        transport.get_json("https://x/y")
    assert _registros(caplog, "gateway_http_error")[0]["detail"] == esperado


def test_recusa_de_credencial_sem_corpo_json_nao_quebra(caplog):
    transport, _ = _transport([FakeResponse(401, raise_json=True)], auth=FakeAuth())
    with caplog.at_level(logging.INFO), pytest.raises(DependencyUnavailableError):
        transport.get_json("https://x/y")
    assert "detail" not in _registros(caplog, "gateway_http_error")[0]


def test_ssl_error_is_not_retried():
    transport, session = _transport([requests.exceptions.SSLError("cadeia invalida")])
    with pytest.raises(DependencyUnavailableError) as exc:
        transport.get_json("https://x/y")
    assert exc.value.retryable is False
    assert len(session.calls) == 1


def test_ca_bundle_inexistente_vira_erro_de_dependencia():
    # O requests levanta OSError puro; antes virava 500 com traceback.
    erro = OSError("Could not find a suitable TLS CA certificate bundle, invalid path: x.pem")
    transport, session = _transport([erro])
    with pytest.raises(DependencyUnavailableError) as exc:
        transport.get_json("https://x/y")
    assert exc.value.retryable is False
    assert len(session.calls) == 1


def test_other_request_errors_are_not_retried():
    transport, session = _transport([requests.exceptions.InvalidURL("url")])
    with pytest.raises(DependencyUnavailableError) as exc:
        transport.get_json("https://x/y")
    assert exc.value.retryable is False
    assert len(session.calls) == 1


# ─── Retry seguro ────────────────────────────────────────────────────────────

def test_get_retries_on_503_then_succeeds():
    transport, session = _transport([FakeResponse(503, {}), FakeResponse(200, {"ok": 1})])
    assert transport.get_json("https://x/y").payload["ok"] == 1
    assert len(session.calls) == 2


def test_get_retries_on_timeout_until_exhausted():
    transport, session = _transport(
        [requests.Timeout(), requests.Timeout(), requests.Timeout()]
    )
    with pytest.raises(DependencyUnavailableError) as exc:
        transport.get_json("https://x/y")
    assert exc.value.retryable is True
    assert len(session.calls) == 3


def test_non_idempotent_post_does_not_retry():
    transport, session = _transport([FakeResponse(503, {})])
    with pytest.raises(DependencyUnavailableError):
        transport.post_json("https://x/y", json_body={"a": 1}, idempotent=False)
    assert len(session.calls) == 1


def test_idempotent_post_retries():
    transport, session = _transport([FakeResponse(503, {}), FakeResponse(200, {"ok": 1})])
    result = transport.post_json("https://x/y", json_body={"a": 1}, idempotent=True)
    assert result.payload["ok"] == 1
    assert len(session.calls) == 2


def test_multipart_upload_retries_on_500():
    transport, session = _transport(
        [FakeResponse(500, {}), FakeResponse(200, {"id": "1"})]
    )
    result = transport.post_multipart("https://x/y", files={"file": ("n", b"x", "text/plain")})
    assert result.payload["id"] == "1"
    assert len(session.calls) == 2


def test_multipart_envia_campos_de_formulario():
    transport, session = _transport([FakeResponse(200, {"id": "1"})])
    transport.post_multipart(
        "https://x/files/upload",
        files={"file": ("n", b"x", "application/pdf")},
        data={"container_name": "c"},
    )
    assert session.calls[0]["kwargs"]["data"] == {"container_name": "c"}


@pytest.mark.parametrize("resposta", [FakeResponse(204, raise_json=True), FakeResponse(200, {})])
def test_delete_aceita_resposta_sem_corpo(resposta):
    transport, session = _transport([resposta])
    transport.delete("https://x/files/doc-1")
    assert session.calls[0]["method"] == "DELETE"
    assert session.calls[0]["url"] == "https://x/files/doc-1"


def test_delete_e_idempotente_e_repete_em_503():
    transport, session = _transport([FakeResponse(503, {}), FakeResponse(204, raise_json=True)])
    transport.delete("https://x/files/doc-1")
    assert len(session.calls) == 2


def test_delete_de_arquivo_inexistente_e_resource_not_found():
    transport, _ = _transport([FakeResponse(404, {})])
    with pytest.raises(ResourceNotFoundError):
        transport.delete("https://x/files/doc-1")


def test_retry_after_is_exposed_when_attempts_run_out():
    transport, _ = _transport(
        [FakeResponse(429, {}, headers={"Retry-After": "2"})],
        settings=_settings(HTTP_RETRY_MAX_ATTEMPTS="1"),
    )
    with pytest.raises(DependencyUnavailableError) as exc:
        transport.get_json("https://x/y")
    assert exc.value.retry_after_seconds == 2.0


@pytest.mark.parametrize("header", ["abc", "-1"])
def test_invalid_retry_after_is_ignored(header):
    transport, _ = _transport(
        [FakeResponse(429, {}, headers={"Retry-After": header})],
        settings=_settings(HTTP_RETRY_MAX_ATTEMPTS="1"),
    )
    with pytest.raises(DependencyUnavailableError) as exc:
        transport.get_json("https://x/y")
    assert exc.value.retry_after_seconds is None


def test_retry_after_is_respected_in_backoff():
    sleeps = []
    transport, _ = _transport(
        [FakeResponse(429, {}, headers={"Retry-After": "2"}), FakeResponse(200, {"ok": 1})],
        settings=_settings(HTTP_RETRY_BACKOFF_MAX_SECONDS="8"),
        sleeps=sleeps,
    )
    transport.get_json("https://x/y")
    assert sleeps == [2.0]
