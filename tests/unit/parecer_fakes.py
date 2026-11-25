"""Parecer sintético válido (nenhum dado real)."""
from __future__ import annotations

import json


def parecer_dict():
    verificacao = {"resposta": "Sim", "observacao": "Conferido no formulário."}
    return {
        "quadro_resumo": {
            "tipo_alteracao": "Troca de titularidade",
            "series_e_titulos": "Série 1, do título 10 ao título 12",
            "subscritor_antigo": "Empresa Exemplo A",
            "subscritor_novo": "Empresa Exemplo B",
            "titular_antigo": "Pessoa Exemplo A",
            "titular_novo": "Pessoa Exemplo B",
            "forma_cobranca": "Débito em conta",
            "formulario_preenchido_corretamente": verificacao,
            "assinaturas_conferem_representantes": {"resposta": "não", "observacao": "Divergente."},
            "falta_assinatura_administrador": {"resposta": "N/A"},
            "gerente_identificado": verificacao,
            "local_e_data_formulario": "Cidade Exemplo - SP | 01/01/2026",
        },
        "orientacoes": [{"item": "Assinatura", "resposta": "Solicitar nova assinatura."}],
        "resultado": {
            "decisao": "APTO COM RESSALVAS",
            "justificativa": "Documentação suporta a solicitação após tratar a assinatura.",
        },
        "sumario_executivo": "Documentos suficientes para a troca de titularidade.",
        "documentos": [
            {"nome": "contrato.pdf", "finalidade": "Comprova os poderes do administrador.",
             "status": "Conferido"},
            {"nome": "inventado.pdf", "finalidade": "Não existe no lote.", "status": "Utilizado"},
        ],
        "partes": [
            {"papel": "Cedente", "nome": "Empresa Exemplo A", "documento": "CNPJ 00.000.000/0001-00",
             "representante": "Pessoa Exemplo A", "evidencia": "Contrato social cláusula 5."},
        ],
        "validacoes": [
            {"criterio": "Poderes de administração", "resultado": "Conferidos",
             "consideracao": "Administração isolada."},
        ],
        "pontos_de_atencao": [
            {"ponto": "Reconhecimento de firma pouco legível", "risco": "Médio",
             "acao_recomendada": "Conferir a imagem original."},
        ],
        "conclusao": "Prosseguir após sanear a assinatura.",
    }


PARECER_JSON = json.dumps(parecer_dict(), ensure_ascii=False)
