"""PDFs e imagens sintéticos para testes (nenhum dado real)."""
from __future__ import annotations

import struct
from typing import Optional

import fitz

#: Cabeçalho mínimo de JPEG: basta para a detecção por assinatura.
JPEG_MINIMO = b"\xff\xd8\xff\xe0" + b"\x00" * 16


def _pixmap(largura: int, altura: int) -> fitz.Pixmap:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, largura, altura), False)
    pix.clear_with(200)
    return pix


def jpeg(largura: int = 200, altura: int = 100, orientacao: Optional[int] = None) -> bytes:
    """JPEG que abre de verdade; ``orientacao`` grava a tag EXIF, como no celular."""
    data = _pixmap(largura, altura).tobytes("jpeg")
    if orientacao is None:
        return data
    # Segmento APP1 com um TIFF big-endian de uma entrada: Orientation (0x0112).
    tiff = (
        b"MM\x00\x2a" + struct.pack(">I", 8) + struct.pack(">H", 1)
        + struct.pack(">HHIHH", 0x0112, 3, 1, orientacao, 0) + struct.pack(">I", 0)
    )
    app1 = b"Exif\x00\x00" + tiff
    return data[:2] + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + data[2:]


def png(largura: int = 200, altura: int = 100) -> bytes:
    return _pixmap(largura, altura).tobytes("png")


def pdf_com_texto(texto: str, paginas: int = 1) -> bytes:
    doc = fitz.open()
    for _ in range(paginas):
        doc.new_page().insert_text((72, 72), texto)
    data = doc.tobytes()
    doc.close()
    return data


def pdf_sem_texto(paginas: int = 2) -> bytes:
    """PDF só com desenho, como um documento escaneado."""
    doc = fitz.open()
    for _ in range(paginas):
        doc.new_page().draw_rect(fitz.Rect(50, 50, 200, 200))
    data = doc.tobytes()
    doc.close()
    return data


def pdf_com_senha() -> bytes:
    """PDF criptografado que exige senha para abrir."""
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Documento protegido de exemplo")
    data = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="dono", user_pw="leitor")
    doc.close()
    return data


def pdf_escaneado_com_anotacao(anotacao: str, paginas: int = 2) -> bytes:
    """Escaneado com uma caixa de texto digital por cima, como o Formulário2 real."""
    doc = fitz.open()
    for _ in range(paginas):
        doc.new_page().draw_rect(fitz.Rect(50, 50, 200, 200))
    doc[0].add_freetext_annot(fitz.Rect(300, 300, 450, 330), anotacao)
    data = doc.tobytes()
    doc.close()
    return data


def pdf_digital_com_imagem(texto: str) -> bytes:
    """Texto digital e uma imagem na página, como a CNH digital do Senatran.

    Na CNH real a camada de texto é só o rodapé da assinatura (~445
    caracteres); nome, CPF e nascimento estão nas imagens.
    """
    doc = fitz.open()
    pagina = doc.new_page()
    pagina.insert_textbox(fitz.Rect(72, 400, 520, 800), texto)
    pagina.insert_image(fitz.Rect(72, 72, 300, 300), stream=png())
    data = doc.tobytes()
    doc.close()
    return data
