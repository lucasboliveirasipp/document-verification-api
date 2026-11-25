"""Testes da leitura da planilha de cessionários (XLSX sintético)."""
from __future__ import annotations

import pytest

from src.domain.errors import ValidationError
from src.services.planilha import CAMPOS, ler_planilha
from tests.unit.cessionario_fakes import (
    linha_exemplo,
    planilha_com_data_nativa,
    planilha_xlsx,
)


def test_le_os_16_campos_pulando_a_linha_de_instrucoes():
    registros = ler_planilha("cessionarios.xlsx", planilha_xlsx())
    assert len(registros) == 1
    registro = registros[0]
    assert registro.linha == 3
    assert [campo for campo, _ in registro.campos] == [campo for campo, _ in CAMPOS]
    assert registro.nome == "PESSOA EXEMPLO UM"
    assert registro.cpf_cnpj == "00000000191"
    assert registro.valor("Endereço") == "RUA EXEMPLO"
    assert registro.valor("Bairro") == "CENTRO"


def test_numeros_viram_texto_e_celula_vazia_vira_vazio():
    registro = ler_planilha("c.xlsx", planilha_xlsx())[0]
    assert registro.valor("Número") == "100"
    assert registro.valor("Banco") == "999"
    assert registro.valor("Agência") == "0001"
    assert registro.valor("Dígito da Agência") == ""
    assert registro.valor("Número da Conta") == "1234567"


def test_data_nativa_do_excel_sai_em_dd_mm_aaaa():
    registro = ler_planilha("c.xlsx", planilha_com_data_nativa())[0]
    assert registro.valor("Data de Nascimento") == "01/02/1990"


def test_cabecalho_na_primeira_linha_tambem_funciona():
    registros = ler_planilha("c.xlsx", planilha_xlsx(instrucoes=False))
    assert registros[0].linha == 2


def test_linhas_em_branco_sao_ignoradas():
    vazia = [None] * len(linha_exemplo())
    registros = ler_planilha(
        "c.xlsx", planilha_xlsx(linha_exemplo(), vazia, linha_exemplo("PESSOA DOIS", "1"))
    )
    assert [r.nome for r in registros] == ["PESSOA EXEMPLO UM", "PESSOA DOIS"]


def test_coluna_ausente_vira_valor_vazio():
    cabecalho = ["Nome ou Razão Social", "CPF"]
    registro = ler_planilha("c.xlsx", planilha_xlsx(["FULANO", "123"], cabecalho=cabecalho))[0]
    assert registro.cpf_cnpj == "123"
    assert registro.valor("CEP") == ""


def test_recusa_planilha_com_mais_registros_que_o_limite():
    linhas = [linha_exemplo(f"PESSOA {n}", str(n)) for n in range(3)]
    with pytest.raises(ValidationError, match="limite por análise é 2"):
        ler_planilha("c.xlsx", planilha_xlsx(*linhas), max_registros=2)


def test_recusa_planilha_sem_registros():
    with pytest.raises(ValidationError, match="nenhum cessionário"):
        ler_planilha("c.xlsx", planilha_xlsx([None] * len(linha_exemplo())))


def test_recusa_planilha_sem_as_colunas_de_nome_e_documento():
    with pytest.raises(ValidationError, match="não tem as colunas"):
        ler_planilha("c.xlsx", planilha_xlsx(["x"], cabecalho=["Outra coluna"]))


@pytest.mark.parametrize(
    "nome, conteudo",
    [
        ("c.xlsx", b"%PDF-1.4 nao e planilha"),
        ("c.docx", b"PK\x03\x04qualquer"),
        ("c.xls", b"\xd0\xcf\x11\xe0 formato antigo"),
    ],
)
def test_recusa_o_que_nao_e_xlsx(nome, conteudo):
    with pytest.raises(ValidationError, match="não é um arquivo .xlsx válido"):
        ler_planilha(nome, conteudo)


def test_recusa_planilha_vazia():
    with pytest.raises(ValidationError, match="está vazia"):
        ler_planilha("c.xlsx", b"")


def test_recusa_zip_que_nao_abre_como_planilha():
    with pytest.raises(ValidationError, match="não pôde ser aberta"):
        ler_planilha("c.xlsx", b"PK\x03\x04" + b"\x00" * 50)
