"""Configuração central, tipada e validada na inicialização.

Este módulo é o **mecanismo único** de leitura de configuração do BFF. Ele
transforma variáveis de ambiente (valores não sensíveis) em objetos tipados e
imutáveis, validando-os na carga. Se uma configuração obrigatória estiver
ausente ou inconsistente, a carga **falha fechada** com um erro de domínio
sanitizado, antes de a aplicação aceitar operações de negócio (Requisito 4.5).

Princípios respeitados:

* nenhum valor real de contrato da Gateway (URL, container, código de
  workflow/step) é embutido: os padrões existentes são apenas parâmetros
  operacionais neutros (timeouts, tentativas, limites);
* nenhum segredo (tenant, client secret, chave) é lido ou transportado aqui;
  segredos pertencem ao cofre/pipeline e a adaptadores específicos;
* CORS nunca aceita o coringa ``*``; a ausência de origens nega por padrão;
* TLS é validado por padrão e ``verify=False`` é proibido em HML/PRD;
* mensagens de erro citam apenas o **nome** de uma chave não sensível, nunca
  seu valor, conteúdo documental, PII ou segredo.

Os identificadores de recurso da Gateway (container/códigos/endpoints) são lidos
como **opcionais** aqui, pois dependem de homologação. Cada adaptador falha
fechado (``CapabilityUnavailableError``) quando o valor de que precisa não existe.
"""
from __future__ import annotations

# Carrega variáveis do arquivo .env mais próximo (pasta do projeto)
def _carregar_env():
    from pathlib import Path
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for pasta in Path(__file__).resolve().parents:
        if (pasta / ".env").exists():
            load_dotenv(pasta / ".env")
            break


_carregar_env()


import enum
import os
from dataclasses import dataclass, field
from typing import Mapping, Optional, Tuple

from src.domain.errors import ValidationError


class Environment(str, enum.Enum):
    """Ambiente de execução reconhecido."""

    DSV = "DSV"
    HML = "HML"
    PRD = "PRD"
    TEST = "TEST"

    @property
    def is_production_like(self) -> bool:
        """Ambientes onde exigências de segurança são inegociáveis."""
        return self in (Environment.HML, Environment.PRD)


# ─── Parsers auxiliares (puros, com mensagens sanitizadas) ────────────────────

def _get_raw(env: Mapping[str, str], key: str) -> Optional[str]:
    value = env.get(key)
    if value is None:
        return None
    value = value.strip()
    return value or None


def parse_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    """Interpreta um booleano textual, aceitando formas usuais.

    Raises:
        ValidationError: quando o valor presente não é reconhecível.
    """
    raw = _get_raw(env, key)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    raise ValidationError(f"Valor booleano invalido para {key}.")


def parse_int(
    env: Mapping[str, str], key: str, default: int, *, minimum: Optional[int] = None
) -> int:
    """Interpreta um inteiro, aplicando limite mínimo opcional."""
    raw = _get_raw(env, key)
    if raw is None:
        value = default
    else:
        try:
            value = int(raw)
        except ValueError:
            raise ValidationError(f"Valor inteiro invalido para {key}.")
    if minimum is not None and value < minimum:
        raise ValidationError(f"Valor de {key} deve ser >= {minimum}.")
    return value


def parse_float(
    env: Mapping[str, str],
    key: str,
    default: float,
    *,
    minimum: Optional[float] = None,
) -> float:
    """Interpreta um número real, aplicando limite mínimo opcional."""
    raw = _get_raw(env, key)
    if raw is None:
        value = default
    else:
        try:
            value = float(raw)
        except ValueError:
            raise ValidationError(f"Valor numerico invalido para {key}.")
    if minimum is not None and value < minimum:
        raise ValidationError(f"Valor de {key} deve ser >= {minimum}.")
    return value


