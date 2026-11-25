"""Testes de início, acompanhamento e leitura de workflows da Gateway."""
from __future__ import annotations

import pytest

from src.adapters.gateway.execution import (
    extract_execution_id,
    output_text,
    start_workflow,
    wait_for_result,
)
from src.domain.errors import (
    CapabilityUnavailableError,
    ContractViolationError,
    DependencyUnavailableError,
    ResourceNotFoundError,
)
from src.settings import PollingSettings, load_settings
from tests.unit.gateway_fakes import FakeGatewayTransport, envelope, fake_clock

_BASE_ENV = {
    "APP_ENV": "TEST",
    "APIM_WORKFLOW_URL": "https://gateway.test/workflow-request/v1/",
    "APIM_STATUS_URL": "https://gateway.test/workflow-response/v1",
    "POLLING_INTERVAL_SECONDS": "2",
    "POLLING_TIMEOUT_SECONDS": "10",
}


def _settings(**overrides):
    env = dict(_BASE_ENV)
    env.update(overrides)
    return load_settings(env)


# ─── start_workflow ──────────────────────────────────────────────────────────

def test_start_workflow_posts_to_curadoria_path_without_retry():
    transport = FakeGatewayTransport({"workflow_execution_id": "exec-7"})
    assert start_workflow(_settings(), transport, {"x": 1}) == "exec-7"
    method, url, body, idempotent = transport.calls[0]
    assert (method, body, idempotent) == ("POST", {"x": 1}, False)
    assert url == "https://gateway.test/workflow-request/v1/request/start-workflow"


def test_start_workflow_without_url_fails_closed():
    with pytest.raises(CapabilityUnavailableError):
        start_workflow(_settings(APIM_WORKFLOW_URL=""), FakeGatewayTransport(), {})


@pytest.mark.parametrize(
    "payload, expected",
    [({"workflow_execution_id": "a"}, "a"), ({"execution_id": "b"}, "b"), ({"id": "c"}, "c")],
)
def test_extract_execution_id_accepts_known_keys(payload, expected):
    assert extract_execution_id(payload) == expected


@pytest.mark.parametrize("payload", [{"workflow_execution_id": "UNKNOWN"}, {}, {"id": ""}])
def test_extract_execution_id_rejects_unusable_values(payload):
    with pytest.raises(ContractViolationError):
        extract_execution_id(payload)


# ─── wait_for_result ─────────────────────────────────────────────────────────

def test_wait_polls_until_success_and_returns_payload():
    final = envelope({"output_text": "ok"})
    transport = FakeGatewayTransport(status_payloads=[{"status": "WF_RUNNING"}, final])
    sleeps = []
    result = wait_for_result(
        _settings(), transport, "exec/1", sleep=sleeps.append, clock=fake_clock(0, 1, 2)
    )
    assert result is final
    assert sleeps == [2.0, 2.0]
    method, url, required_field, _ = transport.calls[0]
    assert method == "GET" and required_field == "status"
    assert url == (
        "https://gateway.test/workflow-response/v1"
        "/response/workflow-execution-status/exec%2F1"
    )


def test_wait_accepts_lowercase_success():
    transport = FakeGatewayTransport(status_payloads=[{"status": "wf_completed_success"}])
    wait_for_result(_settings(), transport, "e", sleep=lambda _: None, clock=fake_clock(0, 1))


@pytest.mark.parametrize("status", ["WF_COMPLETED_ERROR", "ERROR", "FAILED"])
def test_wait_raises_on_workflow_failure(status):
    transport = FakeGatewayTransport(status_payloads=[{"status": status}])
    with pytest.raises(DependencyUnavailableError) as exc:
        wait_for_result(_settings(), transport, "e", sleep=lambda _: None, clock=fake_clock(0, 1))
    assert exc.value.retryable is False


def test_wait_times_out_with_retryable_error():
    transport = FakeGatewayTransport(status_payloads=[{"status": "WF_RUNNING"}] * 2)
    with pytest.raises(DependencyUnavailableError) as exc:
        wait_for_result(
            _settings(), transport, "e", sleep=lambda _: None, clock=fake_clock(0, 5, 11)
        )
    assert exc.value.retryable is True


def test_wait_aceita_polling_proprio():
    # O OCR usa intervalo e timeout próprios, sem mexer nos do Gerador de Texto.
    transport = FakeGatewayTransport(status_payloads=[{"status": "WF_RUNNING"}])
    sleeps = []
    with pytest.raises(DependencyUnavailableError) as exc:
        wait_for_result(
            _settings(), transport, "e",
            polling=PollingSettings(interval_seconds=5.0, timeout_seconds=7.0),
            sleep=sleeps.append, clock=fake_clock(0, 8),
        )
    assert exc.value.retryable is True
    assert sleeps == [5.0]


def _nao_registrada():
    return ResourceNotFoundError("Recurso nao encontrado na Gateway (HTTP 404).")


def test_wait_tolera_404_logo_apos_o_start():
    # A execução pode ainda não estar registrada no primeiro GET de status.
    final = {"status": "WF_COMPLETED_SUCCESS"}
    transport = FakeGatewayTransport(status_payloads=[_nao_registrada(), final])
    result = wait_for_result(
        _settings(POLLING_NOT_FOUND_GRACE_SECONDS="5"), transport, "e",
        sleep=lambda _: None, clock=fake_clock(0, 2),
    )
    assert result is final


def test_wait_desiste_do_404_depois_da_carencia():
    # URL de status errada não pode esperar o timeout inteiro.
    transport = FakeGatewayTransport(status_payloads=[_nao_registrada(), _nao_registrada()])
    with pytest.raises(ResourceNotFoundError):
        wait_for_result(
            _settings(POLLING_NOT_FOUND_GRACE_SECONDS="5"), transport, "e",
            sleep=lambda _: None, clock=fake_clock(0, 3, 6),
        )


def test_wait_carencia_do_404_nao_passa_do_timeout():
    transport = FakeGatewayTransport(status_payloads=[_nao_registrada()])
    with pytest.raises(ResourceNotFoundError):
        wait_for_result(
            _settings(POLLING_NOT_FOUND_GRACE_SECONDS="60"), transport, "e",
            sleep=lambda _: None, clock=fake_clock(0, 11),
        )


def test_wait_without_status_url_fails_closed():
    with pytest.raises(CapabilityUnavailableError):
        wait_for_result(
            _settings(APIM_STATUS_URL=""), FakeGatewayTransport(), "e",
            sleep=lambda _: None, clock=fake_clock(0),
        )


# ─── output_text ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "payload",
    [
        {"output_text": "texto"},
        {"result": {"output_text": "texto"}},
        envelope({"output_text": "texto"}),
        envelope({"choices": [{"message": {"role": "assistant", "content": "texto"}}]}),
        envelope({"choices": ["x", {"message": {"content": 1}},
                              {"message": {"role": "assistant", "content": "texto"}}]}),
    ],
)
def test_output_text_accepts_known_formats(payload):
    assert output_text(payload) == "texto"


@pytest.mark.parametrize(
    "payload",
    [
        {"unrelated": 1},
        {"output_text": "   "},
        {"result": {"output_text": ""}},
        envelope({"choices": []}),
        envelope({"algo": "x"}),
        {"output_collection": {"output_datas": ["x", {"workflow_step_output_collection": 1}]}},
        {"output_collection": {"output_datas": "nao-lista"}},
        {"output_collection": "nao-objeto"},
    ],
)
def test_output_text_returns_none_without_usable_text(payload):
    assert output_text(payload) is None
