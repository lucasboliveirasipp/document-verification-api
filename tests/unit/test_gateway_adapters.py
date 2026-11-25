"""Testes dos adaptadores do caminho B de OCR (File Manager + workflow de OCR).

Um transporte fake devolve ``GatewayResponse`` direto, isolando a montagem de
payload e a leitura de resposta do HTTP (testado à parte).
"""
from __future__ import annotations

import pytest

from src.adapters.gateway.transport import GatewayResponse
from src.adapters.gateway.upload import GatewayDocumentUploadAdapter
from src.adapters.gateway.workflow import GatewayDocumentWorkflowAdapter
from src.domain.errors import CapabilityUnavailableError, ContractViolationError
from src.domain.models import DocumentReference, DocumentUpload, WorkflowSubmission
from src.settings import load_settings


class FakeTransport:
    """Registra chamadas e devolve um payload pré-definido."""

    def __init__(self, payload=None):
        self._payload = payload or {}
        self.calls = []

    def post_multipart(self, url, *, files, data=None, required_field=None):
        self.calls.append(("multipart", url, {"files": files, "data": data}))
        return GatewayResponse(status_code=200, payload=self._payload)

    def post_json(self, url, *, json_body, required_field=None, idempotent=False):
        self.calls.append(("post", url, {"json": json_body, "idempotent": idempotent}))
        return GatewayResponse(status_code=200, payload=self._payload)

    def delete(self, url):
        self.calls.append(("delete", url, {}))


def _settings(**overrides):
    base = {
        "ENV": "test",
        "APP_ROOT_PATH": "/x",
        "APIM_WORKFLOW_URL": "https://wf.example.com/v1",
        "APIM_STATUS_URL": "https://status.example.com/v1",
        "APIM_FILE_MANAGER": "https://fm.example.com/filemanager/v1/",
        "CONTAINER_ID": "container-1",
        "WORKFLOW_CODE": "WF_OCR",
        "STEP_WORKFLOW_CODE": "STEP_OCR",
    }
    base.update(overrides)
    return load_settings(base)


def _upload():
    return DocumentUpload(
        safe_name="form.pdf", media_type="application/pdf", content=b"x", size_bytes=1
    )


# ─── Upload ──────────────────────────────────────────────────────────────────

def test_upload_segue_o_contrato_do_file_manager_no_apim():
    transport = FakeTransport(
        {"id": "doc-1", "file_name": "form.pdf", "container_name": "container-1"}
    )
    GatewayDocumentUploadAdapter(_settings(), transport).upload_document(_upload())
    _, url, enviado = transport.calls[0]
    assert url == "https://fm.example.com/filemanager/v1/files/upload"
    assert enviado["files"] == {"file": ("form.pdf", b"x", "application/pdf")}
    # Campos de formulário, não query string. create_container=true, como no
    # código do João: o container de um projeto novo só nasce no primeiro
    # upload, e com "false" o File Manager recusou (406 no notebook, 21/09).
    assert enviado["data"] == {
        "container_name": "container-1",
        "create_container": "true",
        "overwrite": "true",
    }


def test_upload_guarda_id_e_path_quando_vierem():
    transport = FakeTransport({"id": "doc-1", "path": "/store/doc-1"})
    ref = GatewayDocumentUploadAdapter(_settings(), transport).upload_document(_upload())
    assert ref.external_document_id == "doc-1"
    assert ref.storage_path == "/store/doc-1"


def test_upload_sem_path_guarda_so_o_id():
    # A doc oficial só garante o id; quem exige o path é o workflow de OCR.
    adapter = GatewayDocumentUploadAdapter(_settings(), FakeTransport({"id": "doc-1"}))
    ref = adapter.upload_document(_upload())
    assert ref.external_document_id == "doc-1"
    assert ref.storage_path is None


@pytest.mark.parametrize(
    "payload", [{"file_name": "form.pdf"}, {"id": ""}, {"file_id": {"id": "x"}}]
)
def test_upload_sem_id_e_quebra_de_contrato(payload):
    adapter = GatewayDocumentUploadAdapter(_settings(), FakeTransport(payload))
    with pytest.raises(ContractViolationError):
        adapter.upload_document(_upload())


def test_upload_without_container_config_fails_closed():
    adapter = GatewayDocumentUploadAdapter(
        _settings(CONTAINER_ID=""), FakeTransport({"id": "d"})
    )
    with pytest.raises(CapabilityUnavailableError):
        adapter.upload_document(_upload())


def test_delete_apaga_pelo_id_no_file_manager():
    transport = FakeTransport()
    GatewayDocumentUploadAdapter(_settings(), transport).delete_document("doc/1")
    assert transport.calls == [
        ("delete", "https://fm.example.com/filemanager/v1/files/doc%2F1", {})
    ]


def test_delete_sem_file_manager_configurado_falha_fechado():
    adapter = GatewayDocumentUploadAdapter(_settings(APIM_FILE_MANAGER=""), FakeTransport())
    with pytest.raises(CapabilityUnavailableError):
        adapter.delete_document("doc-1")


# ─── Workflow de OCR ─────────────────────────────────────────────────────────

def _submission():
    docs = (
        DocumentReference(external_document_id="doc-1", safe_name="a.pdf",
                          media_type="application/pdf", size_bytes=1, storage_path="/p/1"),
        DocumentReference(external_document_id="doc-2", safe_name="b.pdf",
                          media_type="application/pdf", size_bytes=1, storage_path="/p/2"),
    )
    return WorkflowSubmission(analysis_type="ALTERACAO_CADASTRAL", documents=docs)


def test_workflow_builds_ocr_full_paths_in_order():
    transport = FakeTransport({"workflow_execution_id": "exec-1"})
    ref = GatewayDocumentWorkflowAdapter(_settings(), transport).start_document_workflow(
        _submission()
    )
    assert ref.external_execution_id == "exec-1"

    body = transport.calls[0][2]["json"]
    assert body["workflow_configuration_code"] == "WF_OCR"
    props = body["input_collection"]["input_properties"][0]
    assert props["workflow_step_order"] == 0
    by_path = {p["full_path"]: p["actual_value"] for p in props["workflow_step_properties"]}
    assert by_path["files_id"] == ["doc-1", "doc-2"]
    assert by_path["files_path"] == ["/p/1", "/p/2"]
    assert by_path["container"] == "container-1"
    assert by_path["input_text"] == ""
    assert transport.calls[0][2]["idempotent"] is False
    assert transport.calls[0][1] == "https://wf.example.com/v1/request/start-workflow"


def test_workflow_requires_storage_path():
    docs = (DocumentReference(external_document_id="doc-1", safe_name="a.pdf",
                              media_type="application/pdf", size_bytes=1),)
    adapter = GatewayDocumentWorkflowAdapter(_settings(), FakeTransport({"workflow_execution_id": "e"}))
    with pytest.raises(ContractViolationError):
        adapter.start_document_workflow(WorkflowSubmission(analysis_type="X", documents=docs))


def test_workflow_rejects_unknown_execution_id():
    adapter = GatewayDocumentWorkflowAdapter(
        _settings(), FakeTransport({"workflow_execution_id": "UNKNOWN"})
    )
    with pytest.raises(ContractViolationError):
        adapter.start_document_workflow(_submission())


def test_workflow_without_code_fails_closed():
    adapter = GatewayDocumentWorkflowAdapter(
        _settings(WORKFLOW_CODE=""), FakeTransport({"workflow_execution_id": "e"})
    )
    with pytest.raises(CapabilityUnavailableError):
        adapter.start_document_workflow(_submission())
