"""Testes da orquestração síncrona (gerador fake, sem Gateway)."""
from __future__ import annotations

from datetime import date

import pytest

from src.domain.errors import ContractViolationError, DependencyUnavailableError, ValidationError
from src.services.analise_service import analisar_documentos
from src.services.ocr import MOTIVO_OCR_FALHOU
from src.services.validacao import ArquivoRecebido
from src.settings import load_settings
from tests.unit.parecer_fakes import PARECER_JSON
from tests.unit.pdf_fakes import (
    JPEG_MINIMO,
    pdf_com_texto,
    pdf_digital_com_imagem,
    pdf_escaneado_com_anotacao,
    pdf_sem_texto,
)


class FakeGerador:
    def __init__(self, resposta=PARECER_JSON):
        self.resposta = resposta
        self.chamadas = []

    def gerar_texto(self, system_prompt, user_content, *, response_format=None, max_tokens=None):
        self.chamadas.append(
            {"system": system_prompt, "user": user_content, "response_format": response_format}
        )
        return self.resposta


class FakeLeitorOcr:
    disponivel = True

    def __init__(self, texto="texto lido por OCR", erro=None):
        self.texto = texto
        self.erro = erro
        self.lidos = []

    def ler(self, arquivo, *, timeout_seconds):
        self.lidos.append(arquivo.nome)
        if self.erro:
            raise self.erro
        return self.texto


def _settings(**env):
    base = {"APP_ENV": "TEST"}
    base.update(env)
    return load_settings(base)


def _contrato():
    return ArquivoRecebido("contrato.pdf", pdf_com_texto("Contrato social da empresa exemplo"))


def test_analise_com_documentos_legiveis():
    gerador = FakeGerador()
    resposta = analisar_documentos([_contrato()], _settings(), gerador)
    assert resposta.parecer.quadro_resumo.tipo_alteracao == "Troca de titularidade"
    assert [d.model_dump() for d in resposta.documentos] == [
        {"nome": "contrato.pdf", "paginas": 1, "lido": True, "motivo": None, "papel": None}
    ]
    assert resposta.avisos == []
    chamada = gerador.chamadas[0]
    assert chamada["response_format"] == {"type": "json_object"}
    assert "Contrato social da empresa exemplo" in chamada["user"]


def test_digitalizados_ficam_fora_e_geram_aviso():
    gerador = FakeGerador()
    arquivos = [
        _contrato(),
        ArquivoRecebido("formulario.pdf", pdf_sem_texto()),
        ArquivoRecebido("cnh.jpg", JPEG_MINIMO),
    ]
    resposta = analisar_documentos(arquivos, _settings(), gerador)
    nao_lidos = [d for d in resposta.documentos if not d.lido]
    assert [d.nome for d in nao_lidos] == ["formulario.pdf", "cnh.jpg"]
    assert all("OCR" in (d.motivo or "") for d in nao_lidos)
    assert resposta.avisos == ["2 documento(s) não puderam ser lidos e não entraram na análise."]
    assert "- formulario.pdf" in gerador.chamadas[0]["user"]


def test_texto_do_ocr_entra_no_prompt():
    gerador = FakeGerador()
    leitor = FakeLeitorOcr()
    resposta = analisar_documentos(
        [_contrato(), ArquivoRecebido("formulario.pdf", pdf_sem_texto())],
        _settings(),
        gerador,
        leitor,
    )
    # Só o digitalizado vai para o OCR; o PDF com texto é lido localmente.
    assert leitor.lidos == ["formulario.pdf"]
    assert [d.lido for d in resposta.documentos] == [True, True]
    assert resposta.avisos == []
    assert "texto lido por OCR" in gerador.chamadas[0]["user"]


def test_falha_no_ocr_deixa_o_documento_fora_e_a_analise_segue():
    gerador = FakeGerador()
    resposta = analisar_documentos(
        [_contrato(), ArquivoRecebido("cnh.jpg", JPEG_MINIMO)],
        _settings(),
        gerador,
        FakeLeitorOcr(erro=DependencyUnavailableError("fora")),
    )
    assert resposta.documentos[1].lido is False
    assert resposta.documentos[1].motivo == MOTIVO_OCR_FALHOU
    assert resposta.avisos == ["1 documento(s) não puderam ser lidos e não entraram na análise."]
    assert "- cnh.jpg" in gerador.chamadas[0]["user"]


