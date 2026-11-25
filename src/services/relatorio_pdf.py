"""Relatório PDF do parecer (ReportLab).

Segue o relatório esperado:
capa com o resultado, oito seções numeradas e cabeçalho com página. Do prompt
original ficam as regras de layout: texto com quebra dentro das células,
padding generoso e cores Acme (vermelho, cinza e branco).
"""
from __future__ import annotations

import io
import unicodedata
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from src.models.parecer import AnaliseResponse, DocumentoCitado, Verificacao

COR_PRIMARIA = colors.HexColor("#1E3A8A")
CINZA_ESCURO = colors.HexColor("#4D4D4D")
CINZA_CLARO = colors.HexColor("#F2F2F2")
CINZA_BORDA = colors.HexColor("#BFBFBF")

_TITULO_RELATORIO = "Relatório Final de Análise Documental — Alteração Cadastral"
_CABECALHO_PAGINA = "Acme Capitalização | Análise Documental"
_MARGEM = 2 * cm
_LARGURA_UTIL = A4[0] - 2 * _MARGEM

_BASE = getSampleStyleSheet()
_TITULO = ParagraphStyle(
    "Titulo", parent=_BASE["Title"], textColor=COR_PRIMARIA, fontSize=18, leading=22
)
_SUBTITULO = ParagraphStyle(
    "Subtitulo", parent=_BASE["Heading3"], textColor=CINZA_ESCURO, alignment=1, spaceAfter=10
)
_SECAO = ParagraphStyle(
    "Secao", parent=_BASE["Heading2"], textColor=COR_PRIMARIA, spaceBefore=14, spaceAfter=6
)
_CELULA = ParagraphStyle("Celula", parent=_BASE["BodyText"], fontSize=9, leading=12)
_CABECALHO = ParagraphStyle(
    "Cabecalho", parent=_CELULA, textColor=colors.white, fontName="Helvetica-Bold"
)
_TEXTO = ParagraphStyle("Texto", parent=_BASE["BodyText"], fontSize=10, leading=14)
_NOTA = ParagraphStyle("Nota", parent=_BASE["BodyText"], fontSize=8, textColor=CINZA_ESCURO)


def _p(texto: str, estilo: ParagraphStyle = _CELULA) -> Paragraph:
    # escape(): o texto vem do modelo e do usuário, e o Paragraph interpreta tags.
    return Paragraph(escape(texto or "-").replace("\n", "<br/>"), estilo)


def _tabela(
    cabecalho: Sequence[str],
    linhas: Sequence[Sequence[str]],
    proporcao: Sequence[float],
) -> Table:
    dados = [[_p(c, _CABECALHO) for c in cabecalho]]
    dados += [[_p(c) for c in linha] for linha in linhas]
    tabela = Table(dados, colWidths=[_LARGURA_UTIL * p for p in proporcao], repeatRows=1)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), COR_PRIMARIA),
        ("GRID", (0, 0), (-1, -1), 0.5, CINZA_BORDA),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]
    for linha in range(2, len(dados), 2):
        estilo.append(("BACKGROUND", (0, linha), (-1, linha), CINZA_CLARO))
    tabela.setStyle(TableStyle(estilo))
    return tabela


def _tabela_ou_aviso(
    cabecalho: Sequence[str],
    linhas: Sequence[Sequence[str]],
    proporcao: Sequence[float],
    aviso: str,
):
    return _tabela(cabecalho, linhas, proporcao) if linhas else _p(aviso, _TEXTO)


def _quadro_resultado(decisao: str, justificativa: str) -> Table:
    texto = f"<b>{escape(decisao.upper())}</b>"
    if justificativa:
        texto += ": " + escape(justificativa).replace("\n", "<br/>")
    tabela = Table(
        [[_p("Resultado da análise", _CABECALHO), Paragraph(texto, _TEXTO)]],
        colWidths=[_LARGURA_UTIL * 0.25, _LARGURA_UTIL * 0.75],
    )
    tabela.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (0, 0), COR_PRIMARIA),
                ("BACKGROUND", (1, 0), (1, 0), CINZA_CLARO),
                ("BOX", (0, 0), (-1, -1), 1, COR_PRIMARIA),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    return tabela


def _parte_combinada(titular: str, subscritor: str) -> str:
    if titular == subscritor:
        return titular
    return f"Titular: {titular} | Subscritor: {subscritor}"


def _verificacao(rotulo: str, verificacao: Verificacao) -> Tuple[str, str, str]:
    return (rotulo, verificacao.resposta, verificacao.observacao)


def _chave_nome(nome: str) -> str:
    # O modelo pode devolver o nome com o acento em outra forma Unicode ou em
    # outra caixa; o cruzamento não pode depender disso.
    return unicodedata.normalize("NFC", nome).strip().casefold()


def _linhas_documentos(analise: AnaliseResponse) -> List[Tuple[str, str, str]]:
    # O modelo só descreve o papel do documento; quem diz se ele foi lido é o
    # BFF. Documento citado pelo modelo e ausente do lote é descartado.
    citados: Dict[str, DocumentoCitado] = {
        _chave_nome(d.nome): d for d in analise.parecer.documentos
    }
    linhas = []
    for documento in analise.documentos:
        citado = citados.get(_chave_nome(documento.nome))
        finalidade = citado.finalidade if citado else ""
        if documento.lido:
            status = (citado.status if citado else "") or f"Lido ({documento.paginas} pág.)"
        else:
            status = f"Não lido: {documento.motivo or 'sem texto'}"
        linhas.append((documento.nome, finalidade or "-", status))
    return linhas


