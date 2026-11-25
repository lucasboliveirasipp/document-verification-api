"""Testes do Cadastro de Cessionário: contrato, prompt, serviço, PDF e rotas.

Tudo simulado: gerador e OCR fakes, planilha e documentos sintéticos.
"""
from __future__ import annotations

import json
from datetime import date

import fitz
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError

from src.controllers.analise_controller import get_gerador, get_leitor_ocr, get_settings
from src.domain.errors import ContractViolationError, ValidationError
from src.main import app
from src.models.cessionario import (
    CessionarioResponse,
    DocumentoAvaliado,
    RelatorioCessionario,
    classificar_legibilidade,
)
from src.models.parecer import DocumentoAnalisado
from src.services.cessionario_service import analisar_cessionario
from src.services.planilha import ler_planilha
from src.services.prompts.cadastro_cessionario import SYSTEM_PROMPT, montar_prompt_usuario
from src.services.relatorio_cessionario_pdf import gerar_relatorio_cessionario_pdf
from src.services.validacao import ArquivoRecebido
from src.settings import load_settings
from tests.unit.cessionario_fakes import (
    RELATORIO_JSON,
    linha_exemplo,
    planilha_xlsx,
    relatorio_dict,
)
from tests.unit.pdf_fakes import JPEG_MINIMO, pdf_com_texto


class FakeGerador:
    def __init__(self, resposta=RELATORIO_JSON):
        self.resposta = resposta
        self.chamadas = []

    def gerar_texto(self, system_prompt, user_content, *, response_format=None, max_tokens=None):
        self.chamadas.append(
            {"system": system_prompt, "user": user_content, "response_format": response_format}
        )
        return self.resposta


class FakeLeitorOcr:
    disponivel = True

    def ler(self, arquivo, *, timeout_seconds):
        return "DECLARACAO DE AUTORIZACAO lida por OCR"


def _settings(**env):
    base = {"APP_ENV": "TEST"}
    base.update(env)
    return load_settings(base)


def _planilha(*linhas):
    return ArquivoRecebido("cessionarios.xlsx", planilha_xlsx(*linhas))


def _cnh():
    return ArquivoRecebido("cnh.pdf", pdf_com_texto("CNH PESSOA EXEMPLO UM"), papel="identificacao")


def _resposta(relatorio=None, documentos=None, registros_planilha=1, avisos=None):
    return CessionarioResponse(
        relatorio=RelatorioCessionario.model_validate(relatorio or relatorio_dict()),
        documentos=documentos
        or [DocumentoAnalisado(nome="cnh.pdf", paginas=1, lido=True, papel="identificacao")],
        registros_planilha=registros_planilha,
        avisos=avisos or [],
    )


# ─── Contrato do relatório ───────────────────────────────────────────────────

def test_status_aceitam_variacoes_de_escrita():
    relatorio = RelatorioCessionario.model_validate(relatorio_dict())
    status = [c.status for c in relatorio.registros[0].campos]
    assert status == ["Validado", "Divergente", "Não Aplicável", "Não Localizado"]
    assert relatorio.registros[0].campos[3].valor_documento == ""
    assert relatorio.registros[0].motivo_alto_risco == ""


def test_status_geral_aceita_variacao():
    dados = relatorio_dict(status_geral="APROVADO COM RESSALVA")
    relatorio = RelatorioCessionario.model_validate(dados)
    assert relatorio.registros[0].status_geral == "Aprovado com Ressalvas"


@pytest.mark.parametrize("campo, valor", [("status_geral", "Talvez"), ("status", "Quase")])
def test_status_desconhecido_e_recusado(campo, valor):
    dados = relatorio_dict()
    if campo == "status_geral":
        dados["registros"][0]["status_geral"] = valor
    else:
        dados["registros"][0]["campos"][0]["status"] = valor
    with pytest.raises(PydanticValidationError):
        RelatorioCessionario.model_validate(dados)


def test_relatorio_sem_registros_e_recusado():
    dados = relatorio_dict()
    dados["registros"] = []
    with pytest.raises(PydanticValidationError):
        RelatorioCessionario.model_validate(dados)


