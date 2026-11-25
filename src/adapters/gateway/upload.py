"""Upload e exclusão no File Manager da Gateway (caminho B do OCR).

Contrato do File Manager no APIM (doc da Gateway; o mesmo que o exemplo de
indexação de referência e o ``api-certificacao-gateway`` usam por baixo):

* ``POST {APIM_FILE_MANAGER}/files/upload``, ``multipart/form-data``;
* campos de formulário ``container_name``, ``create_container`` e
  ``overwrite``, com os booleanos como **string minúscula**;
* sem ``connection_mode``: esse parâmetro é do BFF vitrine, não do APIM;
* a resposta traz ``id`` (obrigatório) e, segundo a implementação de referência, ``path``,
  que a doc oficial não lista. O ``path`` é guardado quando vier; quem o
  exige é o workflow de OCR.

Exclusão: ``DELETE {APIM_FILE_MANAGER}/files/{id}``, depois do OCR, para não
acumular documentos com dados pessoais no storage.

O endpoint e o container são configuração; sem eles, falha fechada.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from src.adapters.gateway.transport import GatewayTransport
from src.domain.errors import CapabilityUnavailableError, ContractViolationError
from src.domain.models import DocumentReference, DocumentUpload
from src.settings import AppSettings

UPLOAD_PATH = "/files/upload"
FILES_PATH = "/files/"


class GatewayDocumentUploadAdapter:
    """Envia um documento ao File Manager e devolve sua referência opaca."""

    def __init__(self, settings: AppSettings, transport: GatewayTransport) -> None:
        self._settings = settings
        self._transport = transport

    def upload_document(self, document: DocumentUpload) -> DocumentReference:
        """Envia um documento validado e devolve o id (e o path, se vier)."""
        base = self._settings.gateway_endpoints.file_manager_url
        container = self._settings.gateway_resources.container_id
        if not base or not container:
            # Gate G2: sem endpoint/container homologados, falha fechada em vez
            # de improvisar um destino.
            raise CapabilityUnavailableError(
                "Armazenamento de documentos nao configurado neste ambiente."
            )

        response = self._transport.post_multipart(
            base.rstrip("/") + UPLOAD_PATH,
            files={
                # media_type já validado por conteúdo pelo BFF (não derivado do
                # nome, ao contrário do projeto de referência).
                "file": (document.safe_name, document.content, document.media_type),
            },
            data={
                "container_name": container,
                # "true", como na implementação de referência: o container de um projeto
                # novo só nasce no primeiro upload, e com "false" o File
                # Manager recusou (406). Upload e OCR usam o mesmo
                # CONTAINER_ID, então o workflow sempre enxerga o arquivo.
                "create_container": "true",
                "overwrite": "true",
            },
            required_field=None,  # a guarda do id é específica abaixo.
        )

        document_id = response.payload.get("id")
        if not document_id:
            # Transporte OK não implica sucesso funcional: sem id não há o que
            # mandar ao workflow de OCR.
            raise ContractViolationError(
                "Armazenamento nao retornou referencia utilizavel do documento."
            )

        path = response.payload.get("path")
        return DocumentReference(
            external_document_id=str(document_id),
            storage_path=str(path) if path else None,
            safe_name=document.safe_name,
            media_type=document.media_type,
            size_bytes=document.size_bytes,
            checksum=document.checksum,
            received_at=datetime.now(timezone.utc),
        )

    def delete_document(self, external_document_id: str) -> None:
        """Apaga um documento do File Manager pelo id devolvido no upload."""
        base = self._settings.gateway_endpoints.file_manager_url
        if not base:
            raise CapabilityUnavailableError(
                "Armazenamento de documentos nao configurado neste ambiente."
            )
        self._transport.delete(base.rstrip("/") + FILES_PATH + quote(external_document_id, safe=""))
