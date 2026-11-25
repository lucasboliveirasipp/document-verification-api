"""Leitura local da planilha de cessionários (XLSX), sem IA e sem custo.

A planilha de importação tem uma linha de instruções antes do cabeçalho; o
cabeçalho é achado pela coluna "Nome ou Razão Social" nas primeiras linhas, e
cada linha preenchida depois dele é um cessionário. Só os 16 campos validados
contra os documentos (``Prompt_Cadastro_Cessionarios.txt``) seguem para o prompt.
"""
from __future__ import annotations

import io
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openpyxl import load_workbook

from src.domain.errors import ValidationError

#: A resposta da IA tem limite de tokens; cada registro ocupa cerca de mil.
MAX_REGISTROS_POR_ANALISE = 5

#: Linhas olhadas à procura do cabeçalho.
_LINHAS_DE_CABECALHO = 10

_ASSINATURA_XLSX = b"PK\x03\x04"

#: Campo validado → cabeçalhos aceitos, já normalizados (sem acento, minúsculo).
CAMPOS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("Tipo de Pessoa", ("tipo de pessoa",)),
    ("Nome ou Razão Social", ("nome ou razao social", "nome")),
    ("CPF ou CNPJ", ("no do cpf ou cnpj", "cpf ou cnpj", "cpf/cnpj", "cpf")),
    ("Data de Nascimento", ("data de nascimento",)),
    ("CEP", ("cep",)),
    ("Endereço", ("endereco (rua / avenida)", "endereco (rua/avenida)", "endereco")),
    ("Número", ("no", "numero")),
    ("Complemento", ("complemento",)),
    ("Bairro", ("bairro",)),
    ("Cidade", ("cidade",)),
    ("Estado", ("estado", "uf")),
    ("Banco", ("banco",)),
    ("Agência", ("agencia bancaria", "agencia")),
    ("Dígito da Agência", ("digito da agencia",)),
    ("Número da Conta", ("no da conta", "numero da conta", "conta")),
    ("Dígito da Conta", ("digito da conta",)),
)
_NOME = "Nome ou Razão Social"
_DOCUMENTO = "CPF ou CNPJ"


@dataclass(frozen=True)
class RegistroPlanilha:
    """Um cessionário da planilha, com os 16 campos na ordem de ``CAMPOS``."""

    linha: int
    campos: Tuple[Tuple[str, str], ...]

    def valor(self, campo: str) -> str:
        return dict(self.campos).get(campo, "")

    @property
    def nome(self) -> str:
        return self.valor(_NOME)

    @property
    def cpf_cnpj(self) -> str:
        return self.valor(_DOCUMENTO)


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return " ".join(sem_acento.lower().split())


def _texto(valor: Any) -> str:
    """Valor da célula como o usuário o vê na planilha."""
    if valor is None:
        return ""
    if isinstance(valor, (datetime, date)):
        return valor.strftime("%d/%m/%Y")
    if isinstance(valor, float) and valor.is_integer():
        return str(int(valor))
    return str(valor).strip()


def _mapear_colunas(cabecalho: Sequence[Any]) -> Optional[Dict[str, int]]:
    """Coluna de cada campo; ``None`` se a linha não é o cabeçalho."""
    normalizados = [_normalizar(_texto(c)) for c in cabecalho]
    colunas: Dict[str, int] = {}
    for campo, aceitos in CAMPOS:
        # O primeiro cabeçalho aceito que existir vence ("Nº" antes de "Nº da Conta").
        for aceito in aceitos:
            if aceito in normalizados:
                colunas[campo] = normalizados.index(aceito)
                break
    if _NOME not in colunas or _DOCUMENTO not in colunas:
        return None
    return colunas


def _celula(linha: Sequence[Any], coluna: Optional[int]) -> str:
    if coluna is None or coluna >= len(linha):
        return ""
    return _texto(linha[coluna])


def validar_arquivo_planilha(nome: str, conteudo: bytes) -> None:
    """Recusa o que não é XLSX, pela assinatura e pela extensão."""
    if not conteudo:
        raise ValidationError(f"A planilha '{nome}' está vazia.")
    # .xlsx e .docx são ZIP: a extensão separa os dois; o conteúdo, o resto.
    if not conteudo.startswith(_ASSINATURA_XLSX) or not nome.lower().endswith(".xlsx"):
        raise ValidationError(f"A planilha '{nome}' não é um arquivo .xlsx válido.")


def ler_planilha(
    nome: str, conteudo: bytes, *, max_registros: int = MAX_REGISTROS_POR_ANALISE
) -> List[RegistroPlanilha]:
    """Lê os cessionários da primeira aba da planilha.

    Raises:
        ValidationError: arquivo inválido, sem cabeçalho, sem registros ou com
            registros demais para uma análise.
    """
    validar_arquivo_planilha(nome, conteudo)
    try:
        livro = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - openpyxl levanta tipos variados
        raise ValidationError(f"A planilha '{nome}' não pôde ser aberta.") from exc

    try:
        linhas = list(livro.worksheets[0].iter_rows(values_only=True))
    finally:
        livro.close()

    colunas = None
    inicio = 0
    for indice, linha in enumerate(linhas[:_LINHAS_DE_CABECALHO]):
        colunas = _mapear_colunas(linha)
        if colunas is not None:
            inicio = indice + 1
            break
    if colunas is None:
        raise ValidationError(
            f"A planilha '{nome}' não tem as colunas 'Nome ou Razão Social' e 'CPF ou CNPJ'."
        )

    registros = []
    for numero, linha in enumerate(linhas[inicio:], start=inicio + 1):
        campos = tuple((campo, _celula(linha, colunas.get(campo))) for campo, _ in CAMPOS)
        registro = RegistroPlanilha(linha=numero, campos=campos)
        if registro.nome or registro.cpf_cnpj:
            registros.append(registro)

    if not registros:
        raise ValidationError(f"A planilha '{nome}' não tem nenhum cessionário preenchido.")
    if len(registros) > max_registros:
        raise ValidationError(
            f"A planilha tem {len(registros)} cessionários; o limite por análise é "
            f"{max_registros}. Divida a planilha."
        )
    return registros