def _cabecalho_e_rodape(canvas, doc) -> None:
    canvas.saveState()
    topo = A4[1] - _MARGEM + 0.8 * cm
    canvas.setFont("Helvetica-Bold", 8)
    canvas.setFillColor(COR_PRIMARIA)
    canvas.drawString(_MARGEM, topo, _CABECALHO_PAGINA)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(CINZA_ESCURO)
    canvas.drawRightString(A4[0] - _MARGEM, topo, f"Página {doc.page}")
    canvas.setStrokeColor(COR_PRIMARIA)
    canvas.line(_MARGEM, topo - 4, A4[0] - _MARGEM, topo - 4)
    canvas.restoreState()


def gerar_relatorio_pdf(analise: AnaliseResponse, *, gerado_em: Optional[datetime] = None) -> bytes:
    """Gera o PDF do parecer e devolve os bytes."""
    parecer = analise.parecer
    q = parecer.quadro_resumo
    data = (gerado_em or datetime.now(timezone.utc)).strftime("%d/%m/%Y")

    capa = [
        ("Tipo de solicitação", q.tipo_alteracao),
        ("Títulos", q.series_e_titulos),
        ("Titular/Subscritor atual",
         _parte_combinada(q.titular_antigo, q.subscritor_antigo)),
        ("Novo Titular/Subscritor",
         _parte_combinada(q.titular_novo, q.subscritor_novo)),
        ("Data do relatório", data),
    ]
    dados_solicitacao = [
        ("Tipo de alteração", q.tipo_alteracao),
        ("Séries e títulos envolvidos", q.series_e_titulos),
        ("Subscritor antigo", q.subscritor_antigo),
        ("Subscritor novo", q.subscritor_novo),
        ("Titular antigo", q.titular_antigo),
        ("Titular novo", q.titular_novo),
        ("Forma de cobrança / conta de débito", q.forma_cobranca),
        ("Local e data do formulário", q.local_e_data_formulario),
    ]
    partes = [
        (p.papel, " - ".join(x for x in (p.nome, p.documento) if x), p.representante, p.evidencia)
        for p in parecer.partes
    ]
    validacoes = [
        _verificacao("Formulário preenchido corretamente", q.formulario_preenchido_corretamente),
        _verificacao(
            "Assinaturas conferem com os representantes legais",
            q.assinaturas_conferem_representantes,
        ),
        _verificacao("Assinatura de administrador em falta", q.falta_assinatura_administrador),
        _verificacao("Assinatura e identificação do gerente", q.gerente_identificado),
    ] + [(v.criterio, v.resultado, v.consideracao) for v in parecer.validacoes]
    pontos = [(p.ponto, p.risco, p.acao_recomendada) for p in parecer.pontos_de_atencao]
    orientacoes = [(o.item, o.resposta) for o in parecer.orientacoes]
    fontes = [d.nome for d in analise.documentos if d.lido]

    elementos: List = [
        Paragraph(escape(_TITULO_RELATORIO.split(" — ")[0].upper()), _TITULO),
        _p(q.tipo_alteracao, _SUBTITULO),
        _quadro_resultado(parecer.resultado.decisao, parecer.resultado.justificativa),
        Spacer(1, 10),
        _tabela(("Campo", "Detalhe"), capa, (0.3, 0.7)),
        Spacer(1, 6),
        _p(
            "Relatório elaborado com base nos documentos disponibilizados para conferência "
            "documental. A decisão operacional deve observar os fluxos internos aplicáveis.",
            _NOTA,
        ),
        Paragraph("1. Sumário executivo", _SECAO),
        _p(parecer.sumario_executivo or "-", _TEXTO),
        Paragraph("2. Documentos analisados", _SECAO),
        _tabela_ou_aviso(
            ("Documento", "Finalidade na análise", "Status"),
            _linhas_documentos(analise),
            (0.3, 0.5, 0.2),
            "Nenhum documento recebido.",
        ),
    ]
    for aviso in analise.avisos:
        elementos += [Spacer(1, 4), _p(f"Atenção: {aviso}", _NOTA)]
    elementos += [
        Paragraph("3. Dados da solicitação", _SECAO),
        _tabela(("Item", "Informação verificada"), dados_solicitacao, (0.35, 0.65)),
        Paragraph("4. Partes envolvidas e poderes de representação", _SECAO),
        _tabela_ou_aviso(
            ("Papel", "Parte", "Representante", "Evidência documental"),
            partes,
            (0.2, 0.3, 0.2, 0.3),
            "Partes não identificadas nos documentos.",
        ),
        Paragraph("5. Validações realizadas", _SECAO),
        _tabela(("Critério", "Resultado", "Consideração"), validacoes, (0.32, 0.18, 0.5)),
        Paragraph("6. Pontos de atenção", _SECAO),
        _tabela_ou_aviso(
            ("Ponto", "Risco operacional", "Ação recomendada"),
            pontos,
            (0.35, 0.3, 0.35),
            "Nenhum ponto de atenção registrado.",
        ),
        Paragraph("7. Conclusão e encaminhamento", _SECAO),
        _p(parecer.conclusao or "-", _TEXTO),
        Spacer(1, 8),
        _tabela_ou_aviso(
            ("Item", "Resposta"), orientacoes, (0.38, 0.62), "Nenhuma orientação adicional."
        ),
        Paragraph("8. Registro de fontes documentais", _SECAO),
        _p(
            ("Fontes utilizadas: " + ", ".join(fontes) + ".") if fontes
            else "Nenhum documento lido.",
            _TEXTO,
        ),
        Spacer(1, 16),
        _p(
            "Parecer gerado com apoio de IA. Requer conferência humana antes de qualquer decisão. "
            "Este relatório não substitui controles internos, alçadas de aprovação, validações "
            "cadastrais, regras de compliance ou consultas aos sistemas oficiais.",
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