def parse_csv(env: Mapping[str, str], key: str) -> Tuple[str, ...]:
    """Extrai itens de uma lista separada por vírgulas, sem itens vazios."""
    raw = env.get(key, "") or ""
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def parse_allowed_origins(raw: Optional[str]) -> Tuple[str, ...]:
    """Extrai origens de CORS, descartando vazios e rejeitando o coringa ``*``.

    O coringa é silenciosamente ignorado (nunca promovido), preservando a
    política de negar por padrão em vez de abrir o CORS.
    """
    origins = []
    for item in (raw or "").split(","):
        origin = item.strip()
        if not origin or origin == "*":
            continue
        origins.append(origin)
    return tuple(origins)


# ─── Grupos de configuração tipados ───────────────────────────────────────────

@dataclass(frozen=True)
class TLSSettings:
    """Política de validação de TLS para chamadas de saída.

    Attributes:
        verify: exige validação de certificado. Padrão ``True``.
        ca_bundle_path: caminho para a CA corporativa, quando necessário. Valor
            não sensível (apenas um caminho), pode ser ausente.
    """

    verify: bool = True
    ca_bundle_path: Optional[str] = None

    @property
    def requests_verify(self):
        """Valor para o ``verify`` do requests: CA bundle tem precedência."""
        return self.ca_bundle_path or self.verify

    def validate_for(self, environment: Environment) -> None:
        """Impede TLS relaxado em ambientes produtivos.

        Raises:
            ValidationError: quando ``verify`` é ``False`` em HML/PRD.
        """
        if environment.is_production_like and not self.verify:
            raise ValidationError(
                "TLS invalido: verificacao de certificado nao pode ser "
                "desabilitada neste ambiente."
            )


@dataclass(frozen=True)
class TimeoutSettings:
    """Timeouts explícitos das chamadas de saída (segundos)."""

    connect_seconds: float = 5.0
    read_seconds: float = 30.0


@dataclass(frozen=True)
class RetrySettings:
    """Política de retry para falhas transitórias e operações idempotentes."""

    max_attempts: int = 3
    backoff_base_seconds: float = 0.5
    backoff_max_seconds: float = 8.0


@dataclass(frozen=True)
class TokenCacheSettings:
    """Reuso do token de serviço até margem segura antes da expiração."""

    safety_margin_seconds: float = 60.0


@dataclass(frozen=True)
class PollingSettings:
    """Acompanhamento do workflow (valores calibrados em produção pela Curadoria).

    ``not_found_grace_seconds``: janela em que um 404 no status é tratado como
    "execução ainda não registrada" (doc da Gateway); depois disso, é erro.
    """

    interval_seconds: float = 2.0
    timeout_seconds: float = 180.0
    not_found_grace_seconds: float = 30.0


@dataclass(frozen=True)
class OcrSettings:
    """Leitura de digitalizados pelo caminho B (File Manager + workflow de OCR).

    O fluxo é síncrono: os documentos são lidos em paralelo e o orçamento
    limita o tempo total do OCR numa análise, para não estourar o timeout do
    front. O intervalo segue a doc da Gateway (nunca abaixo de 3 s); o timeout
    do Gerador de Texto continua em :class:`PollingSettings`.
    """

    max_parallel: int = 4
    polling_interval_seconds: float = 5.0
    timeout_per_document_seconds: float = 120.0
    budget_seconds: float = 150.0


@dataclass(frozen=True)
class UploadLimits:
    """Limites de lote aplicados antes de qualquer upload.

    Os valores são operacionais e configuráveis; não são contrato da Gateway.
    Os tipos aceitos são verificados pelo BFF (MIME real) na validação de lote.
    """

    max_files: int = 10
    max_file_size_bytes: int = 20 * 1024 * 1024
    allowed_media_types: Tuple[str, ...] = (
        "application/pdf",
        "image/jpeg",
        "image/png",
    )


