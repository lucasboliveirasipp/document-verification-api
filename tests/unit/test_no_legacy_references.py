"""Guarda automatizada contra reintrodução de legado e IA direta (Task 2.5).

Este teste falha a suíte se qualquer código executável sob ``src/`` voltar a
referenciar:

* identidade/contratos do agente de Reembolso (identificadores herdados);
* SDKs ou endpoints diretos de provedores de IA (Gemini, OpenAI, Vertex, etc.),
  o que violaria a regra Gateway-only AI;
* os códigos de indexação/Q&A herdados que não pertencem ao workflow documental.

A varredura ignora comentários e docstrings: apenas *tokens de código*
(nomes/strings efetivamente usados na lógica) são considerados, para evitar
falsos positivos em explicações. A implementação percorre a AST e inspeciona
identificadores e literais de string de código, pulando módulos de docstring.

_Requirements: 2.1-2.3, 4.4, 13.5_
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC_DIR = Path(__file__).resolve().parents[2] / "src"

# Termos proibidos em código executável. Comparação case-insensitive.
FORBIDDEN_SUBSTRINGS = (
    "reembolso",
    "gemini",
    "generativeai",
    "api.openai.com",
    "openai.azure.com",
    "vertexai",
    "anthropic",
    "wf_indexer_legado",
    "cd_indexer",
    "text-embedding-3-large",
    "genai-qa",
)


def _python_files() -> list[Path]:
    return [
        p
        for p in SRC_DIR.rglob("*.py")
        if "__pycache__" not in p.parts
    ]


class _CodeTokenCollector(ast.NodeVisitor):
    """Coleta identificadores e literais de string que são *código*.

    Docstrings de módulo, classe e função são deliberadamente ignoradas, pois
    representam documentação e podem citar termos proibidos ao explicar o que
    NÃO deve ser feito.
    """

    def __init__(self) -> None:
        self.tokens: list[str] = []

    def _skip_docstring(self, node: ast.AST) -> None:
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            # Marca a docstring para não visitá-la como string de código.
            body[0]._is_docstring = True  # type: ignore[attr-defined]

    def visit_Module(self, node: ast.Module) -> None:
        self._skip_docstring(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._skip_docstring(node)
        self.tokens.append(node.name)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._skip_docstring(node)
        self.tokens.append(node.name)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._skip_docstring(node)
        self.tokens.append(node.name)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        self.tokens.append(node.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.tokens.append(node.attr)
        self.generic_visit(node)

    def visit_alias(self, node: ast.alias) -> None:
        self.tokens.append(node.name)
        if node.asname:
            self.tokens.append(node.asname)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.visit_alias(alias)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            self.tokens.append(node.module)
        for alias in node.names:
            self.visit_alias(alias)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and not getattr(node, "_is_docstring", False):
            self.tokens.append(node.value)


def _collect_code_tokens(source: str) -> list[str]:
    tree = ast.parse(source)
    collector = _CodeTokenCollector()
    collector.visit(tree)
    return collector.tokens


@pytest.mark.parametrize("py_file", _python_files(), ids=lambda p: str(p.name))
def test_no_forbidden_tokens_in_executable_code(py_file: Path):
    source = py_file.read_text(encoding="utf-8")
    tokens = _collect_code_tokens(source)
    haystack = "\n".join(tokens).lower()

    offenders = [term for term in FORBIDDEN_SUBSTRINGS if term in haystack]
    assert not offenders, (
        f"Referências proibidas em código executável de {py_file.name}: {offenders}. "
        "IA direta e contratos herdados de Reembolso não são permitidos "
        "(regra Gateway-only AI)."
    )


#: Pacotes de SDK de IA que nunca podem ser importados (regra Gateway-only AI).
#: Os campos ``openai_api_*`` do payload da Gateway são permitidos: são nomes do
#: contrato do workflow, não uso direto de provedor.
FORBIDDEN_IMPORT_ROOTS = ("openai", "langchain_openai", "anthropic", "google", "vertexai")


def _imported_roots(source: str) -> set:
    roots = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_import_guard_detects_ai_sdk():
    assert "openai" in _imported_roots("import openai\n")
    assert "google" in _imported_roots("from google.generativeai import x\n")


@pytest.mark.parametrize("py_file", _python_files(), ids=lambda p: str(p.name))
def test_no_ai_sdk_imports(py_file: Path):
    roots = _imported_roots(py_file.read_text(encoding="utf-8"))
    offenders = sorted(roots.intersection(FORBIDDEN_IMPORT_ROOTS))
    assert not offenders, (
        f"SDK de IA importado em {py_file.name}: {offenders} (regra Gateway-only AI)."
    )
