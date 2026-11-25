"""Testes do OCR pelo caminho B (File Manager + workflow de OCR), com fakes."""
from __future__ import annotations

import logging
import threading
import time

import fitz
import pytest

from src.domain.errors import ContractViolationError, DependencyUnavailableError, ValidationError
from src.services.ocr import (
    MOTIVO_OCR_FALHOU,
    MOTIVO_OCR_PENDENTE,
    MOTIVO_OCR_SEM_TEMPO,
    LeitorOcr,
    LeituraOcr,
    ler_digitalizados,
)
from src.services.validacao import ArquivoValidado
from src.settings import load_settings
from tests.unit.gateway_fakes import FakeOcrTransport, envelope, fake_clock
from tests.unit.pdf_fakes import JPEG_MINIMO, jpeg, pdf_sem_texto, png

_ENV = {
    "APP_ENV": "TEST",
    "APIM_WORKFLOW_URL": "https://wf.test/v1",
    "APIM_STATUS_URL": "https://status.test/v1",
    "APIM_FILE_MANAGER": "https://fm.test/filemanager/v1",
    "CONTAINER_ID": "container-1",
    "WORKFLOW_CODE": "WF_OCR_TESTE",
}


def _settings(**overrides):
    env = dict(_ENV)
    env.update(overrides)
    return load_settings(env)


def _leitor(transport, *, sleeps=None, clock=None, **env):
    return LeitorOcr(
        _settings(**env),
        transport,
        sleep=sleeps.append if sleeps is not None else (lambda _: None),
        clock=clock or (lambda: 0.0),
    )


def _escaneado():
    return ArquivoValidado(nome="cnh.jpg", conteudo=jpeg(), media_type="image/jpeg")


def _etapas(transport):
    return [etapa for etapa, _ in transport.calls]


# ─── Leitura de um documento ─────────────────────────────────────────────────

def test_sobe_roda_o_ocr_le_o_texto_e_apaga():
    transport = FakeOcrTransport(status=[envelope({"output_text": "texto do OCR"})])
    sleeps = []
    texto = _leitor(transport, sleeps=sleeps).ler(_escaneado(), timeout_seconds=120)

    assert texto == "texto do OCR"
    assert _etapas(transport) == ["upload", "start", "status", "delete"]
    assert transport.calls[0][1] == "https://fm.test/filemanager/v1/files/upload"
    assert transport.calls[3][1] == "https://fm.test/filemanager/v1/files/doc-1"
    passos = transport.start_body["input_collection"]["input_properties"][0]
    valores = {p["full_path"]: p["actual_value"] for p in passos["workflow_step_properties"]}
    assert transport.start_body["workflow_configuration_code"] == "WF_OCR_TESTE"
    assert valores["files_id"] == ["doc-1"]
    assert valores["files_path"] == ["/p/doc-1"]
    assert valores["input_text"] == ""
    # Intervalo do OCR (5 s), não o do Gerador de Texto (2 s).
    assert sleeps == [5.0]


def test_respeita_o_timeout_recebido():
    transport = FakeOcrTransport(status=[{"status": "WF_RUNNING"}])
    leitor = _leitor(transport, clock=fake_clock(0, 8))
    with pytest.raises(DependencyUnavailableError):
        leitor.ler(_escaneado(), timeout_seconds=7)
    assert _etapas(transport)[-1] == "delete"


def test_apaga_o_arquivo_mesmo_quando_o_ocr_falha():
    transport = FakeOcrTransport(status=[{"status": "WF_COMPLETED_ERROR"}])
    with pytest.raises(DependencyUnavailableError):
        _leitor(transport).ler(_escaneado(), timeout_seconds=120)
    assert _etapas(transport) == ["upload", "start", "status", "delete"]


def test_ocr_concluido_sem_texto_e_quebra_de_contrato():
    transport = FakeOcrTransport(status=[envelope({"output_text": "  "})])
    with pytest.raises(ContractViolationError):
        _leitor(transport).ler(_escaneado(), timeout_seconds=120)
    assert _etapas(transport)[-1] == "delete"


def test_upload_sem_path_nao_inicia_o_ocr_mas_apaga():
    transport = FakeOcrTransport(upload={"id": "doc-1"})
    with pytest.raises(ContractViolationError):
        _leitor(transport).ler(_escaneado(), timeout_seconds=120)
    assert _etapas(transport) == ["upload", "delete"]


def test_falha_no_upload_nao_tenta_apagar():
    transport = FakeOcrTransport(erros={"upload": DependencyUnavailableError("fora")})
    with pytest.raises(DependencyUnavailableError):
        _leitor(transport).ler(_escaneado(), timeout_seconds=120)
    assert _etapas(transport) == ["upload"]


