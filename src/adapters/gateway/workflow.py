"""Workflow de OCR da Gateway (caminho B do OCR, ainda não homologado).

Monta o envelope ``start-workflow`` observado no projeto de Ouvidoria e o envia
por :func:`~src.adapters.gateway.execution.start_workflow` para
``{APIM_WORKFLOW_URL}/request/start-workflow``. O **código do workflow** e o
container são configuração e ficam ``PENDENTE`` até a homologação, com falha
fechada quando ausentes.

Contrato observado (§3 e §4.1 do manual):

* envelope idêntico para qualquer workflow — muda o
  ``workflow_configuration_code`` e a lista de ``workflow_step_properties``;
* ``workflow_step_order`` é ``0``;
* para o step de OCR, os ``full_path`` são ``files_id``, ``files_path``,
  ``container`` e ``input_text`` (vazio nesta etapa). As listas ``files_id`` e
  ``files_path`` seguem **a mesma ordem**.

O ``step_workflow_code`` não é enviado no payload (§3): ele permanece como
contrato/documentação em configuração, não como campo de transporte.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from src.adapters.gateway.execution import start_workflow
from src.adapters.gateway.transport import GatewayTransport
from src.domain.errors import CapabilityUnavailableError, ContractViolationError
from src.domain.models import WorkflowExecutionReference, WorkflowSubmission
from src.settings import AppSettings


class GatewayDocumentWorkflowAdapter:
    """Inicia uma execução do workflow de OCR para um lote de documentos."""

    def __init__(self, settings: AppSettings, transport: GatewayTransport) -> None:
        self._settings = settings
        self._transport = transport

    def start_document_workflow(self, submission: WorkflowSubmission) -> WorkflowExecutionReference:
        """Inicia exatamente uma execução do workflow de OCR para o lote."""
        workflow_code = self._settings.gateway_resources.workflow_code
        container = self._settings.gateway_resources.container_id
        if not workflow_code or not container:
            raise CapabilityUnavailableError("Workflow de OCR nao configurado neste ambiente.")
        payload = self._build_payload(submission, workflow_code, container)
        execution_id = start_workflow(self._settings, self._transport, payload)
        return WorkflowExecutionReference(
            external_execution_id=execution_id,
            accepted_at=datetime.now(timezone.utc),
        )

    def _build_payload(
        self,
        submission: WorkflowSubmission,
        workflow_code: str,
        container: str,
    ) -> Mapping[str, Any]:
        # Ordem preservada: files_id e files_path indexam o mesmo documento.
        files_id = [str(doc.external_document_id) for doc in submission.documents]
        files_path = [self._require_path(doc) for doc in submission.documents]

        return {
            "workflow_configuration_code": workflow_code,
            "input_collection": {
                "input_properties": [
                    {
                        "workflow_step_order": 0,
                        "workflow_step_properties": [
                            {"full_path": "files_id", "actual_value": files_id},
                            {"full_path": "files_path", "actual_value": files_path},
                            {"full_path": "container", "actual_value": container},
                            {"full_path": "input_text", "actual_value": ""},
                        ],
                    }
                ]
            },
        }

    @staticmethod
    def _require_path(document) -> str:
        # Sem storage_path não há como montar files_path (lacuna 1 do manual).
        if not document.storage_path:
            raise ContractViolationError(
                "Referencia de documento sem caminho de armazenamento para o workflow."
            )
        return str(document.storage_path)
