"""Fakes compartilhados pelos testes de execução e do Gerador de Texto."""
from __future__ import annotations

from src.adapters.gateway.transport import GatewayResponse


class FakeGatewayTransport:
    """Devolve ``start_payload`` no POST e ``status_payloads`` em ordem nos GETs.

    Um item de ``status_payloads`` que seja exceção é levantado no GET.
    """

    def __init__(self, start_payload=None, status_payloads=()):
        self.start_payload = start_payload or {"workflow_execution_id": "exec-1"}
        self.status_payloads = list(status_payloads)
        self.calls = []

    def post_json(self, url, *, json_body, required_field=None, idempotent=False):
        self.calls.append(("POST", url, json_body, idempotent))
        return GatewayResponse(status_code=200, payload=self.start_payload)

    def get_json(self, url, *, required_field=None):
        self.calls.append(("GET", url, required_field, None))
        item = self.status_payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        return GatewayResponse(status_code=200, payload=item)


def envelope(json_data, status="WF_COMPLETED_SUCCESS"):
    """Resposta de status no formato do envelope de workflow."""
    return {
        "status": status,
        "output_collection": {
            "output_datas": [
                {"workflow_step_output_collection": {"output": {"json_data": json_data}}}
            ]
        },
    }


def fake_clock(*values):
    """Relógio que devolve os valores informados, em ordem."""
    iterator = iter(values)
    return lambda: next(iterator)


class FakeOcrTransport:
    """Transporte do caminho B (upload, start, status e delete).

    ``erros`` mapeia a etapa (``upload``, ``start``, ``delete``) para a exceção
    que ela deve levantar.
    """

    def __init__(self, *, upload=None, status=(), erros=None):
        self.upload_payload = upload or {"id": "doc-1", "path": "/p/doc-1"}
        self.status_payloads = list(status)
        self.erros = erros or {}
        self.start_body = None
        self.upload_file = None
        self.calls = []

    def _falhar(self, etapa):
        if etapa in self.erros:
            raise self.erros[etapa]

    def post_multipart(self, url, *, files, data=None, required_field=None):
        self.calls.append(("upload", url))
        self.upload_file = files["file"]
        self._falhar("upload")
        return GatewayResponse(status_code=200, payload=self.upload_payload)

    def post_json(self, url, *, json_body, required_field=None, idempotent=False):
        self.calls.append(("start", url))
        self.start_body = json_body
        self._falhar("start")
        return GatewayResponse(status_code=200, payload={"workflow_execution_id": "exec-1"})

    def get_json(self, url, *, required_field=None):
        self.calls.append(("status", url))
        return GatewayResponse(status_code=200, payload=self.status_payloads.pop(0))

    def delete(self, url):
        self.calls.append(("delete", url))
        self._falhar("delete")
