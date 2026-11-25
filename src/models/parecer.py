"""Contratos Pydantic do parecer de Alteração Cadastral e da resposta da API.

O parecer vem do modelo em JSON (``response_format=json_object``). A validação
aceita variações comuns de escrita nas respostas ("nao", "N/A") e recusa o que
não reconhece, para nunca publicar um parecer fora do formato.

As seções seguem o relatório esperado (gabarito em ``src/docs/expectativa de
entrega/``): decisão, sumário, documentos, partes, validações, pontos de
atenção e conclusão. Só a decisão e o quadro-resumo são obrigatórios; as
seções de detalhe ausentes viram vazias.
"""
from __future__ import annotations

import unicodedata
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

RESPOSTAS_VERIFICACAO = ("Sim", "Não", "Parcial", "Não se aplica", "Não identificado")
DECISOES = ("Apto", "Apto com ressalvas", "Inapto", "Inconclusivo")
NAO_IDENTIFICADO = "Não identificado"


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return " ".join(sem_acento.lower().replace("_", " ").split())


_EQUIVALENTES: Dict[str, str] = {_normalizar(r): r for r in RESPOSTAS_VERIFICACAO}
_EQUIVALENTES.update(
    {
        "n/a": "Não se aplica",
        "na": "Não se aplica",
        "nao aplicavel": "Não se aplica",
        "nao encontrado": NAO_IDENTIFICADO,
    }
)


_DECISOES: Dict[str, str] = {_normalizar(d): d for d in DECISOES}


def _texto_vazio_se_nulo(valor: Any) -> Any:
    return "" if valor is None else valor


class Verificacao(BaseModel):
    """Resposta objetiva a uma pergunta de conferência, com justificativa."""

    model_config = ConfigDict(extra="ignore")

    resposta: str
    observacao: str = ""

    @field_validator("resposta")
    @classmethod
    def _resposta_canonica(cls, valor: str) -> str:
        canonica = _EQUIVALENTES.get(_normalizar(valor))
        if canonica is None:
            raise ValueError("resposta fora das opções permitidas")
        return canonica

    _observacao_nula = field_validator("observacao", mode="before")(_texto_vazio_se_nulo)


class QuadroResumo(BaseModel):
    """Os nove itens do quadro-resumo pedidos no prompt original."""

    model_config = ConfigDict(extra="ignore")

    tipo_alteracao: str
    series_e_titulos: str
    subscritor_antigo: str
    subscritor_novo: str
    titular_antigo: str
    titular_novo: str
    forma_cobranca: str
    formulario_preenchido_corretamente: Verificacao
    assinaturas_conferem_representantes: Verificacao
    falta_assinatura_administrador: Verificacao
    gerente_identificado: Verificacao
    local_e_data_formulario: str = NAO_IDENTIFICADO

    @field_validator(
        "tipo_alteracao", "series_e_titulos", "subscritor_antigo", "subscritor_novo",
        "titular_antigo", "titular_novo", "forma_cobranca", "local_e_data_formulario",
        mode="before",
    )
    @classmethod
    def _texto_tolerante(cls, valor: Any) -> Any:
        if valor is None or valor == "":
            return NAO_IDENTIFICADO
        if isinstance(valor, list):
            return "; ".join(str(item) for item in valor)
        return valor


class Orientacao(BaseModel):
    """Linha da tabela "Item | Resposta" de orientações."""

    model_config = ConfigDict(extra="ignore")

    item: str
    resposta: str


class Resultado(BaseModel):
    """Manchete do relatório: a decisão e o porquê."""

    model_config = ConfigDict(extra="ignore")

    decisao: str
    justificativa: str = ""

    @field_validator("decisao")
    @classmethod
    def _decisao_canonica(cls, valor: str) -> str:
        canonica = _DECISOES.get(_normalizar(valor))
        if canonica is None:
            raise ValueError("decisão fora das opções permitidas")
        return canonica

    _justificativa_nula = field_validator("justificativa", mode="before")(_texto_vazio_se_nulo)


class _Linha(BaseModel):
    """Linha de tabela de detalhe: campos nulos viram texto vazio."""

    model_config = ConfigDict(extra="ignore")

    @field_validator("*", mode="before")
    @classmethod
    def _nulo(cls, valor: Any) -> Any:
        return _texto_vazio_se_nulo(valor)


class DocumentoCitado(_Linha):
    """Papel de um documento do lote na análise, segundo o modelo."""

    nome: str
    finalidade: str = ""
    status: str = ""


class Parte(_Linha):
    """Parte envolvida (cedente, cessionário...) e quem a representa."""

    papel: str = ""
    nome: str = ""
    documento: str = ""
    representante: str = ""
    evidencia: str = ""


class Validacao(_Linha):
    """Critério conferido, além das quatro verificações do quadro-resumo."""

    criterio: str
    resultado: str = ""
    consideracao: str = ""


class PontoDeAtencao(_Linha):
    """Ressalva com o risco operacional e a ação recomendada."""

    ponto: str
    risco: str = ""
    acao_recomendada: str = ""


class Parecer(BaseModel):
    """Parecer estruturado devolvido pelo modelo."""

    model_config = ConfigDict(extra="ignore")

    resultado: Resultado
    quadro_resumo: QuadroResumo
    sumario_executivo: str = ""
    documentos: List[DocumentoCitado] = Field(default_factory=list)
    partes: List[Parte] = Field(default_factory=list)
    validacoes: List[Validacao] = Field(default_factory=list)
    pontos_de_atencao: List[PontoDeAtencao] = Field(default_factory=list)
    conclusao: str = ""
    orientacoes: List[Orientacao] = Field(default_factory=list)

    _textos_nulos = field_validator("sumario_executivo", "conclusao", mode="before")(
        _texto_vazio_se_nulo
    )


class DocumentoAnalisado(BaseModel):
    """Situação de cada arquivo do lote na análise."""

    nome: str
    paginas: int
    lido: bool
    motivo: Optional[str] = None
    #: Papel informado no envio (campo de upload da tela), quando houver.
    papel: Optional[str] = None


class AnaliseResponse(BaseModel):
    """Resposta de ``POST /analisar_documentos`` (e entrada do relatório PDF)."""

    modalidade: str = "alteracao_cadastral"
    parecer: Parecer
    documentos: List[DocumentoAnalisado]
    avisos: List[str] = Field(default_factory=list)
