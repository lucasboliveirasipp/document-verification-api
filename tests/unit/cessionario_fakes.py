"""Planilha e relatório sintéticos de cessionários (nenhum dado real)."""
from __future__ import annotations

import io
import json
from datetime import datetime

from openpyxl import Workbook

#: Mesmo desenho da planilha de importação: linha de instruções, cabeçalho
#: com espaços sobrando e colunas que não entram na validação.
CABECALHO = [
    "Código da Parceria", "Tipo de Pessoa", "Nome ou Razão Social", "Nº do CPF ou CNPJ",
    "Data de Nascimento", "CEP", " Endereço Residencial ou Comercial", "Complemento",
    "Endereço (Rua / Avenida)", "Nº", " Bairro", " Cidade", " Estado", "Banco",
    "Agência Bancária", " Dígito da Agência", "Nº da Conta", " Dígito da Conta",
]


def linha_exemplo(nome="PESSOA EXEMPLO UM", cpf="00000000191"):
    return [
        "PARC01", "Física", nome, cpf, "01/02/1990", "01001000", "Residencial", "BLOCO 1 AP 2",
        "RUA EXEMPLO", 100, "CENTRO", "SAO PAULO", "SP", 999, "0001", None, "1234567", "8",
    ]


def planilha_xlsx(*linhas, instrucoes=True, cabecalho=None):
    """XLSX em memória; sem ``linhas``, um registro de exemplo."""
    livro = Workbook()
    aba = livro.active
    aba.title = "Cessionários"
    if instrucoes:
        aba.append([None, None, None, "Somente Nº, Sem ponto, traço ou barra"])
    aba.append(cabecalho or CABECALHO)
    for linha in linhas or (linha_exemplo(),):
        aba.append(linha)
    saida = io.BytesIO()
    livro.save(saida)
    return saida.getvalue()


def planilha_com_data_nativa():
    linha = linha_exemplo()
    linha[4] = datetime(1990, 2, 1)
    return planilha_xlsx(linha)


def relatorio_dict(status_geral="Aprovado", alto_risco=False):
    return {
        "registros": [
            {
                "nome": "PESSOA EXEMPLO UM",
                "cpf_cnpj": "000.000.001-91",
                "status_geral": status_geral,
                "alto_risco": alto_risco,
                "motivo_alto_risco": "CPF divergente" if alto_risco else None,
                "campos": [
                    {"campo": "Nome ou Razão Social", "valor_planilha": "PESSOA EXEMPLO UM",
                     "valor_documento": "PESSOA EXEMPLO UM", "status": "validado",
                     "observacao": "CNH"},
                    {"campo": "Complemento", "valor_planilha": "BLOCO 1 AP 2",
                     "valor_documento": "BL 1", "status": "Divergente",
                     "observacao": "Apartamento ausente"},
                    {"campo": "Dígito da Agência", "valor_planilha": "",
                     "valor_documento": "Não localizado", "status": "N/A",
                     "observacao": "Campo opcional"},
                    {"campo": "Bairro", "valor_planilha": "CENTRO",
                     "valor_documento": None, "status": "nao localizado", "observacao": None},
                ],
            }
        ],
        "documentos": [
            {"nome": "cnh.pdf", "tipo": "CNH", "legibilidade": "98%", "observacoes": "Digital"},
            {"nome": "foto.jpg", "tipo": "Declaração", "legibilidade": 72.4,
             "observacoes": "Sombra"},
        ],
        "pendencias": [
            {"cliente": "PESSOA EXEMPLO UM", "campo": "Complemento",
             "descricao": "Apartamento ausente no comprovante", "acao_recomendada": "Confirmar"},
        ],
        "conclusao": "Documentação suporta a validação com ressalvas.",
    }


RELATORIO_JSON = json.dumps(relatorio_dict(), ensure_ascii=False)
