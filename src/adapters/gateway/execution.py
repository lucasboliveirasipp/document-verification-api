"""Execução de workflows da Gateway: início, acompanhamento e leitura do texto.

Contrato do projeto publicado ``api-agente-orientador-curadoria``:

* início: ``POST {APIM_WORKFLOW_URL}/request/start-workflow``;
* status: ``GET {APIM_STATUS_URL}/response/workflow-execution-status/{id}``.

O mesmo GET traz o status e, na conclusão, o resultado: uma chamada só.
Qualquer status que não seja sucesso ou falha conhecida é tratado como "ainda
processando" até o timeout, como na Curadoria; nunca como sucesso.

Um 404 logo após o start significa que a execução ainda não foi registrada
(doc da Gateway): é tolerado durante ``not_found_grace_seconds`` e, depois
disso, propagado, para que uma URL de status errada não espere o timeout.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Iterator, Mapping, Optional
from urllib.parse import quote

from src.domain.errors import (
    CapabilityUnavailableError,
    ContractViolationError,
    DependencyUnavailableError,
    ResourceNotFoundError,
)
from src.observability.logging import get_logger
from src.settings import AppSettings, PollingSettings

_logger = get_logger("gateway.execution")

START_PATH = "/request/start-workflow"
STATUS_PATH = "/response/workflow-execution-status/"

STATUS_SUCESSO = "WF_COMPLETED_SUCCESS"
STATUS_FALHA = frozenset({"WF_COMPLETED_ERROR", "ERROR", "FAILED"})

#: A Gateway devolve "UNKNOWN" como id em alguns cenários de falha.
_INVALID_EXECUTION_ID = "UNKNOWN"
_EXECUTION_ID_KEYS = ("workflow_execution_id", "execution_id", "id")


def start_workflow(settings: AppSettings, transport: Any, payload: Mapping[str, Any]) -> str:
    """Inicia um workflow e devolve o id de execução.

    O início não é idempotente: o transporte é chamado sem retry, para não
    duplicar execução e custo de IA.
    """
    base = settings.gateway_endpoints.workflow_url
    if not base:
        raise CapabilityUnavailableError("Inicio de workflow nao configurado neste ambiente.")
    response = transport.post_json(
        base.rstrip("/") + START_PATH, json_body=payload, idempotent=False
    )
    return extract_execution_id(response.payload)


def wait_for_result(
    settings: AppSettings,
    transport: Any,
    execution_id: str,
    *,
    polling: Optional[PollingSettings] = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Mapping[str, Any]:
    """Consulta o status até sucesso, falha ou timeout; devolve o corpo final.

    ``polling`` substitui intervalo e timeout padrão (o OCR usa os seus).
    """
    base = settings.gateway_endpoints.status_url
    if not base:
        raise CapabilityUnavailableError("Consulta de execucao nao configurada neste ambiente.")
    url = base.rstrip("/") + STATUS_PATH + quote(execution_id, safe="")
    polling = polling or settings.polling

    carencia_404 = min(polling.not_found_grace_seconds, polling.timeout_seconds)
    inicio = clock()
    while True:
        sleep(polling.interval_seconds)
        try:
            payload = transport.get_json(url, required_field="status").payload
        except ResourceNotFoundError:
            if clock() - inicio > carencia_404:
                raise
            _logger.info("gateway_workflow_not_registered_yet")
            continue
        status = str(payload["status"]).upper()
        if status == STATUS_SUCESSO:
            return payload
        if status in STATUS_FALHA:
            _logger.warning("gateway_workflow_failed", status=status)
            raise DependencyUnavailableError(
                "Workflow da Gateway terminou com erro.", retryable=False
            )
        if clock() - inicio > polling.timeout_seconds:
            _logger.warning("gateway_workflow_timeout", status=status)
            raise DependencyUnavailableError(
                "Tempo esgotado aguardando o workflow da Gateway.", retryable=True
            )


def extract_execution_id(payload: Mapping[str, Any]) -> str:
    """Lê o id de execução, rejeitando ausência e o sentinela ``UNKNOWN``."""
    for key in _EXECUTION_ID_KEYS:
        value = payload.get(key)
        if value and str(value) != _INVALID_EXECUTION_ID:
            return str(value)
    raise ContractViolationError("Inicio de workflow sem identificador de execucao utilizavel.")


def output_text(payload: Mapping[str, Any]) -> Optional[str]:
    """Texto do resultado, nos formatos observados; ``None`` se não houver.

    Ordem: ``output_text`` direto; ``result.output_text``; e, no envelope
    ``output_collection``, a folha ``output_text`` (OCR) ou
    ``choices[].message.content`` (Gerador de Texto).
    """
    direct = _texto(payload.get("output_text"))
    if direct is not None:
        return direct
    result = payload.get("result")
    if isinstance(result, Mapping):
        nested = _texto(result.get("output_text"))
        if nested is not None:
            return nested
    for json_data in _json_data_nodes(payload):
        leaf = _leaf_text(json_data)
        if leaf is not None:
            return leaf
    return None


def _texto(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value.strip() else None


def _json_data_nodes(payload: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    output_collection = payload.get("output_collection")
    if not isinstance(output_collection, Mapping):
        return
    output_datas = output_collection.get("output_datas")
    if not isinstance(output_datas, list):
        return
    for output_data in output_datas:
        if not isinstance(output_data, Mapping):
            continue
        step_output = output_data.get("workflow_step_output_collection")
        output = step_output.get("output") if isinstance(step_output, Mapping) else None
        json_data = output.get("json_data") if isinstance(output, Mapping) else None
        if isinstance(json_data, Mapping):
            yield json_data


def _leaf_text(json_data: Mapping[str, Any]) -> Optional[str]:
    text = _texto(json_data.get("output_text"))
    if text is not None:
        return text
    choices = json_data.get("choices")
    if not isinstance(choices, list):
        return None
    for choice in choices:
        message = choice.get("message") if isinstance(choice, Mapping) else None
        if isinstance(message, Mapping):
            content = _texto(message.get("content"))
            if content is not None:
                return content
    return None