def test_falha_no_delete_nao_derruba_a_leitura(caplog):
    transport = FakeOcrTransport(
        status=[envelope({"output_text": "texto do OCR"})],
        erros={"delete": DependencyUnavailableError("fora")},
    )
    with caplog.at_level(logging.INFO):
        texto = _leitor(transport).ler(_escaneado(), timeout_seconds=120)
    assert texto == "texto do OCR"
    eventos = [getattr(r, "structured", {}).get("event") for r in caplog.records]
    assert "ocr_delete_falhou" in eventos


def test_delete_bem_sucedido_fica_registrado_no_log(caplog):
    # É a única evidência, no teste real, de que o arquivo saiu do storage.
    transport = FakeOcrTransport(status=[envelope({"output_text": "texto do OCR"})])
    with caplog.at_level(logging.INFO):
        _leitor(transport).ler(_escaneado(), timeout_seconds=120)
    eventos = [getattr(r, "structured", {}).get("event") for r in caplog.records]
    assert "ocr_arquivo_apagado" in eventos


# ─── Fotos viram PDF antes do upload (a Gateway recusa JPEG e PNG) ────────────

def _ler_e_capturar_upload(arquivo):
    transport = FakeOcrTransport(status=[envelope({"output_text": "texto do OCR"})])
    _leitor(transport).ler(arquivo, timeout_seconds=120)
    return transport.upload_file


@pytest.mark.parametrize(
    "nome, conteudo, media_type",
    [("cnh.jpg", jpeg(), "image/jpeg"), ("rg.foto.png", png(), "image/png")],
    ids=["jpeg", "png"],
)
def test_foto_sobe_como_pdf(nome, conteudo, media_type):
    arquivo = ArquivoValidado(nome=nome, conteudo=conteudo, media_type=media_type)
    nome_enviado, enviado, tipo_enviado = _ler_e_capturar_upload(arquivo)

    assert nome_enviado == nome.rsplit(".", 1)[0] + ".pdf"
    assert tipo_enviado == "application/pdf"
    with fitz.open(stream=enviado, filetype="pdf") as doc:
        assert doc.page_count == 1
        assert len(doc[0].get_images()) == 1


def test_pdf_escaneado_sobe_como_veio():
    conteudo = pdf_sem_texto()
    arquivo = ArquivoValidado(nome="formulario.pdf", conteudo=conteudo, media_type="application/pdf")
    assert _ler_e_capturar_upload(arquivo) == ("formulario.pdf", conteudo, "application/pdf")


@pytest.mark.parametrize("orientacao, retrato", [(1, False), (6, True)])
def test_foto_de_celular_respeita_a_rotacao_do_exif(orientacao, retrato):
    # Celular grava a foto deitada (200x100) e marca a rotação no EXIF.
    arquivo = ArquivoValidado(
        nome="cnh.jpg", conteudo=jpeg(200, 100, orientacao), media_type="image/jpeg"
    )
    _, enviado, _ = _ler_e_capturar_upload(arquivo)
    with fitz.open(stream=enviado, filetype="pdf") as doc:
        pagina = doc[0].rect
    assert (pagina.height > pagina.width) is retrato


def test_imagem_que_nao_abre_falha_sem_subir():
    transport = FakeOcrTransport()
    arquivo = ArquivoValidado(nome="cnh-fulano.jpg", conteudo=JPEG_MINIMO, media_type="image/jpeg")
    with pytest.raises(ValidationError) as exc:
        _leitor(transport).ler(arquivo, timeout_seconds=120)
    assert transport.calls == []
    # A mensagem vai para o log: sem o nome do arquivo.
    assert "fulano" not in exc.value.message


# ─── Disponibilidade ─────────────────────────────────────────────────────────

def test_disponivel_com_a_configuracao_completa():
    assert _leitor(FakeOcrTransport()).disponivel is True


@pytest.mark.parametrize(
    "faltando", ["APIM_FILE_MANAGER", "CONTAINER_ID", "WORKFLOW_CODE", "APIM_STATUS_URL"]
)
def test_indisponivel_sem_qualquer_item_da_configuracao(faltando):
    assert _leitor(FakeOcrTransport(), **{faltando: ""}).disponivel is False


# ─── Lote de digitalizados: paralelismo, orçamento e isolamento de falhas ────

class FakeLeitor:
    """Leitor fake: devolve "texto de <nome>" e registra paralelismo e timeouts."""

    def __init__(self, *, disponivel=True, erros=None, barreira=None, pausa=0.0):
        self.disponivel = disponivel
        self.erros = erros or {}
        self.barreira = barreira
        self.pausa = pausa
        self.timeouts = []
        self.max_ativos = 0
        self._ativos = 0
        self._lock = threading.Lock()

    def ler(self, arquivo, *, timeout_seconds):
        with self._lock:
            self.timeouts.append(timeout_seconds)
            self._ativos += 1
            self.max_ativos = max(self.max_ativos, self._ativos)
        try:
            if self.barreira is not None:
                self.barreira.wait(timeout=2)
            if self.pausa:
                time.sleep(self.pausa)
            if arquivo.nome in self.erros:
                raise self.erros[arquivo.nome]
            return f"texto de {arquivo.nome}"
        finally:
            with self._lock:
                self._ativos -= 1