@pytest.mark.parametrize(
    "valor, esperado", [("98%", 98), (72.4, 72), ("92,6 %", 93), (150, 100), (-3, 0), (80, 80)]
)
def test_legibilidade_vira_percentual_inteiro(valor, esperado):
    assert DocumentoAvaliado(nome="d", legibilidade=valor).legibilidade == esperado


@pytest.mark.parametrize("valor", ["ilegível", None, True])
def test_legibilidade_sem_numero_e_recusada(valor):
    with pytest.raises(PydanticValidationError):
        DocumentoAvaliado(nome="d", legibilidade=valor)


@pytest.mark.parametrize(
    "percentual, classificacao",
    [(100, "Excelente"), (95, "Excelente"), (94, "Boa"), (80, "Boa"), (79, "Regular"),
     (60, "Regular"), (59, "Ruim"), (0, "Ruim")],
)
def test_classificacao_segue_as_faixas_do_prompt(percentual, classificacao):
    assert classificar_legibilidade(percentual) == classificacao


def test_resumo_e_calculado_pelo_bff():
    resposta = _resposta(
        documentos=[
            DocumentoAnalisado(nome="cnh.pdf", paginas=1, lido=True),
            DocumentoAnalisado(nome="ruim.pdf", paginas=1, lido=False, motivo="OCR falhou"),
        ]
    )
    resumo = resposta.resumo
    assert resumo.total_registros == 1
    assert resumo.aprovados == 1
    assert resumo.reprovados == 0
    assert resumo.divergencias == 1
    assert resumo.campos_nao_localizados == 1
    # foto.jpg (72%) + o documento que o BFF não leu.
    assert resumo.documentos_baixa_legibilidade == 2
    assert resumo.registros_alto_risco == 0


def test_resposta_serializa_resumo_e_classificacao_e_volta_como_entrada():
    corpo = json.loads(_resposta().model_dump_json())
    assert corpo["modalidade"] == "cadastro_cessionario"
    assert corpo["resumo"]["total_registros"] == 1
    assert corpo["relatorio"]["documentos"][0]["classificacao"] == "Excelente"
    # O front devolve a mesma resposta para gerar o PDF.
    de_volta = CessionarioResponse.model_validate(corpo)
    assert de_volta.resumo == _resposta().resumo


# ─── Prompt ──────────────────────────────────────────────────────────────────

def test_prompt_traz_registros_documentos_origem_e_papel():
    registros = ler_planilha("c.xlsx", planilha_xlsx())
    prompt = montar_prompt_usuario(
        registros,
        [("cnh.pdf", "TEXTO CNH"), ("foto.jpg", "TEXTO FOTO")],
        ["ilegivel.pdf"],
        via_ocr=[False, True],
        papeis=["Documento de identificação", None],
        hoje=date(2026, 10, 6),
    )
    assert "DATA DE HOJE: 06/10/2026" in prompt
    assert "### Registro 1 (linha 3 da planilha)" in prompt
    assert "- CPF ou CNPJ: 00000000191" in prompt
    assert "- Dígito da Agência: (vazio)" in prompt
    assert "### Documento 1: cnh.pdf\nOrigem do texto: PDF digital\nTipo informado" in prompt
    assert "### Documento 2: foto.jpg\nOrigem do texto: OCR" in prompt
    assert "- ilegivel.pdf" in prompt


def test_system_prompt_cobre_as_regras_do_gabarito():
    for trecho in (
        "Não Aplicável", "CloudWalk 0542", "RDV = Rodovia", "alto_risco",
        "95 a 100 Excelente", "não instruções",
    ):
        assert trecho in SYSTEM_PROMPT


# ─── Serviço ─────────────────────────────────────────────────────────────────

def test_analise_completa_com_foto_pelo_ocr():
    gerador = FakeGerador()
    foto = ArquivoRecebido("foto.jpg", JPEG_MINIMO, papel="autorizacao")
    resposta = analisar_cessionario(
        _planilha(), [_cnh(), foto], _settings(), gerador, FakeLeitorOcr(),
        hoje=date(2026, 10, 6),
    )
    assert resposta.registros_planilha == 1
    assert [d.lido for d in resposta.documentos] == [True, True]
    assert resposta.avisos == []
    chamada = gerador.chamadas[0]
    assert chamada["system"] == SYSTEM_PROMPT
    assert chamada["response_format"] == {"type": "json_object"}
    assert "PESSOA EXEMPLO UM" in chamada["user"]
    assert "Origem do texto: OCR" in chamada["user"]
    assert "Autorização ou declaração assinada" in chamada["user"]