def test_nenhum_documento_legivel_nao_chama_a_gateway():
    gerador = FakeGerador()
    with pytest.raises(ValidationError, match="Nenhum dos documentos"):
        analisar_documentos([ArquivoRecebido("f.pdf", pdf_sem_texto())], _settings(), gerador)
    assert gerador.chamadas == []


def test_lote_invalido_nao_chama_a_gateway():
    gerador = FakeGerador()
    with pytest.raises(ValidationError):
        analisar_documentos([ArquivoRecebido("x.pdf", b"nao e pdf")], _settings(), gerador)
    assert gerador.chamadas == []


def test_prompt_grande_demais_e_recusado():
    gerador = FakeGerador()
    grande = ArquivoRecebido("grande.pdf", pdf_com_texto("palavra " * 12, paginas=20))
    with pytest.raises(ValidationError, match="excedem o tamanho"):
        analisar_documentos([grande], _settings(TEXTGEN_MAX_PROMPT_CHARS="1000"), gerador)
    assert gerador.chamadas == []


def test_resposta_fora_do_formato_vira_violacao_de_contrato():
    with pytest.raises(ContractViolationError):
        analisar_documentos([_contrato()], _settings(), FakeGerador('{"quadro_resumo": {}}'))



def test_prompt_leva_a_data_de_hoje():
    gerador = FakeGerador()
    analisar_documentos([_contrato()], _settings(), gerador, hoje=date(2026, 9, 22))
    assert "DATA DE HOJE: 22/09/2026" in gerador.chamadas[0]["user"]


def test_sem_data_informada_usa_a_data_do_dia():
    gerador = FakeGerador()
    analisar_documentos([_contrato()], _settings(), gerador)
    assert date.today().strftime("%d/%m/%Y") in gerador.chamadas[0]["user"]



# ─── Texto digital sobre escaneado (lote real de 22/09) ──────────────────────
# O "CONJUGES" do Formulário2 é uma anotação digital sobre o scan: o OCR não o
# devolve, e o BFF descartava a camada de texto de todo digitalizado.

def test_texto_digital_sobre_escaneado_entra_junto_com_o_ocr():
    gerador = FakeGerador()
    analisar_documentos(
        [ArquivoRecebido("formulario2.pdf", pdf_escaneado_com_anotacao("CONJUGES"))],
        _settings(),
        gerador,
        FakeLeitorOcr(),
    )
    prompt = gerador.chamadas[0]["user"]
    assert "texto lido por OCR" in prompt
    assert "CONJUGES" in prompt
    assert "TEXTO DIGITAL SOBRE O ESCANEADO" in prompt


def test_escaneado_sem_ocr_nao_vira_lido_so_pela_anotacao():
    gerador = FakeGerador()
    resposta = analisar_documentos(
        [_contrato(), ArquivoRecebido("formulario2.pdf", pdf_escaneado_com_anotacao("CONJUGES"))],
        _settings(),
        gerador,
        FakeLeitorOcr(erro=DependencyUnavailableError("fora")),
    )
    assert resposta.documentos[1].lido is False
    assert "CONJUGES" not in gerador.chamadas[0]["user"]


# ─── CNH digital: dados em imagem, rodapé em texto (teste de 06/10) ───────────
# A camada de texto da CNH do Senatran é só o rodapé da assinatura; o BFF a
# tratava como PDF digital e a IA nunca via nome, CPF e nascimento.

RODAPE_CNH = "Documento assinado com certificado digital. Assinador Digital. " * 5


def _cnh_digital():
    return ArquivoRecebido("cnh.pdf", pdf_digital_com_imagem(RODAPE_CNH))


def test_cnh_digital_vai_para_o_ocr_e_mantem_a_camada_de_texto():
    gerador, leitor = FakeGerador(), FakeLeitorOcr(texto="NOME PESSOA EXEMPLO CPF 000")
    analisar_documentos([_cnh_digital()], _settings(), gerador, leitor)
    assert leitor.lidos == ["cnh.pdf"]
    prompt = gerador.chamadas[0]["user"]
    assert "NOME PESSOA EXEMPLO CPF 000" in prompt
    assert "[CAMADA DE TEXTO DO PDF]" in prompt and "Assinador Digital" in prompt
    assert "TEXTO DIGITAL SOBRE O ESCANEADO" not in prompt


