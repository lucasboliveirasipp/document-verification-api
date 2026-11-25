"""Workflow "Gerador de Texto" da Gateway (contrato da Curadoria em produção).

Mesmo payload do ``api-agente-orientador-curadoria``: campos
``openai_api_*`` exigidos pelo workflow e ``gen_params.*`` repassados ao modelo,
inclusive ``response_format`` (a Curadoria usa ``{"type": "json_object"}`` em
produção para a avaliação estruturada). Os filtros ``base_filter_fields`` e
``categorization_filter_fields`` vão vazios, como no template do nosso workflow
(``WF_PARECER``) e na implementação de referência.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Union

from src.adapters.gateway.execution import output_text, start_workflow, wait_for_result
from src.domain.errors import CapabilityUnavailableError, ContractViolationError
from src.settings import AppSettings

#: Conteúdo de mensagem: texto ou lista de partes (texto/imagem) no formato de
#: chat completions.
MessageContent = Union[str, List[Dict[str, Any]]]


class TextGenerationClient:
    """Gera texto com um start-workflow e o polling do resultado."""

    def __init__(
        self,
        settings: AppSettings,
        transport: Any,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._sleep = sleep
        self._clock = clock

    def gerar_texto(
        self,
        system_prompt: str,
        user_content: MessageContent,
        *,
        response_format: Optional[Mapping[str, Any]] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Devolve o texto gerado pelo modelo.

        Raises:
            CapabilityUnavailableError: workflow/modelo não configurados.
            DependencyUnavailableError: Gateway indisponível, erro ou timeout.
            ContractViolationError: execução concluída sem texto.
        """
        config = self._settings.text_generation
        if not config.is_complete:
            raise CapabilityUnavailableError("Gerador de Texto nao configurado neste ambiente.")
        payload = self._build_payload(
            system_prompt,
            user_content,
            dict(response_format or {"type": "text"}),
            max_tokens or config.max_tokens,
        )
        execution_id = start_workflow(self._settings, self._transport, payload)
        result = wait_for_result(
            self._settings, self._transport, execution_id, sleep=self._sleep, clock=self._clock
        )
        texto = output_text(result)
        if texto is None:
            raise ContractViolationError("Workflow concluido sem texto gerado.")
        return texto

    def _build_payload(
        self,
        system_prompt: str,
        user_content: MessageContent,
        response_format: Dict[str, Any],
        max_tokens: int,
    ) -> Dict[str, Any]:
        config = self._settings.text_generation
        propriedades = [
            ("openai_api_endpoint", config.openai_api_endpoint),
            ("openai_api_version", config.openai_api_version),
            ("openai_api_key", config.openai_api_key),
            ("gen_params.model", config.model),
            (
                "gen_params.messages",
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            ),
            ("gen_params.temperature", 0),
            ("gen_params.max_tokens", max_tokens),
            ("gen_params.top_p", 0),
            ("gen_params.seed", 0),
            ("gen_params.response_format", response_format),
            ("gen_params.frequency_penalty", 0),
            ("gen_params.presence_penalty", 0),
            ("base_filter_fields", {}),
            ("categorization_filter_fields", {}),
        ]
        return {
            "workflow_configuration_code": config.workflow_code,
            "input_collection": {
                "input_properties": [
                    {
                        "workflow_step_order": 0,
                        "workflow_step_properties": [
                            {"full_path": path, "actual_value": value}
                            for path, value in propriedades
                        ],
                    }
                ]
            },
        }
