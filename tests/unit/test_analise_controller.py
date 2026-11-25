"""Testes das rotas de análise (dependências sobrescritas, sem Gateway)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.controllers.analise_controller import (
    get_gerador,
    get_leitor_ocr,
    get_settings,
    get_transport,
)
from src.domain.errors import DependencyUnavailableError
from src.main import app
from src.services.ocr import LeitorOcr
from src.settings import load_settings
from tests.unit.parecer_fakes import PARECER_JSON
from tests.unit.pdf_fakes import JPEG_MINIMO, pdf_com_texto


class FakeGerador:
    def __init__(self, resposta=PARECER_JSON, erro=None):
        self.resposta = resposta
        self.erro = erro

    def gerar_texto(self, system_prompt, user_content, *, response_format=None, max_tokens=None):
        if self.erro:
            raise self.erro
        return self.resposta


class FakeLeitorOcr:
    disponivel = True

    def ler(self, arquivo, *, timeout_seconds):
        return "texto lido por OCR"


@pytest.fixture
def cliente():
    def _montar(gerador=None, leitor_ocr=None, **env):
        base = {"APP_ENV": "TEST"}
        base.update(env)
        app.dependency_overrides[get_settings] = lambda: load_settings(base)
        app.dependency_overrides[get_gerador] = lambda: gerador or FakeGerador()
        app.dependency_overrides[get_leitor_ocr] = lambda: leitor_ocr
        return TestClient(app)

    yield _montar
    app.dependency_overrides.clear()


def _pdf(nome="contrato.pdf"):
    return ("files", (nome, pdf_com_texto("Contrato social da empresa exemplo"), "application/pdf"))


def test_analisar_documentos_devolve_parecer(cliente):
    resposta = cliente().post("/analisar_documentos", files=[_pdf()])
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["parecer"]["quadro_resumo"]["tipo_alteracao"] == "Troca de titularidade"
    assert corpo["documentos"][0]["lido"] is True



# ─── Modalidade (escolhida no Hub) ───────────────────────────────────────────

def test_sem_modalidade_assume_alteracao_cadastral(cliente):
    # O front atual não manda o campo: continua funcionando.
    resposta = cliente().post("/analisar_documentos", files=[_pdf()])
    assert resposta.json()["modalidade"] == "alteracao_cadastral"


def test_modalidade_informada_volta_na_resposta(cliente):
    resposta = cliente().post(
        "/analisar_documentos", files=[_pdf()], data={"modalidade": "alteracao_cadastral"}
    )
    assert resposta.status_code == 200
    assert resposta.json()["modalidade"] == "alteracao_cadastral"


def test_modalidade_nao_habilitada_e_recusada_antes_da_leitura(cliente):
    gerador = FakeGerador(erro=AssertionError("não deveria chamar a Gateway"))
    resposta = cliente(gerador).post(
        "/analisar_documentos", files=[_pdf()], data={"modalidade": "cadastro_cessionario"}
    )
    assert resposta.status_code == 400
    assert "Modalidade" in resposta.json()["message"]


def test_relatorio_pdf_recusa_modalidade_nao_habilitada(cliente):
    client = cliente()
    analise = client.post("/analisar_documentos", files=[_pdf()]).json()
    analise["modalidade"] = "cadastro_cessionario"
    assert client.post("/relatorio_pdf", json=analise).status_code == 400

def test_documento_digitalizado_passa_pelo_leitor_de_ocr(cliente):
    resposta = cliente(leitor_ocr=FakeLeitorOcr()).post(
        "/analisar_documentos",
        files=[_pdf(), ("files", ("cnh.jpg", JPEG_MINIMO, "image/jpeg"))],
    )
    assert resposta.status_code == 200
    assert [d["lido"] for d in resposta.json()["documentos"]] == [True, True]


def test_excesso_de_arquivos_e_recusado_antes_da_leitura(cliente):
    resposta = cliente(UPLOAD_MAX_FILES="1").post(
        "/analisar_documentos", files=[_pdf("a.pdf"), _pdf("b.pdf")]
    )
    assert resposta.status_code == 400
    assert "no máximo 1" in resposta.json()["message"]


def test_tipo_invalido_e_recusado(cliente):
    resposta = cliente().post(
        "/analisar_documentos",
        files=[("files", ("planilha.pdf", b"PK\x03\x04", "application/pdf"))],
    )
    assert resposta.status_code == 400


def test_gateway_indisponivel_vira_503_generico(cliente):
    gerador = FakeGerador(erro=DependencyUnavailableError("detalhe tecnico", retryable=True))
    resposta = cliente(gerador).post("/analisar_documentos", files=[_pdf()])
    assert resposta.status_code == 503
    assert "detalhe tecnico" not in resposta.text


def test_relatorio_pdf_a_partir_da_resposta_da_analise(cliente):
    client = cliente()
    analise = client.post("/analisar_documentos", files=[_pdf()]).json()
    resposta = client.post("/relatorio_pdf", json=analise)
    assert resposta.status_code == 200
    assert resposta.headers["content-type"] == "application/pdf"
    assert "attachment" in resposta.headers["content-disposition"]
    assert resposta.content.startswith(b"%PDF")


def test_relatorio_pdf_com_corpo_invalido_da_422(cliente):
    assert cliente().post("/relatorio_pdf", json={"parecer": {}}).status_code == 422


def _limpar_caches():
    for dependencia in (get_settings, get_transport, get_gerador, get_leitor_ocr):
        dependencia.cache_clear()


def test_dependencias_da_gateway_sao_unicas_por_processo():
    _limpar_caches()
    try:
        assert get_gerador() is get_gerador()
        assert get_transport() is get_transport()
        assert isinstance(get_leitor_ocr(), LeitorOcr)
        assert get_leitor_ocr() is get_leitor_ocr()
    finally:
        _limpar_caches()


# ─── Papel de cada arquivo (campo de upload da tela do Hub) ──────────────────
# A aba Pessoa Jurídica tem um campo por tipo de documento; o front manda um
# "papeis" por arquivo, na mesma ordem de "files".

def test_papel_de_cada_arquivo_volta_na_resposta(cliente):
    resposta = cliente().post(
        "/analisar_documentos",
        files=[_pdf("form.pdf"), _pdf("contrato.pdf")],
        data={"papeis": ["formulario", "contrato_social"]},
    )
    assert resposta.status_code == 200
    assert [d["papel"] for d in resposta.json()["documentos"]] == ["formulario", "contrato_social"]


def test_sem_papeis_o_papel_fica_vazio(cliente):
    resposta = cliente().post("/analisar_documentos", files=[_pdf()])
    assert resposta.json()["documentos"][0]["papel"] is None


def test_papeis_vazio_do_swagger_conta_como_sem_papeis(cliente):
    resposta = cliente().post(
        "/analisar_documentos", files=[_pdf("a.pdf"), _pdf("b.pdf")], data={"papeis": [""]}
    )
    assert resposta.status_code == 200
    assert [d["papel"] for d in resposta.json()["documentos"]] == [None, None]


def test_quantidade_de_papeis_diferente_da_de_arquivos_e_recusada(cliente):
    resposta = cliente().post(
        "/analisar_documentos", files=[_pdf("a.pdf"), _pdf("b.pdf")], data={"papeis": ["formulario"]}
    )
    assert resposta.status_code == 400


def test_papel_desconhecido_e_recusado(cliente):
    resposta = cliente().post(
        "/analisar_documentos", files=[_pdf()], data={"papeis": ["boleto"]}
    )
    assert resposta.status_code == 400