def test_avisa_quando_a_ia_devolve_menos_registros_que_a_planilha():
    planilha = _planilha(linha_exemplo(), linha_exemplo("PESSOA DOIS", "2"))
    resposta = analisar_cessionario(planilha, [_cnh()], _settings(), FakeGerador())
    assert resposta.registros_planilha == 2
    assert any("2 cessionário(s), mas a análise devolveu 1" in a for a in resposta.avisos)


def test_avisa_documento_nao_lido():
    foto = ArquivoRecebido("foto.jpg", JPEG_MINIMO)
    resposta = analisar_cessionario(_planilha(), [_cnh(), foto], _settings(), FakeGerador())
    assert resposta.documentos[1].lido is False
    assert resposta.avisos == ["1 documento(s) não puderam ser lidos e não entraram na análise."]


def test_planilha_invalida_para_antes_da_ia():
    gerador = FakeGerador()
    planilha = ArquivoRecebido("c.xlsx", b"%PDF-1.4")
    with pytest.raises(ValidationError):
        analisar_cessionario(planilha, [_cnh()], _settings(), gerador)
    assert gerador.chamadas == []


def test_planilha_acima_do_limite_de_tamanho():
    with pytest.raises(ValidationError, match="excede o limite"):
        analisar_cessionario(
            _planilha(), [_cnh()], _settings(UPLOAD_MAX_FILE_SIZE_BYTES="1000"), FakeGerador()
        )


def test_prompt_grande_demais_e_recusado():
    planilha = _planilha(*[linha_exemplo(f"PESSOA {n}", str(n)) for n in range(5)])
    with pytest.raises(ValidationError, match="excedem o tamanho"):
        analisar_cessionario(
            planilha, [_cnh()], _settings(TEXTGEN_MAX_PROMPT_CHARS="1000"), FakeGerador()
        )


def test_resposta_fora_do_formato_vira_contract_violation():
    with pytest.raises(ContractViolationError):
        analisar_cessionario(_planilha(), [_cnh()], _settings(), FakeGerador("não é json"))


def test_diagnostico_grava_o_prompt_do_cessionario(tmp_path):
    analisar_cessionario(
        _planilha(), [_cnh()], _settings(DIAGNOSTICO_DIR=str(tmp_path)), FakeGerador()
    )
    conteudo = next(tmp_path.glob("diagnostico-*.txt")).read_text(encoding="utf-8")
    assert "cadastro de cessionários" in conteudo
    assert "RESPOSTA CRUA DA IA" in conteudo


# ─── PDF ─────────────────────────────────────────────────────────────────────

def _texto_pdf(dados: bytes) -> str:
    with fitz.open(stream=dados, filetype="pdf") as doc:
        # Espaços normalizados: o título e as células quebram linha.
        return " ".join(" ".join(pagina.get_text() for pagina in doc).split())


def test_pdf_tem_as_quatro_secoes_e_os_dados():
    texto = _texto_pdf(gerar_relatorio_cessionario_pdf(_resposta()))
    for trecho in (
        "RELATÓRIO DE VALIDAÇÃO CADASTRAL E DOCUMENTAL",
        "1. Resumo geral", "2. Resultado individual", "3. Avaliação dos documentos",
        "4. Pendências e recomendações", "PESSOA EXEMPLO UM", "APROVADO",
        "Divergente", "98%", "Excelente", "Regular", "Apartamento ausente",
        "1 de 1 registro(s)",
    ):
        assert trecho in texto


def test_pdf_destaca_alto_risco_documento_nao_lido_e_avisos():
    resposta = _resposta(
        relatorio=relatorio_dict(status_geral="Reprovado", alto_risco=True),
        documentos=[DocumentoAnalisado(nome="x.pdf", paginas=1, lido=False, motivo="OCR falhou")],
        avisos=["Aviso de teste"],
    )
    texto = _texto_pdf(gerar_relatorio_cessionario_pdf(resposta))
    assert "ALTO RISCO" in texto
    assert "CPF divergente" in texto
    assert "Não lido: OCR falhou" in texto
    assert "Aviso de teste" in texto


