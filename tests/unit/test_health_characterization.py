"""Testes de caracterização dos health checks e da montagem da aplicação.

Estes testes fixam (fotografam) o comportamento que a **esteira** depende e que
NÃO pode regredir durante a remoção do legado de Reembolso:

* ``/health/liveness`` e ``/health/readiness`` permanecem públicos (sem auth);
* seus payloads mínimos permanecem estáveis;
* a aplicação FastAPI sobe e expõe o OpenAPI.

Diferente de ``test_main_cover``, este arquivo é **agnóstico à identidade** do
agente: não afirma título, ``root_path`` ou origens de CORS específicas, pois
esses valores mudam legitimamente na migração para Reconhecimento de Documentação de Cadastro.
O objetivo é garantir que a migração de identidade não quebre o contrato de
saúde exigido pela esteira.
"""
from fastapi.testclient import TestClient

from src.main import app


LIVENESS_CONTRACT = {
    "title": "Estado da aplicação",
    "description": "Utilizado pela esteira para validar o estado da aplicação",
    "version": "v1.0",
    "status": "OK",
}

READINESS_CONTRACT = {
    "title": "Status da prontidão",
    "description": "Utilizado pela esteira para validar a prontidão da aplicação",
    "version": "v1.0",
    "status": "OK",
}


def test_liveness_contract_is_stable_and_public():
    client = TestClient(app)
    resp = client.get("/health/liveness")
    assert resp.status_code == 200
    assert resp.json() == LIVENESS_CONTRACT


def test_readiness_contract_is_stable_and_public():
    client = TestClient(app)
    resp = client.get("/health/readiness")
    assert resp.status_code == 200
    assert resp.json() == READINESS_CONTRACT


def test_application_boots_and_exposes_openapi():
    client = TestClient(app)
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    body = resp.json()
    assert "paths" in body
    # Os health checks devem estar registrados no schema público.
    assert "/health/liveness" in body["paths"]
    assert "/health/readiness" in body["paths"]


def test_health_routes_do_not_require_authentication():
    # Sem qualquer header de autorização, os health checks respondem 200.
    client = TestClient(app)
    assert client.get("/health/liveness").status_code == 200
    assert client.get("/health/readiness").status_code == 200