def _arquivos(*nomes):
    return [ArquivoValidado(nome=n, conteudo=JPEG_MINIMO, media_type="image/jpeg") for n in nomes]


def _ocr(**env):
    return _settings(**env).ocr


def test_lista_vazia_nao_le_nada():
    leitor = FakeLeitor()
    assert ler_digitalizados([], leitor, _ocr()) == []
    assert leitor.timeouts == []


@pytest.mark.parametrize("leitor", [None, FakeLeitor(disponivel=False)])
def test_sem_configuracao_nao_tenta_ler(leitor):
    resultado = ler_digitalizados(_arquivos("a", "b"), leitor, _ocr())
    assert resultado == [LeituraOcr(motivo=MOTIVO_OCR_PENDENTE)] * 2
    assert leitor is None or leitor.timeouts == []


def test_devolve_na_ordem_dos_arquivos():
    resultado = ler_digitalizados(_arquivos("a", "b", "c"), FakeLeitor(), _ocr())
    assert [r.texto for r in resultado] == ["texto de a", "texto de b", "texto de c"]
    assert all(r.motivo is None for r in resultado)


def test_le_os_documentos_em_paralelo():
    # Em sequência, a barreira de 2 nunca se completaria e as leituras falhariam.
    leitor = FakeLeitor(barreira=threading.Barrier(2))
    resultado = ler_digitalizados(_arquivos("a", "b"), leitor, _ocr(OCR_MAX_PARALLEL="2"))
    assert [r.texto for r in resultado] == ["texto de a", "texto de b"]


def test_respeita_o_limite_de_leituras_simultaneas():
    leitor = FakeLeitor(pausa=0.05)
    ler_digitalizados(_arquivos("a", "b", "c", "d", "e"), leitor, _ocr(OCR_MAX_PARALLEL="2"))
    assert leitor.max_ativos == 2


@pytest.mark.parametrize("orcamento, esperado", [("150", 120.0), ("100", 100.0)])
def test_timeout_de_cada_documento_nao_passa_do_orcamento(orcamento, esperado):
    leitor = FakeLeitor()
    ler_digitalizados(
        _arquivos("a"), leitor, _ocr(OCR_BUDGET_SECONDS=orcamento), clock=lambda: 0.0
    )
    assert leitor.timeouts == [esperado]


def test_orcamento_esgotado_marca_os_restantes_sem_ler():
    leitor = FakeLeitor()
    resultado = ler_digitalizados(
        _arquivos("a", "b"),
        leitor,
        _ocr(OCR_MAX_PARALLEL="1", OCR_BUDGET_SECONDS="10"),
        clock=fake_clock(0, 0, 11),
    )
    assert resultado == [LeituraOcr(texto="texto de a"), LeituraOcr(motivo=MOTIVO_OCR_SEM_TEMPO)]
    assert leitor.timeouts == [10.0]


def test_falha_de_um_documento_nao_derruba_os_outros(caplog):
    leitor = FakeLeitor(
        erros={
            "cnh-fulano.jpg": DependencyUnavailableError("fora"),
            "rg-beltrano.jpg": RuntimeError("defeito"),
        }
    )
    with caplog.at_level(logging.INFO):
        resultado = ler_digitalizados(
            _arquivos("contrato.jpg", "cnh-fulano.jpg", "rg-beltrano.jpg"), leitor, _ocr()
        )
    assert resultado == [
        LeituraOcr(texto="texto de contrato.jpg"),
        LeituraOcr(motivo=MOTIVO_OCR_FALHOU),
        LeituraOcr(motivo=MOTIVO_OCR_FALHOU),
    ]
    registros = [getattr(r, "structured", {}) for r in caplog.records]
    falhas = [r for r in registros if r.get("event") == "ocr_documento_falhou"]
    assert len(falhas) == 2
    # O nome do arquivo pode conter nome de pessoa: não vai para o log.
    assert "fulano" not in str(registros) and "beltrano" not in str(registros)


def test_log_da_falha_diz_qual_etapa_falhou(caplog):
    # A categoria sozinha não separa "upload sem path" de "OCR sem texto".
    erro = ContractViolationError("Referencia de documento sem caminho de armazenamento.")
    with caplog.at_level(logging.INFO):
        ler_digitalizados(_arquivos("a.jpg"), FakeLeitor(erros={"a.jpg": erro}), _ocr())
    falha = [
        r.structured for r in caplog.records
        if getattr(r, "structured", {}).get("event") == "ocr_documento_falhou"
    ][0]
    assert falha["category"] == "CONTRACT_VIOLATION"
    assert falha["detail"] == "Referencia de documento sem caminho de armazenamento."
