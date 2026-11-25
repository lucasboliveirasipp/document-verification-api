"""Testes do contrato do parecer e do prompt de Alteração Cadastral."""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError as PydanticValidationError

from src.models.parecer import DECISOES, RESPOSTAS_VERIFICACAO, Parecer
from src.services.prompts.alteracao_cadastral import SYSTEM_PROMPT, montar_prompt_usuario
from tests.unit.parecer_fakes import PARECER_JSON, parecer_dict


def test_parecer_valido_e_respostas_normalizadas():
    parecer = Parecer.model_validate_json(PARECER_JSON)
    q = parecer.quadro_resumo
    assert q.formulario_preenchido_corretamente.resposta == "Sim"
    assert q.assinaturas_conferem_representantes.resposta == "Não"
    assert q.falta_assinatura_administrador.resposta == "Não se aplica"
    assert q.falta_assinatura_administrador.observacao == ""
    assert parecer.orientacoes[0].item == "Assinatura"


@pytest.mark.parametrize("valor", ["SIM", "nao identificado", "Nao_se_aplica", "parcial"])
def test_variacoes_de_resposta_sao_aceitas(valor):
    dados = parecer_dict()
    dados["quadro_resumo"]["gerente_identificado"]["resposta"] = valor
    resposta = Parecer.model_validate(dados).quadro_resumo.gerente_identificado.resposta
    assert resposta in RESPOSTAS_VERIFICACAO


def test_resposta_fora_das_opcoes_e_rejeitada():
    dados = parecer_dict()
    dados["quadro_resumo"]["gerente_identificado"]["resposta"] = "talvez"
    with pytest.raises(PydanticValidationError):
        Parecer.model_validate(dados)


def test_observacao_nula_vira_texto_vazio():
    dados = parecer_dict()
    dados["quadro_resumo"]["gerente_identificado"]["observacao"] = None
    assert Parecer.model_validate(dados).quadro_resumo.gerente_identificado.observacao == ""


def test_campo_texto_nulo_ou_lista_e_tolerado():
    dados = parecer_dict()
    dados["quadro_resumo"]["titular_novo"] = None
    dados["quadro_resumo"]["series_e_titulos"] = ["Série 1", "Série 2"]
    q = Parecer.model_validate(dados).quadro_resumo
    assert q.titular_novo == "Não identificado"
    assert q.series_e_titulos == "Série 1; Série 2"


def test_parecer_sem_quadro_resumo_e_rejeitado():
    with pytest.raises(PydanticValidationError):
        Parecer.model_validate({"orientacoes": []})


def test_json_invalido_e_rejeitado():
    with pytest.raises(PydanticValidationError):
        Parecer.model_validate_json("isto não é json")


def test_system_prompt_pede_json_com_todos_os_campos():
    assert "JSON" in SYSTEM_PROMPT
    dados = parecer_dict()
    campos = set(dados) | set(dados["quadro_resumo"]) | set(dados["resultado"])
    for secao in ("documentos", "partes", "validacoes", "pontos_de_atencao", "orientacoes"):
        campos |= set(dados[secao][0])
    for campo in campos:
        assert f'"{campo}"' in SYSTEM_PROMPT
    for decisao in DECISOES:
        assert decisao in SYSTEM_PROMPT
    assert "ignore" in SYSTEM_PROMPT.lower()


def test_prompt_usuario_lista_documentos_lidos_e_nao_lidos():
    prompt = montar_prompt_usuario([("contrato.pdf", "texto do contrato")], ["formulario.pdf"])
    assert "### Documento 1: contrato.pdf\ntexto do contrato" in prompt
    assert "- formulario.pdf" in prompt
    assert "JSON" in prompt


def test_prompt_usuario_sem_nao_lidos_nao_cita_secao():
    prompt = montar_prompt_usuario([("a.pdf", "t")], [])
    assert "NÃO PUDERAM SER LIDOS" not in prompt


# ─── Seções do gabarito (relatório esperado) ─────────────────────────────────

def test_parecer_completo_no_formato_do_gabarito():
    parecer = Parecer.model_validate(parecer_dict())
    assert parecer.resultado.decisao == "Apto com ressalvas"
    assert parecer.quadro_resumo.local_e_data_formulario == "Cidade Exemplo - SP | 01/01/2026"
    assert parecer.partes[0].documento == "CNPJ 00.000.000/0001-00"
    assert parecer.validacoes[0].criterio == "Poderes de administração"
    assert parecer.pontos_de_atencao[0].acao_recomendada == "Conferir a imagem original."
    assert parecer.documentos[0].finalidade == "Comprova os poderes do administrador."
    assert parecer.conclusao.startswith("Prosseguir")


@pytest.mark.parametrize(
    "valor, esperado",
    [("apto", "Apto"), ("APTO_COM_RESSALVAS", "Apto com ressalvas"),
     ("Inapto", "Inapto"), ("inconclusivo", "Inconclusivo")],
)
def test_variacoes_da_decisao_sao_aceitas(valor, esperado):
    dados = parecer_dict()
    dados["resultado"]["decisao"] = valor
    assert Parecer.model_validate(dados).resultado.decisao == esperado


