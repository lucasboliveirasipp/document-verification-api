"""Relatório PDF da validação de cessionários (ReportLab).

Segue o relatório esperado (``Relatorio_Validacao_Cessionario_Luciano.pdf``):
resumo geral, resultado individual de cada registro, avaliação dos documentos
e pendências. Usa os estilos e as tabelas do relatório da Alteração (cores
Acme, quebra de linha nas células e cabeçalho repetido a cada página).
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import List, Optional
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

from src.models.cessionario import CessionarioResponse, RegistroValidado
from src.services.relatorio_pdf import (
    _MARGEM,
    _NOTA,
    _SECAO,
    _TEXTO,
    _TITULO,
    _cabecalho_e_rodape,
    _p,
    _quadro_resultado,
    _tabela,
    _tabela_ou_aviso,
)

_TITULO_RELATORIO = "Relatório de Validação Cadastral e Documental — Cessionários"


def _registro(numero: int, registro: RegistroValidado) -> List:
    titulo = f"2.{numero} {registro.nome or 'Registro sem nome'}"
    identificacao = (
        f"<b>CPF/CNPJ:</b> {escape(registro.cpf_cnpj or '-')} &nbsp;&nbsp; "
        f"<b>Status geral:</b> {escape(registro.status_geral.upper())}"
    )
    elementos: List = [
        KeepTogether([Paragraph(escape(titulo), _TEXTO), Paragraph(identificacao, _TEXTO)])
    ]
    if registro.alto_risco:
        elementos.append(
            _quadro_resultado("Alto risco", registro.motivo_alto_risco or "Sem motivo informado.")
        )
    elementos += [
        Spacer(1, 4),
        _tabela_ou_aviso(
            ("Campo", "Planilha", "Documento", "Status", "Observações"),
            [
                (c.campo, c.valor_planilha, c.valor_documento, c.status, c.observacao)
                for c in registro.campos
            ],
            (0.17, 0.2, 0.2, 0.15, 0.28),
            "Nenhum campo validado.",
        ),
        Spacer(1, 10),
    ]
    return elementos


def gerar_relatorio_cessionario_pdf(
    analise: CessionarioResponse, *, gerado_em: Optional[datetime] = None
) -> bytes:
    """Gera o PDF da validação de cessionários e devolve os bytes."""
    relatorio = analise.relatorio
    resumo = analise.resumo
    data = (gerado_em or datetime.now(timezone.utc)).strftime("%d/%m/%Y")

    indicadores = [
        ("Total de registros analisados", str(resumo.total_registros)),
        ("Registros aprovados", str(resumo.aprovados)),
        ("Registros aprovados com ressalvas", str(resumo.aprovados_com_ressalvas)),
        ("Registros reprovados", str(resumo.reprovados)),
        ("Quantidade de divergências", str(resumo.divergencias)),
        ("Campos não localizados", str(resumo.campos_nao_localizados)),
        ("Documentos com baixa legibilidade", str(resumo.documentos_baixa_legibilidade)),
        ("Registros de alto risco", str(resumo.registros_alto_risco)),
        ("Data do relatório", data),
    ]
    avaliados = [
        (d.nome, d.tipo, f"{d.legibilidade}%", d.classificacao, d.observacoes)
        for d in relatorio.documentos
    ]
    # Quem diz se o documento foi lido é o BFF, não o modelo.
    avaliados += [
        (d.nome, "-", "0%", "Ruim", f"Não lido: {d.motivo or 'sem texto'}")
        for d in analise.documentos
        if not d.lido
    ]
    pendencias = [
        (p.cliente, p.campo, p.descricao, p.acao_recomendada) for p in relatorio.pendencias
    ]
    lidos = sum(1 for d in analise.documentos if d.lido)

    elementos: List = [
        Paragraph(escape(_TITULO_RELATORIO.split(" — ")[0].upper()), _TITULO),
        Paragraph("1. Resumo geral", _SECAO),
        _tabela(("Indicador", "Resultado"), indicadores, (0.6, 0.4)),
    ]
    for aviso in analise.avisos:
        elementos += [Spacer(1, 4), _p(f"Atenção: {aviso}", _NOTA)]
    elementos.append(Paragraph("2. Resultado individual de cada registro", _SECAO))
    for numero, registro in enumerate(relatorio.registros, start=1):
        elementos += _registro(numero, registro)
    elementos += [
        Paragraph("3. Avaliação dos documentos", _SECAO),
        _tabela_ou_aviso(
            ("Documento", "Tipo", "Legibilidade", "Classificação", "Observações"),
            avaliados,
            (0.22, 0.15, 0.16, 0.17, 0.30),
            "Nenhum documento avaliado.",
        ),
        Paragraph("4. Pendências e recomendações", _SECAO),
        _tabela_ou_aviso(
            ("Cliente", "Campo", "Descrição", "Ação recomendada"),
            pendencias,
            (0.22, 0.16, 0.32, 0.3),
            "Não foram identificadas divergências cadastrais nem ausências documentais.",
        ),
        Spacer(1, 8),
        _p(relatorio.conclusao or "-", _TEXTO),
        Spacer(1, 8),
        _p(
            f"Confirmações finais: {resumo.total_registros} de {analise.registros_planilha} "
            f"registro(s) da planilha analisado(s); {lidos} de {len(analise.documentos)} "
            "documento(s) lido(s); divergências registradas na seção 2.",
            _NOTA,
        ),
        Spacer(1, 16),
        _p(
            "Relatório gerado com apoio de IA. A legibilidade é estimada a partir do texto lido "
            "(PDF digital ou OCR), não da imagem. Requer conferência humana antes de qualquer "
            "decisão e não substitui controles internos, validações cadastrais, regras de "
            "compliance ou consultas aos sistemas oficiais.",
            _NOTA,
        ),
    ]

    buffer = io.BytesIO()
    SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=_MARGEM,
        rightMargin=_MARGEM,
        topMargin=_MARGEM,
        bottomMargin=_MARGEM,
        title=_TITULO_RELATORIO,
    ).build(elementos, onFirstPage=_cabecalho_e_rodape, onLaterPages=_cabecalho_e_rodape)
    return buffer.getvalue()
