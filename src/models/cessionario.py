"""Contratos Pydantic do relatório de Cadastro de Cessionário.

O modelo devolve, em JSON, a validação campo a campo de cada registro da
planilha, a avaliação de cada documento e as pendências (gabarito
``Relatorio_Validacao_Cessionario_Luciano.pdf``). O que é conta fica com o BFF:
o resumo geral e a classificação da legibilidade são calculados aqui, para não
depender da aritmética do modelo.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from src.models.parecer import DocumentoAnalisado, _Linha, _normalizar

CADASTRO_CESSIONARIO = "cadastro_cessionario"

VALIDADO = "Validado"
DIVERGENTE = "Divergente"
NAO_LOCALIZADO = "Não Localizado"
NAO_APLICAVEL = "Não Aplicável"
STATUS_CAMPO = (VALIDADO, DIVERGENTE, NAO_LOCALIZADO, NAO_APLICAVEL)

APROVADO = "Aprovado"
APROVADO_COM_RESSALVAS = "Aprovado com Ressalvas"
REPROVADO = "Reprovado"
STATUS_REGISTRO = (APROVADO, APROVADO_COM_RESSALVAS, REPROVADO)

#: Faixas do prompt original: (piso em %, classificação).
FAIXAS_LEGIBILIDADE = ((95, "Excelente"), (80, "Boa"), (60, "Regular"), (0, "Ruim"))
#: Abaixo disso o documento conta como de baixa legibilidade (Regular ou Ruim).
LEGIBILIDADE_MINIMA = 80

_STATUS_CAMPO: Dict[str, str] = {_normalizar(s): s for s in STATUS_CAMPO}
_STATUS_CAMPO.update(
    {
        "validada": VALIDADO,
        "compativel": VALIDADO,
        "divergencia": DIVERGENTE,
        "nao localizada": NAO_LOCALIZADO,
        "nao encontrado": NAO_LOCALIZADO,
        "nao identificado": NAO_LOCALIZADO,
        "n/a": NAO_APLICAVEL,
        "na": NAO_APLICAVEL,
        "nao se aplica": NAO_APLICAVEL,
    }
)
_STATUS_REGISTRO: Dict[str, str] = {_normalizar(s): s for s in STATUS_REGISTRO}
_STATUS_REGISTRO.update(
    {
        "aprovado com ressalva": APROVADO_COM_RESSALVAS,
        "reprovada": REPROVADO,
        "aprovada": APROVADO,
    }
)


def _canonico(valor: Any, tabela: Dict[str, str], erro: str) -> str:
    canonico = tabela.get(_normalizar(str(valor or "")))
    if canonico is None:
        raise ValueError(erro)
    return canonico


def classificar_legibilidade(percentual: int) -> str:
    """Classificação do prompt original para um percentual de 0 a 100."""
    for piso, classificacao in FAIXAS_LEGIBILIDADE:
        if percentual >= piso:
            return classificacao
    return FAIXAS_LEGIBILIDADE[-1][1]  # pragma: no cover - o piso 0 sempre casa


class CampoValidado(_Linha):
    """Linha da tabela "Campo | Planilha | Documento | Status | Observações"."""

    campo: str
    valor_planilha: str = ""
    valor_documento: str = ""
    status: str
    observacao: str = ""

    @field_validator("status", mode="after")
    @classmethod
    def _status_canonico(cls, valor: str) -> str:
        return _canonico(valor, _STATUS_CAMPO, "status de campo fora das opções permitidas")


class RegistroValidado(BaseModel):
    """Resultado de um cessionário da planilha."""

    model_config = ConfigDict(extra="ignore")

    nome: str = ""
    cpf_cnpj: str = ""
    status_geral: str
    campos: List[CampoValidado] = Field(default_factory=list)
    alto_risco: bool = False
    motivo_alto_risco: str = ""

    @field_validator("status_geral")
    @classmethod
    def _status_canonico(cls, valor: str) -> str:
        return _canonico(valor, _STATUS_REGISTRO, "status geral fora das opções permitidas")

    @field_validator("nome", "cpf_cnpj", "motivo_alto_risco", mode="before")
    @classmethod
    def _nulo(cls, valor: Any) -> Any:
        return "" if valor is None else valor

    @field_validator("alto_risco", mode="before")
    @classmethod
    def _nulo_falso(cls, valor: Any) -> Any:
        return False if valor is None else valor

    def contar(self, status: str) -> int:
        return sum(1 for campo in self.campos if campo.status == status)


class DocumentoAvaliado(_Linha):
    """Avaliação de qualidade de um documento, segundo o modelo."""

    nome: str
    tipo: str = ""
    legibilidade: int
    observacoes: str = ""

    @field_validator("legibilidade", mode="before")
    @classmethod
    def _percentual(cls, valor: Any) -> int:
        # O modelo às vezes escreve "92%" ou 92.5; o que importa é o número.
        if isinstance(valor, (int, float)) and not isinstance(valor, bool):
            numero = float(valor)
        else:
            achado = re.search(r"\d+(?:[.,]\d+)?", str(valor or ""))
            if achado is None:
                raise ValueError("legibilidade sem percentual")
            numero = float(achado.group().replace(",", "."))
        return max(0, min(100, round(numero)))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def classificacao(self) -> str:
        return classificar_legibilidade(self.legibilidade)


class Pendencia(_Linha):
    """Linha de "Pendências e Recomendações"."""

    cliente: str = ""
    campo: str = ""
    descricao: str = ""
    acao_recomendada: str = ""


class RelatorioCessionario(BaseModel):
    """Relatório devolvido pelo modelo."""

    model_config = ConfigDict(extra="ignore")

    registros: List[RegistroValidado] = Field(min_length=1)
    documentos: List[DocumentoAvaliado] = Field(default_factory=list)
    pendencias: List[Pendencia] = Field(default_factory=list)
    conclusao: str = ""

    @field_validator("conclusao", mode="before")
    @classmethod
    def _nulo(cls, valor: Any) -> Any:
        return "" if valor is None else valor


class ResumoGeral(BaseModel):
    """Seção 1 do relatório, calculada pelo BFF."""

    total_registros: int
    aprovados: int
    aprovados_com_ressalvas: int
    reprovados: int
    divergencias: int
    campos_nao_localizados: int
    documentos_baixa_legibilidade: int
    registros_alto_risco: int


class CessionarioResponse(BaseModel):
    """Resposta de ``POST /analisar_cessionario`` (e entrada do relatório PDF)."""

    modalidade: str = CADASTRO_CESSIONARIO
    relatorio: RelatorioCessionario
    documentos: List[DocumentoAnalisado]
    #: Quantos cessionários a planilha tinha, para conferir se todos voltaram.
    registros_planilha: int = Field(ge=0)
    avisos: List[str] = Field(default_factory=list)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resumo(self) -> ResumoGeral:
        registros = self.relatorio.registros
        baixa = sum(1 for d in self.relatorio.documentos if d.legibilidade < LEGIBILIDADE_MINIMA)
        baixa += sum(1 for d in self.documentos if not d.lido)
        return ResumoGeral(
            total_registros=len(registros),
            aprovados=sum(1 for r in registros if r.status_geral == APROVADO),
            aprovados_com_ressalvas=sum(
                1 for r in registros if r.status_geral == APROVADO_COM_RESSALVAS
            ),
            reprovados=sum(1 for r in registros if r.status_geral == REPROVADO),
            divergencias=sum(r.contar(DIVERGENTE) for r in registros),
            campos_nao_localizados=sum(r.contar(NAO_LOCALIZADO) for r in registros),
            documentos_baixa_legibilidade=baixa,
            registros_alto_risco=sum(1 for r in registros if r.alto_risco),
        )
