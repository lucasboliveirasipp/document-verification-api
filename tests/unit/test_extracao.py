"""Testes da extração local de texto."""
from __future__ import annotations

import pytest

from src.domain.errors import ValidationError
from src.services.extracao import extrair_documento
from src.services.validacao import ArquivoValidado
from tests.unit.pdf_fakes import (
    JPEG_MINIMO,
    pdf_com_senha,
    pdf_com_texto,
    pdf_digital_com_imagem,
    pdf_sem_texto,
)

#: Rodapé da CNH digital, sem dado pessoal (é o que a camada de texto traz).
RODAPE_CNH = (
    "Documento assinado com certificado digital em conformidade com a Medida "
    "Provisória nº 2200-2/2001. Sua validade poderá ser confirmada por meio do "
    "programa Assinador Digital. "
) * 2


def _pdf(conteudo, nome="doc.pdf"):
    return ArquivoValidado(nome=nome, conteudo=conteudo, media_type="application/pdf")


def test_pdf_com_texto_e_lido_com_marcacao_de_pagina():
    doc = extrair_documento(_pdf(pdf_com_texto("Contrato social da empresa exemplo", paginas=2)))
    assert doc.digitalizado is False
    assert doc.paginas == 2
    assert "[Página 1]" in doc.texto and "[Página 2]" in doc.texto
    assert "Contrato social da empresa exemplo" in doc.texto


def test_pdf_sem_texto_e_marcado_como_digitalizado():
    doc = extrair_documento(_pdf(pdf_sem_texto(paginas=3)))
    assert doc.digitalizado is True
    assert doc.paginas == 3
    assert doc.texto == ""


def test_pdf_com_pouco_texto_por_pagina_e_digitalizado():
    doc = extrair_documento(_pdf(pdf_com_texto("x", paginas=3)))
    assert doc.digitalizado is True


def test_imagem_e_sempre_digitalizada():
    doc = extrair_documento(
        ArquivoValidado(nome="cnh.jpg", conteudo=JPEG_MINIMO, media_type="image/jpeg")
    )
    assert (doc.digitalizado, doc.paginas, doc.texto) == (True, 1, "")


def test_pdf_corrompido_vira_erro_de_validacao():
    with pytest.raises(ValidationError, match="não pôde ser aberto"):
        extrair_documento(_pdf(b"%PDF-1.7 lixo sem estrutura", nome="quebrado.pdf"))


def test_pdf_com_senha_vira_erro_de_validacao():
    with pytest.raises(ValidationError, match="protegido por senha"):
        extrair_documento(_pdf(pdf_com_senha(), nome="protegido.pdf"))


# ─── PDF digital com os dados em imagem (CNH digital do Senatran) ────────────

def test_pdf_com_imagem_e_pouco_texto_pede_ocr_complementar():
    doc = extrair_documento(_pdf(pdf_digital_com_imagem(RODAPE_CNH)))
    assert doc.digitalizado is True
    assert doc.ocr_complementar is True
    assert "Assinador Digital" in doc.texto


def test_pdf_com_imagem_e_texto_suficiente_nao_vai_para_o_ocr():
    # Como a última página de um contrato social com a assinatura escaneada.
    doc = extrair_documento(_pdf(pdf_digital_com_imagem("Cláusula do contrato social. " * 30)))
    assert (doc.digitalizado, doc.ocr_complementar) == (False, False)


def test_pdf_com_pouco_texto_e_sem_imagem_continua_digital():
    # Como o print do InfinitePay: ~400 caracteres, nenhuma imagem.
    doc = extrair_documento(_pdf(pdf_com_texto("Banco 0542 Agência 0001 Conta 6479187-5")))
    assert (doc.digitalizado, doc.ocr_complementar) == (False, False)


def test_escaneado_nao_e_ocr_complementar():
    doc = extrair_documento(_pdf(pdf_sem_texto()))
    assert (doc.digitalizado, doc.ocr_complementar) == (True, False)
