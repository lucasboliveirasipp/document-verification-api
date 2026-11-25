"""Testes do relatório PDF (conteúdo verificado com PyMuPDF)."""
from __future__ import annotations

from datetime import datetime, timezone

import fitz

from src.models.parecer import AnaliseResponse, DocumentoAnalisado, Parecer
from src.services.relatorio_pdf import gerar_relatorio_pdf
from tests.unit.parecer_fakes import parecer_dict


def _analise(**overrides):
    dados = {
        "parecer": Parecer.model_validate(parecer_dict()),
        "documentos": [
            DocumentoAnalisado(nome="contrato.pdf", paginas=5, lido=True),
            DocumentoAnalisado(nome="formulario.pdf", paginas=3, lido=False, motivo="OCR pendente"),
        ],
        "avisos": ["1 documento(s) não puderam ser lidos e não entraram na análise."],
    }
    dados.update(overrides)
    return AnaliseResponse(**dados)


def _texto(pdf: bytes) -> str:
    with fitz.open(stream=pdf, filetype="pdf") as doc:
        # Espaços normalizados: o texto quebra de linha dentro das células.
        return " ".join(" ".join(pagina.get_text() for pagina in doc).split())


def _secao(texto: str, inicio: str, fim: str) -> str:
    return texto[texto.index(inicio):texto.index(fim)]


def test_pdf_segue_as_secoes_do_gabarito_na_ordem():
    pdf = gerar_relatorio_pdf(
        _analise(), gerado_em=datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
    )
    assert pdf.startswith(b"%PDF")
    texto = _texto(pdf)
    secoes = [
        "RELATÓRIO FINAL DE ANÁLISE DOCUMENTAL",
        "Resultado da análise",
        "1. Sumário executivo",
        "2. Documentos analisados",
        "3. Dados da solicitação",
        "4. Partes envolvidas e poderes de representação",
        "5. Validações realizadas",
        "6. Pontos de atenção",
        "7. Conclusão e encaminhamento",
        "8. Registro de fontes documentais",
    ]
    posicoes = [texto.index(secao) for secao in secoes]
    assert posicoes == sorted(posicoes)
    for trecho in (
        "APTO COM RESSALVAS",
        "Troca de titularidade",
        "Cidade Exemplo - SP | 01/01/2026",
        "CNPJ 00.000.000/0001-00",
        "Reconhecimento de firma pouco legível",
        "Prosseguir após sanear a assinatura.",
        "Solicitar nova assinatura.",
        "16/09/2026",
        "conferência humana",
        "Análise Documental",
        "Página 1",
    ):
        assert trecho in texto


def test_documentos_cruzam_leitura_do_bff_com_a_finalidade_do_modelo():
    texto = _texto(gerar_relatorio_pdf(_analise()))
    documentos = _secao(texto, "2. Documentos analisados", "3. Dados da solicitação")
    assert "Comprova os poderes do administrador." in documentos
    assert "Conferido" in documentos
    # Não lido pelo BFF: aparece com o motivo.
    assert "formulario.pdf" in documentos and "OCR pendente" in documentos
    # Citado pelo modelo mas fora do lote: não entra no relatório.
    assert "inventado.pdf" not in texto


def test_validacoes_trazem_as_quatro_perguntas_do_prompt_e_as_do_modelo():
    texto = _texto(gerar_relatorio_pdf(_analise()))
    validacoes = _secao(texto, "5. Validações realizadas", "6. Pontos de atenção")
    for trecho in (
        "Formulário preenchido corretamente",
        "Assinaturas conferem com os representantes legais",
        "Assinatura de administrador",
        "Assinatura e identificação do gerente",
        "Poderes de administração",
    ):
        assert trecho in validacoes


def test_fontes_listam_so_os_documentos_lidos():
    texto = _texto(gerar_relatorio_pdf(_analise()))
    fontes = texto[texto.index("8. Registro de fontes documentais"):]
    assert "contrato.pdf" in fontes
    assert "formulario.pdf" not in fontes


def test_texto_com_marcacao_nao_quebra_o_pdf():
    dados = parecer_dict()
    dados["orientacoes"] = [{"item": "<b>tag</b> & <script>", "resposta": "linha 1\nlinha 2"}]
    pdf = gerar_relatorio_pdf(_analise(parecer=Parecer.model_validate(dados)))
    assert "<b>tag</b> & <script>" in _texto(pdf)


def test_secoes_vazias_mostram_aviso_em_vez_de_tabela_vazia():
    dados = parecer_dict()
    for secao in ("orientacoes", "partes", "validacoes", "pontos_de_atencao", "documentos"):
        dados[secao] = []
    pdf = gerar_relatorio_pdf(
        _analise(parecer=Parecer.model_validate(dados), documentos=[], avisos=[])
    )
    texto = _texto(pdf)
    assert "Nenhuma orientação adicional." in texto
    assert "Nenhum ponto de atenção registrado." in texto
    assert "Partes não identificadas nos documentos." in texto
    assert "Nenhum documento lido." in texto


def test_capa_mostra_a_parte_uma_vez_quando_titular_e_subscritor_coincidem():
    dados = parecer_dict()
    q = dados["quadro_resumo"]
    q["titular_antigo"] = q["subscritor_antigo"] = "Empresa Exemplo A"
    texto = _texto(gerar_relatorio_pdf(_analise(parecer=Parecer.model_validate(dados))))
    capa = texto[:texto.index("1. Sumário executivo")]
    assert "Titular/Subscritor atual Empresa Exemplo A" in capa
    assert "Titular: Pessoa Exemplo B | Subscritor: Empresa Exemplo B" in capa


def test_finalidade_do_documento_casa_mesmo_com_acento_em_outra_forma_ou_caixa():
    import unicodedata

    dados = parecer_dict()
    dados["documentos"] = [
        {"nome": unicodedata.normalize("NFD", "FORMULÁRIO.pdf"), "finalidade": "Base da solicitação",
         "status": "Utilizado"},
    ]
    analise = _analise(
        parecer=Parecer.model_validate(dados),
        documentos=[DocumentoAnalisado(nome="Formulário.pdf", paginas=3, lido=True)],
    )
    documentos = _secao(
        _texto(gerar_relatorio_pdf(analise)), "2. Documentos analisados", "3. Dados da solicitação"
    )
    assert "Base da solicitação" in documentos
