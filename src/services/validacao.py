"""Validação do lote recebido, antes de qualquer leitura ou chamada à Gateway.

O tipo é verificado pelo conteúdo (assinatura do arquivo), não pela extensão.
As mensagens são para o usuário final e podem citar o nome do arquivo que ele
mesmo enviou.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

from src.domain.errors import ValidationError
from src.settings import UploadLimits

_ASSINATURAS = (
    (b"%PDF-", "application/pdf"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)
_CARACTERES_PROIBIDOS = re.compile(r'[\x00-\x1f\x7f<>:"/\\|?*]')
_MAX_NOME = 120


@dataclass(frozen=True)
class ArquivoRecebido:
    """Arquivo como chegou no upload."""

    nome: str
    conteudo: bytes
    #: Papel informado no envio (ver ``modalidades.PAPEIS_DOCUMENTO``).
    papel: Optional[str] = None


@dataclass(frozen=True)
class ArquivoValidado:
    """Arquivo aprovado na validação, com nome seguro e tipo real."""

    nome: str
    conteudo: bytes
    media_type: str
    papel: Optional[str] = None


def nome_seguro(nome: Optional[str]) -> str:
    """Só o nome do arquivo, sem caminho nem caracteres de controle."""
    base = os.path.basename((nome or "").replace("\\", "/"))
    base = _CARACTERES_PROIBIDOS.sub("_", base).strip(" .")
    return base[:_MAX_NOME] or "documento"


def detectar_media_type(conteudo: bytes) -> Optional[str]:
    """Tipo de mídia pela assinatura do conteúdo, ou ``None`` se desconhecido."""
    for assinatura, media_type in _ASSINATURAS:
        if conteudo.startswith(assinatura):
            return media_type
    return None


def validar_quantidade(quantidade: int, limites: UploadLimits) -> None:
    """Recusa o lote pela quantidade, antes de ler qualquer arquivo."""
    if quantidade > limites.max_files:
        raise ValidationError(
            f"É permitido enviar no máximo {limites.max_files} documentos por análise."
        )


def validar_lote(
    arquivos: Sequence[ArquivoRecebido], limites: UploadLimits
) -> List[ArquivoValidado]:
    """Valida quantidade, tamanho e tipo real de cada arquivo."""
    if not arquivos:
        raise ValidationError("Envie ao menos um documento.")
    validar_quantidade(len(arquivos), limites)

    validados: List[ArquivoValidado] = []
    for arquivo in arquivos:
        nome = nome_seguro(arquivo.nome)
        tamanho = len(arquivo.conteudo)
        if tamanho == 0:
            raise ValidationError(f"O documento '{nome}' está vazio.")
        if tamanho > limites.max_file_size_bytes:
            limite_mb = limites.max_file_size_bytes // (1024 * 1024)
            raise ValidationError(f"O documento '{nome}' excede o limite de {limite_mb} MB.")
        media_type = detectar_media_type(arquivo.conteudo)
        if media_type is None or media_type not in limites.allowed_media_types:
            raise ValidationError(f"O documento '{nome}' não é um PDF, JPEG ou PNG válido.")
        validados.append(
            ArquivoValidado(
                nome=nome, conteudo=arquivo.conteudo, media_type=media_type, papel=arquivo.papel
            )
        )
    return validados
