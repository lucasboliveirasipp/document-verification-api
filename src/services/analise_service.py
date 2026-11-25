"""Análise síncrona de Alteração Cadastral (mesmo modelo da Curadoria).

A leitura do lote (:func:`ler_lote`) e o diagnóstico também servem ao
Cadastro de Cessionário (:mod:`src.services.cessionario_service`).

validar lote → extrair texto local → OCR dos digitalizados (em paralelo, pelo
caminho B) → Gerador de Texto (JSON) → validar o parecer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from pydantic import ValidationError as PydanticValidationError

from src.domain.errors import ContractViolationError, ValidationError
from src.models.parecer import AnaliseResponse, DocumentoAnalisado, Parecer
from src.observability.logging import get_logger
from src.services import ocr
from src.services.extracao import extrair_documento
from src.services.modalidades import PAPEIS_DOCUMENTO
from src.services.prompts.alteracao_cadastral import SYSTEM_PROMPT, montar_prompt_usuario
from src.services.validacao import ArquivoRecebido, validar_lote
from src.settings import AppSettings

_logger = get_logger("analise")

AVISO_NAO_LIDOS = "{n} documento(s) não puderam ser lidos e não entraram na análise."

#: Rótulo do texto digital (anotação, campo preenchido) que está sobre um
#: escaneado. O OCR não o devolve: no lote real, o "CONJUGES" do Formulário2
#: era uma anotação assim e o parecer não o viu.
ROTULO_TEXTO_DIGITAL = "[TEXTO DIGITAL SOBRE O ESCANEADO: anotações ou campos preenchidos]"
#: Para o PDF digital cujos dados estão em imagem (CNH digital): o OCR traz os
#: dados, a camada de texto traz o resto (rodapé, assinatura).
ROTULO_CAMADA_DE_TEXTO = "[CAMADA DE TEXTO DO PDF]"


@dataclass
class LoteLido:
    """Lote depois da extração local e do OCR, pronto para virar prompt."""

    #: Pares ``(nome, texto)`` dos documentos que têm texto.
    lidos: List[Tuple[str, str]] = field(default_factory=list)
    #: Rótulo do papel de cada documento lido, na mesma ordem.
    rotulos: List[Optional[str]] = field(default_factory=list)
    #: Se o texto de cada documento lido veio do OCR (escaneado ou foto).
    via_ocr: List[bool] = field(default_factory=list)
    nao_lidos: List[str] = field(default_factory=list)
    documentos: List[DocumentoAnalisado] = field(default_factory=list)
    digitalizados: int = 0


def ler_lote(
    arquivos: Sequence[ArquivoRecebido],
    settings: AppSettings,
    leitor_ocr: Optional[ocr.LeitorOcr],
    rotulos_papeis: Mapping[str, str],
) -> LoteLido:
    """Valida o lote, extrai o texto local e manda os digitalizados para o OCR.

    Args:
        rotulos_papeis: rótulo que a IA recebe para cada papel da modalidade.

    Raises:
        ValidationError: lote inválido ou nenhum documento com texto.
    """
    validados = validar_lote(arquivos, settings.upload_limits)
    extraidos = [extrair_documento(arquivo) for arquivo in validados]

    digitalizados = [a for a, e in zip(validados, extraidos) if e.digitalizado]
    leituras = iter(ocr.ler_digitalizados(digitalizados, leitor_ocr, settings.ocr))

    lote = LoteLido(digitalizados=len(digitalizados))
    for validado, extraido in zip(validados, extraidos):
        texto, motivo, via_ocr = extraido.texto, None, False
        if extraido.digitalizado:
            leitura = next(leituras)
            texto, motivo, via_ocr = leitura.texto, leitura.motivo, True
            if not texto.strip() and extraido.ocr_complementar:
                # OCR falhou num PDF que tem texto: fica com o texto digital,
                # como antes do OCR complementar existir.
                texto, motivo, via_ocr = extraido.texto, None, False
            elif texto.strip() and extraido.texto.strip():
                # Só complementa o OCR: a anotação sozinha não faz o documento
                # contar como lido.
                rotulo = (
                    ROTULO_CAMADA_DE_TEXTO if extraido.ocr_complementar else ROTULO_TEXTO_DIGITAL
                )
                texto = f"{texto}\n\n{rotulo}\n{extraido.texto}"
        if texto.strip():
            lote.lidos.append((extraido.nome, texto))
            lote.rotulos.append(rotulos_papeis.get(validado.papel or ""))
            lote.via_ocr.append(via_ocr)
            lote.documentos.append(
                DocumentoAnalisado(
                    nome=extraido.nome, paginas=extraido.paginas, lido=True, papel=validado.papel
                )
            )
        else:
            lote.nao_lidos.append(extraido.nome)
            lote.documentos.append(
                DocumentoAnalisado(
                    nome=extraido.nome,
                    paginas=extraido.paginas,
                    lido=False,
                    motivo=motivo or ocr.MOTIVO_OCR_FALHOU,
                    papel=validado.papel,
                )
            )

    if not lote.lidos:
        raise ValidationError(
            "Nenhum dos documentos enviados pôde ser lido. Envie PDFs com texto selecionável."
        )
    return lote


def analisar_documentos(
    arquivos: Sequence[ArquivoRecebido],
    settings: AppSettings,
    gerador: Any,
    leitor_ocr: Optional[ocr.LeitorOcr] = None,
    *,
    hoje: Optional[date] = None,
) -> AnaliseResponse:
    """Executa a análise completa de um lote e devolve o parecer.

    Args:
        arquivos: arquivos como chegaram no upload.
        settings: configuração validada.
        gerador: objeto com ``gerar_texto`` (``TextGenerationClient`` em produção).
        leitor_ocr: leitor dos digitalizados; sem ele, eles saem como não lidos.
        hoje: data de referência para o modelo; padrão, a data do dia.
    """
    lote = ler_lote(arquivos, settings, leitor_ocr, PAPEIS_DOCUMENTO)

    prompt = montar_prompt_usuario(lote.lidos, lote.nao_lidos, hoje=hoje, papeis=lote.rotulos)
    if len(prompt) > settings.text_generation.max_prompt_chars:
        raise ValidationError(
            "Os documentos enviados excedem o tamanho suportado para uma análise. Divida o lote."
        )

    _logger.info(
        "analise_iniciada",
        documentos=len(lote.documentos),
        digitalizados=lote.digitalizados,
        lidos=len(lote.lidos),
        nao_lidos=len(lote.nao_lidos),
        prompt_chars=len(prompt),
    )
    diagnostico = abrir_diagnostico(settings.diagnostico_dir, SYSTEM_PROMPT, prompt)
    conteudo = gerador.gerar_texto(SYSTEM_PROMPT, prompt, response_format={"type": "json_object"})
    anexar_diagnostico(diagnostico, conteudo)
    parecer = _interpretar_parecer(conteudo)
    _logger.info("analise_concluida", orientacoes=len(parecer.orientacoes))

    avisos = [AVISO_NAO_LIDOS.format(n=len(lote.nao_lidos))] if lote.nao_lidos else []
    return AnaliseResponse(parecer=parecer, documentos=lote.documentos, avisos=avisos)


def _interpretar_parecer(conteudo: str) -> Parecer:
    try:
        return Parecer.model_validate_json(conteudo)
    except PydanticValidationError as exc:
        # Só os nomes dos campos com erro vão para o log, nunca o conteúdo.
        campos = sorted({".".join(str(p) for p in erro["loc"]) for erro in exc.errors()})
        _logger.warning("parecer_fora_do_formato", erros=exc.error_count(), campos=campos)
        raise ContractViolationError("Parecer do modelo fora do formato esperado.") from exc


def abrir_diagnostico(pasta: Optional[str], system_prompt: str, prompt: str) -> Optional[Path]:
    """Grava o prompt antes da chamada, para sobrar mesmo se a IA falhar.

    Mostra o que o OCR entregou à IA; sem isso não se distingue erro de leitura
    de erro de interpretação. Falha ao gravar não interrompe a análise.
    """
    if not pasta:
        return None
    try:
        destino = Path(pasta)
        destino.mkdir(parents=True, exist_ok=True)
        arquivo = destino / f"diagnostico-{datetime.now():%Y%m%d-%H%M%S-%f}.txt"
        arquivo.write_text(
            f"=== SYSTEM PROMPT ===\n{system_prompt}\n\n=== MENSAGEM COM OS DOCUMENTOS ===\n{prompt}\n",
            encoding="utf-8",
        )
    except OSError as exc:
        _logger.warning("diagnostico_nao_gravado", error=type(exc).__name__)
        return None
    _logger.info("diagnostico_gravado", arquivo=arquivo.name)
    return arquivo


def anexar_diagnostico(arquivo: Optional[Path], resposta: str) -> None:
    if arquivo is None:
        return
    try:
        with arquivo.open("a", encoding="utf-8") as saida:
            saida.write(f"\n=== RESPOSTA CRUA DA IA ===\n{resposta}\n")
    except OSError as exc:
        _logger.warning("diagnostico_nao_gravado", error=type(exc).__name__)
