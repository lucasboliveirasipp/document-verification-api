"""Análise síncrona de Cadastro de Cessionário.

ler a planilha → validar e ler os documentos (extração local + OCR, como na
Alteração) → Gerador de Texto (JSON) → validar o relatório. A planilha é lida
pelo BFF e nunca vai para o OCR.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional, Sequence

from pydantic import ValidationError as PydanticValidationError

from src.domain.errors import ContractViolationError, ValidationError
from src.models.cessionario import CessionarioResponse, RelatorioCessionario
from src.observability.logging import get_logger
from src.services import ocr
from src.services.analise_service import (
    AVISO_NAO_LIDOS,
    abrir_diagnostico,
    anexar_diagnostico,
    ler_lote,
)
from src.services.modalidades import PAPEIS_CESSIONARIO
from src.services.planilha import ler_planilha
from src.services.prompts.cadastro_cessionario import SYSTEM_PROMPT, montar_prompt_usuario
from src.services.validacao import ArquivoRecebido, nome_seguro
from src.settings import AppSettings

_logger = get_logger("cessionario")

AVISO_REGISTROS_FALTANDO = (
    "A planilha tem {esperados} cessionário(s), mas a análise devolveu {recebidos}. "
    "Confira os que faltam manualmente ou refaça a análise."
)


def analisar_cessionario(
    planilha: ArquivoRecebido,
    arquivos: Sequence[ArquivoRecebido],
    settings: AppSettings,
    gerador: Any,
    leitor_ocr: Optional[ocr.LeitorOcr] = None,
    *,
    hoje: Optional[date] = None,
) -> CessionarioResponse:
    """Valida os cessionários da planilha contra os documentos anexos.

    Args:
        planilha: o XLSX de cadastro, recebido em campo próprio.
        arquivos: documentos dos cessionários (PDF, JPEG ou PNG).
        settings: configuração validada.
        gerador: objeto com ``gerar_texto`` (``TextGenerationClient`` em produção).
        leitor_ocr: leitor dos digitalizados; sem ele, eles saem como não lidos.
        hoje: data de referência para o modelo; padrão, a data do dia.
    """
    nome_planilha = nome_seguro(planilha.nome)
    if len(planilha.conteudo) > settings.upload_limits.max_file_size_bytes:
        limite_mb = settings.upload_limits.max_file_size_bytes // (1024 * 1024)
        raise ValidationError(f"A planilha '{nome_planilha}' excede o limite de {limite_mb} MB.")
    registros = ler_planilha(nome_planilha, planilha.conteudo)
    lote = ler_lote(arquivos, settings, leitor_ocr, PAPEIS_CESSIONARIO)

    prompt = montar_prompt_usuario(
        registros,
        lote.lidos,
        lote.nao_lidos,
        via_ocr=lote.via_ocr,
        papeis=lote.rotulos,
        hoje=hoje,
    )
    if len(prompt) > settings.text_generation.max_prompt_chars:
        raise ValidationError(
            "Os documentos enviados excedem o tamanho suportado para uma análise. Divida o lote."
        )

    _logger.info(
        "cessionario_iniciado",
        registros=len(registros),
        documentos=len(lote.documentos),
        digitalizados=lote.digitalizados,
        lidos=len(lote.lidos),
        nao_lidos=len(lote.nao_lidos),
        prompt_chars=len(prompt),
    )
    diagnostico = abrir_diagnostico(settings.diagnostico_dir, SYSTEM_PROMPT, prompt)
    conteudo = gerador.gerar_texto(SYSTEM_PROMPT, prompt, response_format={"type": "json_object"})
    anexar_diagnostico(diagnostico, conteudo)
    relatorio = _interpretar_relatorio(conteudo)

    avisos = []
    if lote.nao_lidos:
        avisos.append(AVISO_NAO_LIDOS.format(n=len(lote.nao_lidos)))
    if len(relatorio.registros) != len(registros):
        avisos.append(
            AVISO_REGISTROS_FALTANDO.format(
                esperados=len(registros), recebidos=len(relatorio.registros)
            )
        )
    resposta = CessionarioResponse(
        relatorio=relatorio,
        documentos=lote.documentos,
        registros_planilha=len(registros),
        avisos=avisos,
    )
    resumo = resposta.resumo
    _logger.info(
        "cessionario_concluido",
        registros=resumo.total_registros,
        reprovados=resumo.reprovados,
        alto_risco=resumo.registros_alto_risco,
    )
    return resposta


def _interpretar_relatorio(conteudo: str) -> RelatorioCessionario:
    try:
        return RelatorioCessionario.model_validate_json(conteudo)
    except PydanticValidationError as exc:
        # Só os nomes dos campos com erro vão para o log, nunca o conteúdo.
        campos = sorted({".".join(str(p) for p in erro["loc"]) for erro in exc.errors()})
        _logger.warning("relatorio_fora_do_formato", erros=exc.error_count(), campos=campos)
        raise ContractViolationError("Relatório do modelo fora do formato esperado.") from exc
