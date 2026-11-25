"""Testes do cliente do workflow "Gerador de Texto" (payload da Curadoria)."""
from __future__ import annotations

import pytest

from src.adapters.gateway.text_generation import TextGenerationClient
from src.domain.errors import (
    CapabilityUnavailableError,
    ContractViolationError,
    DependencyUnavailableError,
)
from src.settings import load_settings
from tests.unit.gateway_fakes import FakeGatewayTransport, envelope, fake_clock

_ENV = {
    "APP_ENV": "TEST",
    "APIM_WORKFLOW_URL": "https://gateway.test/workflow-request/v1",
    "APIM_STATUS_URL": "https://gateway.test/workflow-response/v1",
    "TEXTGEN_WORKFLOW_CODE": "WF_TEXTO_TESTE",
    "TEXTGEN_MODEL": "deployment-teste",
    "OPENAI_API_ENDPOINT": "https://aoai.test/",
    "OPENAI_API_VERSION": "2024-02-01",
    "OPENAI_API_KEY": "placeholder",
}


def _client(status_payloads, **env_overrides):
    env = dict(_ENV)
    env.update(env_overrides)
    transport = FakeGatewayTransport(status_payloads=status_payloads)
    client = TextGenerationClient(
        load_settings(env), transport, sleep=lambda _: None, clock=fake_clock(0, 1, 2, 3)
    )
    return client, transport


def _resposta(texto):
    return envelope({"choices": [{"message": {"role": "assistant", "content": texto}}]})


def _props(transport):
    body = transport.calls[0][2]
    step = body["input_collection"]["input_properties"][0]
    assert step["workflow_step_order"] == 0
    return body, {p["full_path"]: p["actual_value"] for p in step["workflow_step_properties"]}


def test_gerar_texto_returns_generated_content():
    client, _ = _client([{"status": "WF_RUNNING"}, _resposta('{"ok": true}')])
    assert client.gerar_texto("sistema", "usuario") == '{"ok": true}'


def test_payload_segue_o_template_do_workflow():
    # Template do WF_PARECER (21/09): os 12 campos da Curadoria
    # mais os dois filtros vazios, que o código do João também envia.
    client, transport = _client([_resposta("x")])
    client.gerar_texto(
        "sistema", "usuario", response_format={"type": "json_object"}, max_tokens=123
    )
    body, props = _props(transport)
    assert body["workflow_configuration_code"] == "WF_TEXTO_TESTE"
    assert props == {
        "openai_api_endpoint": "https://aoai.test/",
        "openai_api_version": "2024-02-01",
        "openai_api_key": "placeholder",
        "gen_params.model": "deployment-teste",
        "gen_params.messages": [
            {"role": "system", "content": "sistema"},
            {"role": "user", "content": "usuario"},
        ],
        "gen_params.temperature": 0,
        "gen_params.max_tokens": 123,
        "gen_params.top_p": 0,
        "gen_params.seed": 0,
        "gen_params.response_format": {"type": "json_object"},
        "gen_params.frequency_penalty": 0,
        "gen_params.presence_penalty": 0,
        "base_filter_fields": {},
        "categorization_filter_fields": {},
    }


def test_defaults_are_text_format_and_configured_max_tokens():
    client, transport = _client([_resposta("x")], TEXTGEN_MAX_TOKENS="2048")
    client.gerar_texto("s", "u")
    _, props = _props(transport)
    assert props["gen_params.response_format"] == {"type": "text"}
    assert props["gen_params.max_tokens"] == 2048


def test_user_content_can_be_a_list_of_parts():
    partes = [{"type": "text", "text": "leia"}]
    client, transport = _client([_resposta("x")])
    client.gerar_texto("s", partes)
    _, props = _props(transport)
    assert props["gen_params.messages"][1]["content"] == partes


@pytest.mark.parametrize(
    "missing",
    ["TEXTGEN_WORKFLOW_CODE", "TEXTGEN_MODEL", "OPENAI_API_ENDPOINT", "OPENAI_API_VERSION",
     "OPENAI_API_KEY"],
)
def test_incomplete_configuration_fails_before_any_call(missing):
    client, transport = _client([], **{missing: ""})
    with pytest.raises(CapabilityUnavailableError):
        client.gerar_texto("s", "u")
    assert transport.calls == []


def test_success_without_text_is_contract_violation():
    client, _ = _client([envelope({"choices": []})])
    with pytest.raises(ContractViolationError):
        client.gerar_texto("s", "u")


def test_workflow_error_propagates():
    client, _ = _client([{"status": "WF_COMPLETED_ERROR"}])
    with pytest.raises(DependencyUnavailableError):
        client.gerar_texto("s", "u")
