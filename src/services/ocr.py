"""Leitura de documentos digitalizados (PDF escaneado e imagem) — caminho B.

O Gerador de Texto não lê imagem (call de 18/09), então o texto vem do
workflow de OCR da Gateway. Por documento: upload no File Manager → workflow de
OCR → polling → texto → exclusão do arquivo, para não acumular documentos com
dados pessoais no storage. A Gateway recusa JPEG e PNG (teste de 21/09), então a
foto vira um PDF de uma página antes do upload.

Os documentos de uma análise são lidos em paralelo (até ``OCR_MAX_PARALLEL``)
e dentro de um orçamento de tempo (``OCR_BUDGET_SECONDS``), porque o fluxo é
síncrono e o front tem timeout. Sem a configuração do caminho B (File Manager,
container e código do workflow), a leitura não é tentada. Em qualquer caso, um
documento que não foi lido sai do parecer com o motivo, e a análise segue.
"""
from __future__ import annotations

import dataclasses
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence

import fitz  # PyMuPDF

from src.adapters.gateway.execution import output_text, wait_for_result
from src.adapters.gateway.upload import GatewayDocumentUploadAdapter
from src.adapters.gateway.workflow import GatewayDocumentWorkflowAdapter
from src.domain.errors import ContractViolationError, DomainError, ValidationError
from src.domain.models import DocumentUpload, WorkflowSubmission
from src.observability.logging import get_logger
from src.services.validacao import ArquivoValidado
from src.settings import AppSettings, OcrSettings

_logger = get_logger("ocr")

MOTIVO_OCR_PENDENTE = "Documento digitalizado: leitura por OCR ainda não disponível."
MOTIVO_OCR_FALHOU = "Documento digitalizado: a leitura por OCR falhou."
MOTIVO_OCR_SEM_TEMPO = (
    "Documento digitalizado: o tempo de leitura por OCR desta análise acabou."
)

#: Tipo de análise registrado na submissão do workflow (não vai no payload).
TIPO_ANALISE = "ALTERACAO_CADASTRAL"


@dataclass(frozen=True)
class LeituraOcr:
    """Resultado de um digitalizado: o texto ou o motivo de não ter sido lido."""

    texto: str = ""
    motivo: Optional[str] = None


class LeitorOcr:
    """Lê um documento digitalizado pelo workflow de OCR da Gateway."""

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
        self._upload = GatewayDocumentUploadAdapter(settings, transport)
        self._workflow = GatewayDocumentWorkflowAdapter(settings, transport)

    @property
    def disponivel(self) -> bool:
        """Se a configuração do caminho B está completa neste ambiente."""
        endpoints = self._settings.gateway_endpoints
        recursos = self._settings.gateway_resources
        return all(
            (
                endpoints.file_manager_url,
                endpoints.workflow_url,
                endpoints.status_url,
                recursos.container_id,
                recursos.workflow_code,
            )
        )

    def ler(self, arquivo: ArquivoValidado, *, timeout_seconds: float) -> str:
        """Devolve o texto do documento; apaga o arquivo enviado em qualquer caso.

        Raises:
            DomainError: falha em qualquer etapa (upload, workflow, polling) ou
                OCR concluído sem texto.
        """
        arquivo = imagem_em_pdf(arquivo)
        referencia = self._upload.upload_document(
            DocumentUpload(
                safe_name=arquivo.nome,
                media_type=arquivo.media_type,
                content=arquivo.conteudo,
                size_bytes=len(arquivo.conteudo),
            )
        )
        try:
            execucao = self._workflow.start_document_workflow(
                WorkflowSubmission(analysis_type=TIPO_ANALISE, documents=(referencia,))
            )
            polling = dataclasses.replace(
                self._settings.polling,
                interval_seconds=self._settings.ocr.polling_interval_seconds,
                timeout_seconds=timeout_seconds,
            )
            resultado = wait_for_result(
                self._settings,
                self._transport,
                execucao.external_execution_id,
                polling=polling,
                sleep=self._sleep,
                clock=self._clock,
            )
            texto = output_text(resultado)
            if texto is None:
                raise ContractViolationError("Workflow de OCR concluido sem texto.")
            return texto
        finally:
            self._apagar(referencia.external_document_id)

    def _apagar(self, document_id: str) -> None:
        try:
            self._upload.delete_document(document_id)
        except DomainError as exc:
            # A leitura já terminou (com ou sem texto): o arquivo que sobra no
            # storage não muda o resultado da análise.
            _logger.warning("ocr_delete_falhou", category=exc.category.value)
            return
        _logger.info("ocr_arquivo_apagado")


def imagem_em_pdf(arquivo: ArquivoValidado) -> ArquivoValidado:
    """Converte uma foto em PDF de uma página; PDF passa como veio.

    ``convert_to_pdf`` aplica a rotação gravada no EXIF pelo celular, o que o
    ``insert_image`` não faz: sem isso o OCR recebe o documento deitado.

    Raises:
        ValidationError: a imagem não abre (só a assinatura era válida).
    """
    if arquivo.media_type == "application/pdf":
        return arquivo
    try:
        with fitz.open(stream=arquivo.conteudo, filetype=arquivo.media_type.split("/")[1]) as img:
            conteudo = img.convert_to_pdf()
    except Exception as exc:  # noqa: BLE001 - PyMuPDF levanta tipos variados
        # Sem o nome do arquivo: a mensagem vai para o log.
        raise ValidationError("Imagem nao pode ser convertida em PDF para o OCR.") from exc
    return ArquivoValidado(
        nome=arquivo.nome.rsplit(".", 1)[0] + ".pdf",
        conteudo=conteudo,
        media_type="application/pdf",
    )


def ler_digitalizados(
    arquivos: Sequence[ArquivoValidado],
    leitor: Optional[LeitorOcr],
    config: OcrSettings,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> List[LeituraOcr]:
    """Lê os digitalizados em paralelo e devolve os resultados na ordem recebida.

    O timeout de cada documento é o menor entre o limite por documento e o que
    resta do orçamento; um documento que só começaria depois do fim do
    orçamento nem é enviado. A falha de um documento vira motivo e não afeta
    os demais.
    """
    if not arquivos:
        return []
    if leitor is None or not leitor.disponivel:
        _logger.warning("ocr_indisponivel", documentos=len(arquivos))
        return [LeituraOcr(motivo=MOTIVO_OCR_PENDENTE) for _ in arquivos]

    prazo = clock() + config.budget_seconds

    def ler_um(arquivo: ArquivoValidado) -> LeituraOcr:
        restante = prazo - clock()
        if restante <= 0:
            return LeituraOcr(motivo=MOTIVO_OCR_SEM_TEMPO)
        timeout = min(config.timeout_per_document_seconds, restante)
        try:
            return LeituraOcr(texto=leitor.ler(arquivo, timeout_seconds=timeout))
        except DomainError as exc:
            # A mensagem dos erros do BFF é segura para log e diz a etapa.
            _logger.warning(
                "ocr_documento_falhou", category=exc.category.value, detail=exc.message
            )
        except Exception as exc:  # noqa: BLE001 - um defeito não derruba o lote
            _logger.error("ocr_documento_falhou", error=exc)
        return LeituraOcr(motivo=MOTIVO_OCR_FALHOU)

    with ThreadPoolExecutor(max_workers=min(config.max_parallel, len(arquivos))) as pool:
        return list(pool.map(ler_um, arquivos))
