"""Testes da validação de lote (antes de qualquer leitura ou chamada externa)."""
from __future__ import annotations

import pytest

from src.domain.errors import ValidationError
from src.services.validacao import (
    ArquivoRecebido,
    detectar_media_type,
    nome_seguro,
    validar_lote,
    validar_quantidade,
)
from src.settings import UploadLimits
from tests.unit.pdf_fakes import JPEG_MINIMO

_LIMITES = UploadLimits(max_files=2, max_file_size_bytes=1024 * 1024)
_PDF = b"%PDF-1.7\n..."


@pytest.mark.parametrize(
    "nome, esperado",
    [
        ("formulario.pdf", "formulario.pdf"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\x\\cnh.pdf", "cnh.pdf"),
        ("a<b>:c.pdf", "a_b__c.pdf"),
        ("", "documento"),
        (None, "documento"),
        ("x" * 300 + ".pdf", "x" * 120),
    ],
)
def test_nome_seguro(nome, esperado):
    assert nome_seguro(nome) == esperado


@pytest.mark.parametrize(
    "conteudo, esperado",
    [
        (_PDF, "application/pdf"),
        (JPEG_MINIMO, "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n...", "image/png"),
        (b"PK\x03\x04 planilha", None),
    ],
)
def test_detectar_media_type_by_content(conteudo, esperado):
    assert detectar_media_type(conteudo) == esperado


def test_validar_quantidade_rejeita_excesso():
    with pytest.raises(ValidationError, match="no máximo 2"):
        validar_quantidade(3, _LIMITES)


def test_validar_lote_aceita_pdf_e_imagem():
    validados = validar_lote(
        [ArquivoRecebido("a.pdf", _PDF), ArquivoRecebido("b.jpg", JPEG_MINIMO)], _LIMITES
    )
    assert [(v.nome, v.media_type) for v in validados] == [
        ("a.pdf", "application/pdf"),
        ("b.jpg", "image/jpeg"),
    ]


def test_validar_lote_vazio():
    with pytest.raises(ValidationError, match="ao menos um"):
        validar_lote([], _LIMITES)


def test_validar_lote_arquivo_vazio():
    with pytest.raises(ValidationError, match="está vazio"):
        validar_lote([ArquivoRecebido("a.pdf", b"")], _LIMITES)


def test_validar_lote_arquivo_grande():
    grande = _PDF + b"0" * (1024 * 1024)
    with pytest.raises(ValidationError, match="excede o limite de 1 MB"):
        validar_lote([ArquivoRecebido("a.pdf", grande)], _LIMITES)


def test_validar_lote_tipo_real_invalido_mesmo_com_extensao_pdf():
    with pytest.raises(ValidationError, match="não é um PDF"):
        validar_lote([ArquivoRecebido("planilha.pdf", b"PK\x03\x04")], _LIMITES)


def test_validar_lote_respeita_tipos_configurados():
    so_pdf = UploadLimits(
        max_files=2, max_file_size_bytes=1024, allowed_media_types=("application/pdf",)
    )
    with pytest.raises(ValidationError):
        validar_lote([ArquivoRecebido("b.jpg", JPEG_MINIMO)], so_pdf)
