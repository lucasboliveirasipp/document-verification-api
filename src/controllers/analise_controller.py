"""Rotas da análise documental (fluxo síncrono).

A modalidade vem do Hub no campo de formulário ``modalidade``; sem ele, vale
Alteração Cadastral, para o front atual continuar funcionando. O Cadastro de
Cessionário tem rotas próprias, porque recebe a planilha em campo separado e
devolve outro relatório. O controller só
valida a modalidade, lê o upload e delega. A análise roda em threadpool porque o
serviço é bloqueante (extração, OCR e polling da Gateway).
"""
from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from src.adapters.gateway.service_auth import AzureAdServiceAuthAdapter
from src.adapters.gateway.text_generation import TextGenerationClient
from src.adapters.gateway.transport import GatewayTransport
from src.models.cessionario import CessionarioResponse
from src.models.parecer import AnaliseResponse
from src.services.analise_service import analisar_documentos
from src.services.cessionario_service import analisar_cessionario
from src.services.modalidades import (
    ALTERACAO_CADASTRAL,
    PAPEIS_CESSIONARIO,
    validar_modalidade,
    validar_papeis,
)
from src.services.ocr import LeitorOcr
from src.services.relatorio_cessionario_pdf import gerar_relatorio_cessionario_pdf
from src.services.relatorio_pdf import gerar_relatorio_pdf
from src.services.validacao import ArquivoRecebido, validar_quantidade
from src.settings import AppSettings, load_settings

analise_route = APIRouter(tags=["Análise documental"])


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Configuração validada, carregada uma vez por processo."""
    return load_settings()


@lru_cache(maxsize=1)
def get_transport() -> GatewayTransport:
    """Transporte único por processo: reaproveita conexões e o token em cache."""
    settings = get_settings()
    return GatewayTransport(settings, service_auth=AzureAdServiceAuthAdapter(settings))


@lru_cache(maxsize=1)
def get_gerador() -> TextGenerationClient:
    """Gerador de Texto sobre o transporte único."""
    return TextGenerationClient(get_settings(), get_transport())


@lru_cache(maxsize=1)
def get_leitor_ocr() -> LeitorOcr:
    """Leitor dos digitalizados sobre o mesmo transporte do Gerador de Texto."""
    return LeitorOcr(get_settings(), get_transport())


@analise_route.post(
    "/analisar_documentos",
    response_model=AnaliseResponse,
    summary="Analisa um lote de Alteração Cadastral e devolve o parecer estruturado",
)
async def api_analisar_documentos(
    files: List[UploadFile] = File(...),
    modalidade: str = Form(ALTERACAO_CADASTRAL),
    papeis: Optional[List[str]] = Form(None),
    settings: AppSettings = Depends(get_settings),
    gerador: TextGenerationClient = Depends(get_gerador),
    leitor_ocr: LeitorOcr = Depends(get_leitor_ocr),
) -> AnaliseResponse:
    validar_modalidade(modalidade)
    limites = settings.upload_limits
    validar_quantidade(len(files), limites)
    papeis_validos = validar_papeis(papeis, len(files))
    arquivos = []
    for arquivo, papel in zip(files, papeis_validos):
        # Lê no máximo limite + 1 byte: o excedente já basta para recusar.
        conteudo = await arquivo.read(limites.max_file_size_bytes + 1)
        arquivos.append(
            ArquivoRecebido(nome=arquivo.filename or "", conteudo=conteudo, papel=papel)
        )
    return await run_in_threadpool(analisar_documentos, arquivos, settings, gerador, leitor_ocr)


@analise_route.post(
    "/relatorio_pdf",
    response_class=Response,
    summary="Gera o relatório PDF de uma análise já feita",
    responses={200: {"content": {"application/pdf": {}}}},
)
def api_relatorio_pdf(analise: AnaliseResponse) -> Response:
    validar_modalidade(analise.modalidade)
    return Response(
        content=gerar_relatorio_pdf(analise),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="analise-alteracao-cadastral.pdf"'},
    )


@analise_route.post(
    "/analisar_cessionario",
    response_model=CessionarioResponse,
    summary="Valida os cessionários da planilha contra os documentos anexos",
)
async def api_analisar_cessionario(
    planilha: UploadFile = File(..., description="Planilha de cadastro (.xlsx)"),
    files: List[UploadFile] = File(..., description="Documentos dos cessionários"),
    papeis: Optional[List[str]] = Form(None),
    settings: AppSettings = Depends(get_settings),
    gerador: TextGenerationClient = Depends(get_gerador),
    leitor_ocr: LeitorOcr = Depends(get_leitor_ocr),
) -> CessionarioResponse:
    limites = settings.upload_limits
    validar_quantidade(len(files), limites)
    papeis_validos = validar_papeis(papeis, len(files), PAPEIS_CESSIONARIO)
    arquivos = []
    for arquivo, papel in zip(files, papeis_validos):
        conteudo = await arquivo.read(limites.max_file_size_bytes + 1)
        arquivos.append(
            ArquivoRecebido(nome=arquivo.filename or "", conteudo=conteudo, papel=papel)
        )
    xlsx = ArquivoRecebido(
        nome=planilha.filename or "", conteudo=await planilha.read(limites.max_file_size_bytes + 1)
    )
    return await run_in_threadpool(
        analisar_cessionario, xlsx, arquivos, settings, gerador, leitor_ocr
    )


@analise_route.post(
    "/relatorio_cessionario_pdf",
    response_class=Response,
    summary="Gera o relatório PDF de uma validação de cessionários já feita",
    responses={200: {"content": {"application/pdf": {}}}},
)
def api_relatorio_cessionario_pdf(analise: CessionarioResponse) -> Response:
    return Response(
        content=gerar_relatorio_cessionario_pdf(analise),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="validacao-cessionarios.pdf"'},
    )