def test_cnh_digital_com_ocr_fora_usa_o_texto_digital_como_antes():
    gerador = FakeGerador()
    resposta = analisar_documentos(
        [_cnh_digital()], _settings(), gerador, FakeLeitorOcr(erro=DependencyUnavailableError("fora"))
    )
    assert resposta.documentos[0].lido is True
    assert resposta.avisos == []
    assert "Assinador Digital" in gerador.chamadas[0]["user"]


# ─── Diagnóstico local: o que chega à IA e o que ela devolve ─────────────────
# Serve para separar erro do OCR de erro da IA (ex.: a razão social abreviada
# chega abreviada ao modelo?). Só em DSV e só com DIAGNOSTICO_DIR.

def _diagnosticos(pasta):
    return sorted(pasta.glob("diagnostico-*.txt"))


def test_diagnostico_grava_o_prompt_e_a_resposta(tmp_path):
    analisar_documentos(
        [ArquivoRecebido("formulario2.pdf", pdf_escaneado_com_anotacao("CONJUGES"))],
        _settings(APP_ENV="DSV", DIAGNOSTICO_DIR=str(tmp_path)),
        FakeGerador(),
        FakeLeitorOcr(texto="EMPRESA EXEMPLO COMERCIO DE PRODUTOS ALIMENT"),
    )
    [arquivo] = _diagnosticos(tmp_path)
    conteudo = arquivo.read_text(encoding="utf-8")
    assert "EMPRESA EXEMPLO COMERCIO DE PRODUTOS ALIMENT" in conteudo
    assert "CONJUGES" in conteudo
    assert '"quadro_resumo"' in conteudo


def test_diagnostico_guarda_o_prompt_mesmo_quando_a_ia_falha(tmp_path):
    with pytest.raises(ContractViolationError):
        analisar_documentos(
            [_contrato()],
            _settings(APP_ENV="DSV", DIAGNOSTICO_DIR=str(tmp_path)),
            FakeGerador(resposta="não é json"),
        )
    [arquivo] = _diagnosticos(tmp_path)
    conteudo = arquivo.read_text(encoding="utf-8")
    assert "Contrato social da empresa exemplo" in conteudo
    assert "não é json" in conteudo


def test_falha_ao_gravar_o_diagnostico_nao_derruba_a_analise(tmp_path):
    ocupado = tmp_path / "arquivo"
    ocupado.write_text("não é pasta")
    resposta = analisar_documentos(
        [_contrato()],
        _settings(APP_ENV="DSV", DIAGNOSTICO_DIR=str(ocupado)),
        FakeGerador(),
    )
    assert resposta.parecer is not None


def test_sem_diagnostico_nada_e_gravado(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    analisar_documentos([_contrato()], _settings(APP_ENV="DSV"), FakeGerador())
    assert list(tmp_path.rglob("*")) == []


def test_falha_ao_anexar_a_resposta_nao_derruba_a_analise(tmp_path, monkeypatch):
    class GeradorQueTrancaODiagnostico(FakeGerador):
        def gerar_texto(self, *args, **kwargs):
            for arquivo in _diagnosticos(tmp_path):
                arquivo.unlink()
                arquivo.mkdir()  # abrir para anexar agora falha
            return super().gerar_texto(*args, **kwargs)

    resposta = analisar_documentos(
        [_contrato()],
        _settings(APP_ENV="DSV", DIAGNOSTICO_DIR=str(tmp_path)),
        GeradorQueTrancaODiagnostico(),
    )
    assert resposta.parecer is not None


def test_papel_informado_no_envio_entra_no_prompt():
    gerador = FakeGerador()
    analisar_documentos(
        [ArquivoRecebido("doc.pdf", pdf_com_texto("Contrato social da empresa exemplo"), papel="contrato_social")],
        _settings(),
        gerador,
    )
    prompt = gerador.chamadas[0]["user"]
    assert "### Documento 1: doc.pdf\nTipo informado no envio: Contrato social ou ata de assembleia\n" in prompt