def test_decisao_fora_das_opcoes_e_rejeitada():
    dados = parecer_dict()
    dados["resultado"]["decisao"] = "Aprovado talvez"
    with pytest.raises(PydanticValidationError):
        Parecer.model_validate(dados)


def test_parecer_sem_resultado_e_rejeitado():
    # A decisão é a manchete do relatório: sem ela o parecer não é publicado.
    dados = parecer_dict()
    del dados["resultado"]
    with pytest.raises(PydanticValidationError):
        Parecer.model_validate(dados)


def test_secoes_de_detalhe_ausentes_viram_vazias():
    dados = parecer_dict()
    for secao in ("sumario_executivo", "documentos", "partes", "validacoes",
                  "pontos_de_atencao", "conclusao"):
        del dados[secao]
    del dados["quadro_resumo"]["local_e_data_formulario"]
    parecer = Parecer.model_validate(dados)
    assert parecer.partes == [] and parecer.pontos_de_atencao == []
    assert parecer.sumario_executivo == "" and parecer.conclusao == ""
    assert parecer.quadro_resumo.local_e_data_formulario == "Não identificado"


def test_campos_nulos_nas_secoes_viram_texto_vazio():
    dados = parecer_dict()
    dados["partes"][0]["representante"] = None
    dados["sumario_executivo"] = None
    parecer = Parecer.model_validate(dados)
    assert parecer.partes[0].representante == ""
    assert parecer.sumario_executivo == ""



# ─── Achados do lote real de 22/09 ───────────────────────────────────────────

def test_prompt_usuario_informa_a_data_de_hoje():
    # Sem a data, o modelo tomou 19/05/2026 como "data futura" em 22/09/2026.
    prompt = montar_prompt_usuario([("a.pdf", "t")], [], hoje=date(2026, 9, 22))
    assert "DATA DE HOJE: 22/09/2026" in prompt


def test_system_prompt_manda_usar_a_data_de_hoje_e_cobrar_o_conjunto_minimo():
    # O lote real veio sem o contrato social da cedente e sem a CNH de um
    # representante, e o parecer marcou os representantes como consistentes.
    texto = SYSTEM_PROMPT.lower()
    assert "data de hoje" in texto
    assert "conjunto mínimo" in texto
    for documento in ("formulário", "contrato social", "identificação"):
        assert documento in texto


def test_system_prompt_cobre_os_pontos_do_gabarito_que_o_lote_real_perdeu():
    # 2ª rodada (7 documentos): não viu o "CONJUGES" do Formulário2, não
    # apontou a razão social abreviada e acusou como ausente um dado que ele
    # mesmo preencheu.
    texto = SYSTEM_PROMPT.lower()
    for regra in (
        "texto digital sobre o escaneado",
        "mais de uma versão",
        "razão social",
        "abreviad",
        "reconhecimento de firma",
        "contradiga",
    ):
        assert regra in texto, regra


def test_json_pede_a_decisao_depois_da_analise():
    # O modelo escreve na ordem do JSON: decidir antes de validar vira
    # justificar uma decisão já tomada.
    # "resultado" também é campo de cada validação: busca a chave de topo.
    posicao = {chave: SYSTEM_PROMPT.index(f'"{chave}": {abre}') for chave, abre in (
        ("quadro_resumo", "{"), ("documentos", "["), ("validacoes", "["),
        ("pontos_de_atencao", "["), ("resultado", "{"), ("sumario_executivo", '"'),
    )}
    assert posicao["pontos_de_atencao"] < posicao["resultado"]
    assert posicao["validacoes"] < posicao["sumario_executivo"]
    assert posicao["quadro_resumo"] < posicao["documentos"]


def test_system_prompt_trata_relatorio_previo_como_referencia():
    texto = SYSTEM_PROMPT.lower()
    assert "relatório prévio" in texto
    assert "não como evidência" in texto


def test_system_prompt_cobre_as_conclusoes_que_o_pacote_final_errou():
    # Pacote final (22/09 17:22): achou o "CONJUGES", mas mandou confirmar o
    # parentesco com o cliente; achou a data de 2025 na firma, mas marcou
    # "Conferida"; disse que a razão social do formulário "confere
    # exatamente" porque comparou o contrato com o nome que ele mesmo
    # completou no quadro-resumo.
    texto = SYSTEM_PROMPT.lower()
    for regra in (
        "parentesco não se aplica",
        "conferência da imagem original",
        "transcreva",
        "exatamente como está escrito no formulário",
    ):
        assert regra in texto, regra


def test_system_prompt_explica_a_ordem_das_datas_do_reconhecimento_de_firma():
    # Atualização de 23/09: o modelo achou "aceitável" a firma da cedente
    # reconhecida um ano ANTES do formulário e marcou como suspeita a do
    # cessionário, reconhecida um dia DEPOIS, que é o normal.
    texto = SYSTEM_PROMPT.lower()
    assert "no mesmo dia ou depois da assinatura" in texto
    assert "data anterior à do formulário é impossível" in texto


def test_system_prompt_explica_o_tipo_informado_no_envio():
    texto = SYSTEM_PROMPT.lower()
    assert "tipo informado no envio" in texto
    assert "confirme pelo conteúdo" in texto
