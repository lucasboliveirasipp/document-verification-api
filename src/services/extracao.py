"""Extração local de texto (sem IA e sem custo), como na Curadoria.

PDF com camada de texto é lido com PyMuPDF. PDF digitalizado e imagens são
marcados como ``digitalizado`` e seguem para :mod:`src.services.ocr`. O PDF com
imagem e pouco texto numa página (a CNH digital do Senatran: só o rodapé da
assinatura é texto, os dados estão em imagem) também vai para o OCR, que
complementa a camada de texto.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

import fitz  # PyMuPDF

from src.domain.errors import ValidationError
from src.services.validacao import ArquivoValidado

#: Média mínima de caracteres por página para o PDF contar como legível.
#: Mesmo limiar da Curadoria (abaixo disso o PDF é tratado como escaneado).
MIN_CHARS_POR_PAGINA = 20

#: Página com imagem e menos texto que isso pede OCR complementar. A CNH
#: digital tem ~445 caracteres (só o rodapé); a página de contrato social com
#: assinatura escaneada, a de menos texto entre os exemplos, tem 850.
MAX_CHARS_PAGINA_COM_IMAGEM = 600


@dataclass(frozen=True)
class DocumentoExtraido:
    """Resultado da leitura local de um arquivo."""

    nome: str
    media_type: str
    paginas: int
    texto: str
    digitalizado: bool
    #: Tem camada de texto aproveitável, mas alguma página pede OCR (imagem e
    #: pouco texto). Se o OCR falhar, o texto digital ainda serve.
    ocr_complementar: bool = False


def extrair_documento(arquivo: ArquivoValidado) -> DocumentoExtraido:
    """Extrai o texto de um PDF; imagens voltam vazias e marcadas como digitalizadas."""
    if arquivo.media_type != "application/pdf":
        return DocumentoExtraido(
            nome=arquivo.nome, media_type=arquivo.media_type, paginas=1, texto="",
            digitalizado=True,
        )
    try:
        doc = fitz.open(stream=arquivo.conteudo, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - PyMuPDF levanta tipos variados
        raise ValidationError(
            f"O documento '{arquivo.nome}' não pôde ser aberto como PDF."
        ) from exc

    with doc:
        if doc.needs_pass:
            raise ValidationError(f"O documento '{arquivo.nome}' está protegido por senha.")
        partes: List[str] = []
        total_chars = 0
        pagina_com_imagem_e_pouco_texto = False
        for numero, pagina in enumerate(doc, start=1):
            texto = pagina.get_text().strip()
            total_chars += len(texto)
            if len(texto) < MAX_CHARS_PAGINA_COM_IMAGEM and pagina.get_images():
                pagina_com_imagem_e_pouco_texto = True
            if texto:
                partes.append(f"[Página {numero}]\n{texto}")
        paginas = doc.page_count

    escaneado = total_chars < MIN_CHARS_POR_PAGINA * max(paginas, 1)
    complementar = not escaneado and pagina_com_imagem_e_pouco_texto
    return DocumentoExtraido(
        nome=arquivo.nome,
        media_type=arquivo.media_type,
        paginas=paginas,
        texto="\n\n".join(partes),
        digitalizado=escaneado or complementar,
        ocr_complementar=complementar,
    )
