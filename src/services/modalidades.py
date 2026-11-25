"""Modalidades de análise habilitadas neste agente.

A modalidade é escolhida pelo usuário no Hub e chega em cada requisição. Cada
modalidade tem prompt, schema e relatório próprios, sem misturar regras.
``/analisar_documentos`` atende a Alteração Cadastral; o Cadastro de
Cessionário tem rota própria (``/analisar_cessionario``), porque recebe a
planilha à parte e devolve outro relatório.
"""
from __future__ import annotations

from typing import List, Mapping, Optional, Sequence

from src.domain.errors import ValidationError
from src.models.cessionario import CADASTRO_CESSIONARIO  # noqa: F401 - reexportado

ALTERACAO_CADASTRAL = "alteracao_cadastral"

MODALIDADES_HABILITADAS = frozenset({ALTERACAO_CADASTRAL})


def validar_modalidade(codigo: str) -> str:
    """Devolve o código se a modalidade estiver habilitada; senão recusa."""
    if codigo not in MODALIDADES_HABILITADAS:
        raise ValidationError("Modalidade não habilitada neste agente.")
    return codigo


#: Papel de cada arquivo, vindo do campo de upload da tela do Hub, e o rótulo
#: que a IA recebe. O papel orienta a leitura; o conteúdo continua mandando.
PAPEIS_DOCUMENTO = {
    "identificacao": "Documento de identificação de representante",
    "formulario": "Formulário de alteração cadastral",
    "contrato_social": "Contrato social ou ata de assembleia",
    "procuracao": "Procuração",
    "reconhecimento_cartorio": "Reconhecimento em cartório",
}


#: Papéis dos campos de upload da aba Cessionário. A planilha vem em campo
#: próprio e não entra aqui.
PAPEIS_CESSIONARIO = {
    "identificacao": "Documento de identificação do cessionário (RG, CNH ou passaporte)",
    "comprovante_endereco": "Comprovante de endereço",
    "comprovante_bancario": "Comprovante de dados bancários",
    "autorizacao": "Autorização ou declaração assinada",
}


def validar_papeis(
    papeis: Optional[Sequence[str]],
    quantidade: int,
    catalogo: Mapping[str, str] = PAPEIS_DOCUMENTO,
) -> List[Optional[str]]:
    """Um papel do ``catalogo`` por arquivo, na ordem dos arquivos; sem papéis, ``None``."""
    # O Swagger, com "Send empty value", manda papeis="" (lista com um vazio).
    if not papeis or all(not papel for papel in papeis):
        return [None] * quantidade
    if len(papeis) != quantidade:
        raise ValidationError("Informe um tipo de documento para cada arquivo enviado.")
    if any(papel not in catalogo for papel in papeis):
        raise ValidationError("Tipo de documento não reconhecido.")
    return list(papeis)
