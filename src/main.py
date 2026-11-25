"""Montagem da aplicação FastAPI do BFF Reconhecimento de Documentação de Cadastro.

Configuração central (:mod:`src.settings`) validada na carga, logging JSON
sanitizado, CORS restrito ao Hub e um handler único que converte erros do BFF
em HTTP com mensagem genérica (o detalhe técnico fica só no log).
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.controllers.analise_controller import analise_route
from src.controllers.health_check_controller import health_check_route
from src.domain.errors import DomainError
from src.observability.logging import configure_logging, get_logger
from src.settings import AppSettings, load_settings

_logger = get_logger("api")
_MENSAGEM_ERRO_INTERNO = "Erro interno. Tente novamente mais tarde."


async def _domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    fields = {
        "category": exc.category.value,
        "http_status": exc.http_status,
        "path": request.url.path,
    }
    if exc.http_status >= 500:
        _logger.error("request_failed", detail=exc.message, **fields)
    else:
        # Mensagens 4xx são para o usuário e podem citar o nome do arquivo
        # (que pode conter nome de pessoa): não vão para o log.
        _logger.warning("request_rejected", **fields)
    return JSONResponse(status_code=exc.http_status, content={"message": exc.public_message()})


async def _unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    _logger.error("request_unexpected_error", error=exc, path=request.url.path)
    return JSONResponse(status_code=500, content={"message": _MENSAGEM_ERRO_INTERNO})


def create_app(settings: AppSettings) -> FastAPI:
    """Constrói a aplicação FastAPI a partir de uma configuração validada."""
    configure_logging(settings.log_level)
    application = FastAPI(
        title=settings.app_title,
        version=settings.app_version,
        root_path=settings.app_root_path,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_credentials=True,
        allow_origins=list(settings.cors_allowed_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    application.add_exception_handler(DomainError, _domain_error_handler)
    application.add_exception_handler(Exception, _unexpected_error_handler)
    application.include_router(health_check_route)
    application.include_router(analise_route)
    return application


settings = load_settings()
app = create_app(settings)
