"""Sanitização e redaction de dados antes de qualquer registro.

Este módulo é a barreira que impede que logs, métricas, traces e mensagens de
erro exponham dados proibidos (Requisitos 9.4, 10.4, 10.7, 11.2):

* tokens/credenciais e cabeçalhos de autorização;
* CPF/CNPJ, conta bancária e outros números sensíveis;
* nomes de campo notoriamente sensíveis (senha, secret, token, authorization);
* URLs assinadas (com credencial/assinatura em query string);
* conteúdo documental ou OCR (mantido fora do log por construção; qualquer
  string longa é truncada).

Princípios:

* funções **puras** e determinísticas, seguras para uso em qualquer camada;
* redaction por **valor** (padrões regex) e por **chave** (nomes sensíveis);
* nunca lança exceção por causa de conteúdo: entrada inesperada é reduzida a um
  marcador seguro.

Aviso: redaction reduz risco, não substitui a regra de **não coletar** dados
sensíveis. O objetivo é falhar seguro caso um valor sensível chegue aqui.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

#: Marcador único usado para qualquer valor removido.
REDACTED = "[REDACTED]"

#: Tamanho máximo de string preservada em log. Evita vazar conteúdo/OCR extenso.
MAX_STRING_LEN = 256

#: Tamanho máximo de um valor de cabeçalho HTTP de saída. Evita estourar o
#: limite de servidores/proxies intermediários.
MAX_HEADER_LEN = 8192

#: Nomes de chave (case-insensitive) cujo valor é sempre removido.
_SENSITIVE_KEY_PARTS = (
    "authorization",
    "token",
    "secret",
    "password",
    "senha",
    "client_secret",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "bearer",
    "cpf",
    "cnpj",
    "conta",
    "account",
    "agencia",
    "iban",
    "card",
    "cartao",
    "signature",
    "assinatura",
    "sas",
)

# ─── Padrões de valor sensível ────────────────────────────────────────────────

# Bearer/JWT: "Bearer x" ou três segmentos base64url separados por ponto.
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._-]+", re.IGNORECASE)
_JWT_RE = re.compile(r"\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")

# CPF: 000.000.000-00 ou 11 dígitos isolados. CNPJ: 00.000.000/0000-00 ou 14 dígitos.
_CPF_RE = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")
_CNPJ_RE = re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b")

# URL assinada: qualquer URL cuja query contenha parâmetros de credencial/assinatura.
_SIGNED_URL_RE = re.compile(
    r"https?://[^\s\"']*[?&](?:sig|signature|sas|token|x-amz-signature|"
    r"awsaccesskeyid|se|sr|sp|sv|st|api[_-]?key|access[_-]?token)=[^\s\"']*",
    re.IGNORECASE,
)


def _looks_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def redact_text(value: Any) -> str:
    """Remove padrões sensíveis de um texto e trunca comprimento excessivo.

    A ordem importa: URLs assinadas e JWTs são removidos antes de padrões
    numéricos para não fragmentar a substituição.

    Args:
        value: valor a sanitizar. Não-strings são convertidas de forma segura.

    Returns:
        Texto sanitizado, seguro para log.
    """
    try:
        text = value if isinstance(value, str) else str(value)
    except Exception:  # noqa: BLE001 - valor não convertível vira marcador
        return REDACTED

    text = _SIGNED_URL_RE.sub(REDACTED, text)
    text = _BEARER_RE.sub(REDACTED, text)
    text = _JWT_RE.sub(REDACTED, text)
    text = _CNPJ_RE.sub(REDACTED, text)
    text = _CPF_RE.sub(REDACTED, text)

    if len(text) > MAX_STRING_LEN:
        text = text[:MAX_STRING_LEN] + "...[truncated]"
    return text


def redact_mapping(data: Mapping[str, Any], *, _depth: int = 0) -> dict:
    """Sanitiza um mapeamento recursivamente, por chave e por valor.

    Chaves notoriamente sensíveis têm o valor totalmente removido. Demais
    valores passam por :func:`redact_text` (strings) ou são percorridos
    (mapeamentos/sequências). A profundidade é limitada para evitar recursão
    excessiva de estruturas inesperadas.

    Args:
        data: mapeamento a sanitizar.

    Returns:
        Novo dicionário sanitizado, seguro para log.
    """
    if _depth > 6:
        return {"...": "[max-depth]"}

    result: dict = {}
    for raw_key, raw_value in data.items():
        key = str(raw_key)
        if _looks_sensitive_key(key):
            result[key] = REDACTED
            continue
        result[key] = _redact_value(raw_value, _depth=_depth + 1)
    return result


def _redact_value(value: Any, *, _depth: int) -> Any:
    if isinstance(value, Mapping):
        return redact_mapping(value, _depth=_depth)
    if isinstance(value, (list, tuple)):
        if _depth > 6:
            return ["[max-depth]"]
        return [_redact_value(item, _depth=_depth + 1) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    # Tipos desconhecidos: representa de forma sanitizada.
    return redact_text(value)


def safe_error_summary(error: BaseException) -> str:
    """Produz um resumo curto e sanitizado de uma exceção, seguro para log.

    Registra apenas o tipo e uma mensagem sanitizada/truncada. Nunca inclui
    stack trace, corpo de resposta externa ou conteúdo documental.

    Args:
        error: exceção a resumir.

    Returns:
        Texto no formato ``"TipoDoErro: mensagem sanitizada"``.
    """
    error_type = type(error).__name__
    message = redact_text(str(error)) if str(error) else ""
    if not message:
        return error_type
    return f"{error_type}: {message}"


def sanitize_header_value(value: Any) -> str:
    """Sanitiza um valor destinado a cabeçalho HTTP de saída.

    Defesa contra **CRLF/header injection**: um valor de credencial recebido de
    terceiro (ex.: ``access_token`` devolvido pelo emissor) nunca deve ser
    concatenado cru em ``Authorization: Bearer <token>``. Um ``\\r`` ou ``\\n``
    no valor permitiria injetar cabeçalhos arbitrários na requisição de saída.

    Prática adotada do projeto de referência ``api-agente-orientador-curadoria``,
    que aplica a mesma proteção antes de montar o cabeçalho de autorização.

    Diferente de :func:`redact_text`, esta função **preserva o valor** (não
    remove segredos): o objetivo é tornar o valor seguro para transporte, não
    seguro para log. Nunca registre o retorno desta função.

    Args:
        value: valor a sanitizar. ``None`` resulta em string vazia.

    Returns:
        Valor sem quebras de linha nem caracteres de controle, truncado em
        :data:`MAX_HEADER_LEN`.
    """
    if value is None:
        return ""

    text = value if isinstance(value, str) else str(value)
    # Remove explicitamente CR/LF antes do filtro geral, para deixar a intenção
    # de anti-injeção evidente na leitura.
    text = text.replace("\r", "").replace("\n", "")
    # Mantém ASCII imprimível e Unicode acima de 0x80; descarta controles.
    text = "".join(ch for ch in text if 0x20 <= ord(ch) <= 0x7E or ord(ch) >= 0x80)
    text = text.strip()

    if len(text) > MAX_HEADER_LEN:
        text = text[:MAX_HEADER_LEN]
    return text