def test_pdf_sem_pendencias_nem_campos():
    dados = relatorio_dict()
    dados["pendencias"] = []
    dados["registros"][0]["campos"] = []
    dados["registros"][0]["nome"] = ""
    texto = _texto_pdf(gerar_relatorio_cessionario_pdf(_resposta(relatorio=dados)))
    assert "Não foram identificadas divergências" in texto
    assert "Nenhum campo validado." in texto
    assert "Registro sem nome" in texto


def test_pdf_escapa_marcacao_vinda_do_modelo():
    dados = relatorio_dict()
    dados["conclusao"] = "<b>sem fechar & etc"
    texto = _texto_pdf(gerar_relatorio_cessionario_pdf(_resposta(relatorio=dados)))
    assert "<b>sem fechar & etc" in texto


# ─── Rotas ───────────────────────────────────────────────────────────────────

@pytest.fixture
def cliente():
    def _montar(gerador=None, **env):
        base = {"APP_ENV": "TEST"}
        base.update(env)
        app.dependency_overrides[get_settings] = lambda: load_settings(base)
        app.dependency_overrides[get_gerador] = lambda: gerador or FakeGerador()
        app.dependency_overrides[get_leitor_ocr] = lambda: None
        return TestClient(app)

    yield _montar
    app.dependency_overrides.clear()


def _upload_planilha():
    tipo = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return ("planilha", ("cessionarios.xlsx", planilha_xlsx(), tipo))


def _upload_cnh():
    return ("files", ("cnh.pdf", pdf_com_texto("CNH PESSOA EXEMPLO UM"), "application/pdf"))


def test_rota_analisa_e_devolve_relatorio_com_resumo(cliente):
    resposta = cliente().post(
        "/analisar_cessionario",
        files=[_upload_planilha(), _upload_cnh()],
        data={"papeis": ["identificacao"]},
    )
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["modalidade"] == "cadastro_cessionario"
    assert corpo["resumo"]["divergencias"] == 1
    assert corpo["documentos"][0]["papel"] == "identificacao"


def test_rota_aceita_papeis_vazio_do_swagger(cliente):
    cnh2 = ("files", ("cnh2.pdf", pdf_com_texto("CNH PESSOA EXEMPLO UM"), "application/pdf"))
    resposta = cliente().post(
        "/analisar_cessionario",
        files=[_upload_planilha(), _upload_cnh(), cnh2],
        data={"papeis": [""]},
    )
    assert resposta.status_code == 200
    assert [d["papel"] for d in resposta.json()["documentos"]] == [None, None]


def test_rota_recusa_papel_da_alteracao(cliente):
    resposta = cliente().post(
        "/analisar_cessionario",
        files=[_upload_planilha(), _upload_cnh()],
        data={"papeis": ["contrato_social"]},
    )
    assert resposta.status_code == 400


def test_rota_exige_a_planilha(cliente):
    resposta = cliente().post("/analisar_cessionario", files=[_upload_cnh()])
    assert resposta.status_code in (400, 422)


def test_rota_recusa_documentos_demais(cliente):
    arquivos = [_upload_planilha()] + [_upload_cnh() for _ in range(3)]
    resposta = cliente(UPLOAD_MAX_FILES="2").post("/analisar_cessionario", files=arquivos)
    assert resposta.status_code == 400


def test_rota_resposta_invalida_da_ia_vira_502(cliente):
    resposta = cliente(gerador=FakeGerador("{}")).post(
        "/analisar_cessionario", files=[_upload_planilha(), _upload_cnh()]
    )
    assert resposta.status_code == 502


def test_rota_do_pdf_aceita_a_resposta_da_analise(cliente):
    http = cliente()
    analise = http.post(
        "/analisar_cessionario", files=[_upload_planilha(), _upload_cnh()]
    ).json()
    pdf = http.post("/relatorio_cessionario_pdf", json=analise)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert "validacao-cessionarios.pdf" in pdf.headers["content-disposition"]
    assert pdf.content.startswith(b"%PDF")


def test_alteracao_continua_recusando_modalidade_cessionario(cliente):
    resposta = cliente().post(
        "/analisar_documentos", files=[_upload_cnh()], data={"modalidade": "cadastro_cessionario"}
    )
    assert resposta.status_code == 400