@dataclass(frozen=True)
class ServiceIdentitySettings:
    """Nomes/parâmetros NÃO sensíveis da identidade de serviço (G1).

    O contrato é o observado no projeto de referência
    ``api-agente-orientador-curadoria``, que autentica por
    ``client_credentials`` no **Azure AD** (não na Gateway) e usa o token
    resultante como ``Authorization: Bearer`` nas chamadas à plataforma.

    Nada aqui é segredo: ``tenant_id``, ``client_id`` e ``scope_uri`` são
    identificadores de configuração. O ``client_secret`` **não** pertence a
    este objeto — é lido apenas no momento da requisição de token, pelo
    adaptador, direto do ambiente/cofre, e nunca é transportado ou registrado.
    """

    tenant_id: Optional[str] = None
    client_id: Optional[str] = None
    scope_uri: Optional[str] = None
    #: Template do endpoint de token. Parametrizado para permitir nuvem
    #: soberana/ambiente distinto sem alterar código.
    token_endpoint_template: str = (
        "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    )

    @property
    def is_complete(self) -> bool:
        return all(bool(v) for v in (self.tenant_id, self.client_id, self.scope_uri))

    def token_endpoint(self) -> Optional[str]:
        """Monta o endpoint de token, ou ``None`` se o tenant não existir."""
        if not self.tenant_id:
            return None
        return self.token_endpoint_template.format(tenant_id=self.tenant_id)

    def scope(self) -> Optional[str]:
        """Monta o scope no formato ``{uri}/.default``, como no contrato real.

        Aceita a URI já com ``/.default``: duplicado, o Azure AD recusa.
        """
        if not self.scope_uri:
            return None
        uri = self.scope_uri.rstrip("/")
        if uri.endswith("/.default"):
            return uri
        return f"{uri}/.default"


@dataclass(frozen=True)
class GatewayEndpoints:
    """URLs APIM por ambiente. Opcionais até homologação (G1/G2/G3)."""

    workflow_url: Optional[str] = None
    status_url: Optional[str] = None
    file_manager_url: Optional[str] = None

    @property
    def is_complete(self) -> bool:
        return all(
            bool(v)
            for v in (self.workflow_url, self.status_url, self.file_manager_url)
        )


@dataclass(frozen=True)
class GatewayResourceCodes:
    """Identificadores de contrato da Gateway. Opcionais até homologação (G2/G3).

    São mantidos como opacos e opcionais aqui; a obrigatoriedade é imposta no
    momento de construir a capacidade documental, que falha fechada quando
    algum estiver ausente.
    """

    container_id: Optional[str] = None
    workflow_code: Optional[str] = None
    step_workflow_code: Optional[str] = None

    @property
    def is_complete(self) -> bool:
        return all(
            bool(v)
            for v in (self.container_id, self.workflow_code, self.step_workflow_code)
        )


@dataclass(frozen=True)
class TextGenerationSettings:
    """Workflow "Gerador de Texto" (contrato da Curadoria).

    Os campos ``openai_api_*`` são exigidos pelo payload do workflow; a chave é
    um placeholder (a autenticação real é o Bearer do Azure AD).
    """

    workflow_code: Optional[str] = None
    model: Optional[str] = None
    openai_api_endpoint: Optional[str] = None
    openai_api_version: Optional[str] = None
    openai_api_key: Optional[str] = None
    max_tokens: int = 4096
    max_prompt_chars: int = 60000

    @property
    def is_complete(self) -> bool:
        return all(
            bool(v)
            for v in (
                self.workflow_code,
                self.model,
                self.openai_api_endpoint,
                self.openai_api_version,
                self.openai_api_key,
            )
        )


@dataclass(frozen=True)
class AppSettings:
    """Configuração completa e validada da aplicação."""

    environment: Environment
    app_title: str
    app_root_path: str
    app_version: str
    cors_allowed_origins: Tuple[str, ...]
    tls: TLSSettings
    timeouts: TimeoutSettings
    retry: RetrySettings
    token_cache: TokenCacheSettings
    polling: PollingSettings
    ocr: OcrSettings
    upload_limits: UploadLimits
    gateway_endpoints: GatewayEndpoints
    gateway_resources: GatewayResourceCodes
    service_identity: ServiceIdentitySettings
    text_generation: TextGenerationSettings
    #: Modo de conexão enviado como query param nas chamadas à Gateway.
    #:
    #: Divergência conhecida entre os projetos de referência: o de Ouvidoria
    #: envia ``connection_mode`` em todas as chamadas; o de Curadoria (mais
    #: recente, publicado e funcional) **não envia em nenhuma**. Como a
    #: Curadoria é a referência autoritativa, o padrão é **não enviar**;
    #: definir ``GATEWAY_CONNECTION_MODE`` reativa o parâmetro sem alterar
    #: código, caso a homologação confirme que é exigido.
    gateway_connection_mode: Optional[str] = None
    #: Nível mínimo de log (``LOG_LEVEL``).
    log_level: str = "INFO"
    #: Pasta onde cada análise grava o prompt enviado à IA e a resposta crua
    #: (``DIAGNOSTICO_DIR``). Só para dev local: contém PII e é recusada fora
    #: de DSV/TEST.
    diagnostico_dir: Optional[str] = None


def load_settings(env: Optional[Mapping[str, str]] = None) -> AppSettings:
    """Carrega e valida a configuração a partir do ambiente.

    Args:
        env: mapeamento de variáveis (padrão: ``os.environ``). Injetável para
            testes determinísticos.

    Returns:
        Configuração tipada, imutável e validada.

    Raises:
        src.domain.errors.ValidationError: quando algum valor presente é
            inválido ou uma regra de segurança (ex.: TLS em HML/PRD) é violada.
    """
    if env is None:
        env = os.environ

    raw_environment = (_get_raw(env, "APP_ENV") or _get_raw(env, "ENV") or "DSV").upper()
    try:
        environment = Environment(raw_environment)
    except ValueError:
        raise ValidationError("Ambiente (APP_ENV/ENV) invalido ou nao suportado.")

    app_root_path = _get_raw(env, "APP_ROOT_PATH") or "/api-reconhecimento-documentacao-cadastro"
    if not app_root_path.startswith("/"):
        raise ValidationError("APP_ROOT_PATH deve iniciar com '/'.")

    tls = TLSSettings(
        verify=parse_bool(env, "TLS_VERIFY", True),
        ca_bundle_path=_get_raw(env, "TLS_CA_BUNDLE_PATH"),
    )
    tls.validate_for(environment)

    diagnostico_dir = _get_raw(env, "DIAGNOSTICO_DIR")
    if diagnostico_dir and environment.is_production_like:
        raise ValidationError(
            "DIAGNOSTICO_DIR grava o texto dos documentos e so e permitido em DSV."
        )

    timeouts = TimeoutSettings(
        connect_seconds=parse_float(env, "HTTP_CONNECT_TIMEOUT_SECONDS", 5.0, minimum=0.1),
        read_seconds=parse_float(env, "HTTP_READ_TIMEOUT_SECONDS", 30.0, minimum=0.1),
    )

    retry = RetrySettings(
        max_attempts=parse_int(env, "HTTP_RETRY_MAX_ATTEMPTS", 3, minimum=1),
        backoff_base_seconds=parse_float(env, "HTTP_RETRY_BACKOFF_BASE_SECONDS", 0.5, minimum=0.0),
        backoff_max_seconds=parse_float(env, "HTTP_RETRY_BACKOFF_MAX_SECONDS", 8.0, minimum=0.0),
    )

    token_cache = TokenCacheSettings(
        safety_margin_seconds=parse_float(
            env, "TOKEN_CACHE_SAFETY_MARGIN_SECONDS", 60.0, minimum=0.0
        ),
    )

    polling = PollingSettings(
        interval_seconds=parse_float(env, "POLLING_INTERVAL_SECONDS", 2.0, minimum=0.1),
        timeout_seconds=parse_float(env, "POLLING_TIMEOUT_SECONDS", 180.0, minimum=1.0),
        not_found_grace_seconds=parse_float(
            env, "POLLING_NOT_FOUND_GRACE_SECONDS", 30.0, minimum=0.0
        ),
    )

    ocr = OcrSettings(
        max_parallel=parse_int(env, "OCR_MAX_PARALLEL", 4, minimum=1),
        polling_interval_seconds=parse_float(
            env, "OCR_POLLING_INTERVAL_SECONDS", 5.0, minimum=3.0
        ),
        timeout_per_document_seconds=parse_float(
            env, "OCR_TIMEOUT_PER_DOCUMENT_SECONDS", 120.0, minimum=1.0
        ),
        budget_seconds=parse_float(env, "OCR_BUDGET_SECONDS", 150.0, minimum=1.0),
    )

    default_media = UploadLimits().allowed_media_types
    configured_media = parse_csv(env, "UPLOAD_ALLOWED_MEDIA_TYPES")
    upload_limits = UploadLimits(
        max_files=parse_int(env, "UPLOAD_MAX_FILES", 10, minimum=1),
        max_file_size_bytes=parse_int(
            env, "UPLOAD_MAX_FILE_SIZE_BYTES", 20 * 1024 * 1024, minimum=1
        ),
        allowed_media_types=configured_media or default_media,
    )

    gateway_endpoints = GatewayEndpoints(
        workflow_url=_get_raw(env, "APIM_WORKFLOW_URL"),
        status_url=_get_raw(env, "APIM_STATUS_URL"),
        file_manager_url=_get_raw(env, "APIM_FILE_MANAGER"),
    )

    gateway_resources = GatewayResourceCodes(
        container_id=_get_raw(env, "CONTAINER_ID"),
        workflow_code=_get_raw(env, "WORKFLOW_CODE"),
        step_workflow_code=_get_raw(env, "STEP_WORKFLOW_CODE"),
    )

    # Nomes de variável alinhados ao padrão corporativo confirmado em dois
    # projetos publicados (secrets.DSV/HML/PRD). O client secret NÃO é lido
    # aqui: fica no cofre e é obtido pelo adaptador no momento da requisição.
    service_identity = ServiceIdentitySettings(
        tenant_id=_get_raw(env, "TENANTID"),
        client_id=_get_raw(env, "GATEWAYAPPID"),
        scope_uri=_get_raw(env, "GATEWAYAPPIDURI"),
    )

    text_generation = TextGenerationSettings(
        workflow_code=_get_raw(env, "TEXTGEN_WORKFLOW_CODE"),
        model=_get_raw(env, "TEXTGEN_MODEL"),
        openai_api_endpoint=_get_raw(env, "OPENAI_API_ENDPOINT"),
        openai_api_version=_get_raw(env, "OPENAI_API_VERSION"),
        openai_api_key=_get_raw(env, "OPENAI_API_KEY"),
        max_tokens=parse_int(env, "TEXTGEN_MAX_TOKENS", 4096, minimum=1),
        max_prompt_chars=parse_int(env, "TEXTGEN_MAX_PROMPT_CHARS", 60000, minimum=1000),
    )

    return AppSettings(
        environment=environment,
        app_title=_get_raw(env, "APP_TITLE")
        or "API Agente Reconhecimento de Documentação de Cadastro - Hub BFF",
        app_root_path=app_root_path,
        app_version=_get_raw(env, "APP_VERSION") or "1.0.0",
        cors_allowed_origins=parse_allowed_origins(env.get("CORS_ALLOWED_ORIGINS")),
        tls=tls,
        timeouts=timeouts,
        retry=retry,
        token_cache=token_cache,
        polling=polling,
        ocr=ocr,
        upload_limits=upload_limits,
        gateway_endpoints=gateway_endpoints,
        gateway_resources=gateway_resources,
        service_identity=service_identity,
        text_generation=text_generation,
        # Ausente por padrão: a referência autoritativa (Curadoria) não envia.
        gateway_connection_mode=_get_raw(env, "GATEWAY_CONNECTION_MODE"),
        log_level=(_get_raw(env, "LOG_LEVEL") or "INFO").upper(),
        diagnostico_dir=diagnostico_dir,
    )
