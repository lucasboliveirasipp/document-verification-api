# tests/conftest.py
from __future__ import annotations
import os
import sys
from pathlib import Path
import pytest

# ==============================================================================
# 1) Descobrir a raiz do repositório e injetar caminhos no sys.path
# ==============================================================================
THIS_FILE = Path(__file__).resolve()
TESTS_DIR = THIS_FILE.parent
ROOT = TESTS_DIR.parent
SRC = ROOT / "src"

paths_to_add = []
if SRC.exists():
    paths_to_add.append(str(SRC))
paths_to_add.append(str(ROOT))

for p in paths_to_add:
    if p not in sys.path:
        sys.path.insert(0, p)

# ==============================================================================
# 2) Variáveis de ambiente padrão para modo de teste
# ==============================================================================
# Somente valores sintéticos e neutros. Nenhum contrato real da Gateway e nenhum
# resquício do agente de Reembolso (indexador/Q&A) é semeado aqui.
DEFAULT_ENV = {
    "ENV": os.getenv("ENV", "test"),
    "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO"),
    "APP_TITLE": "API Agente Reconhecimento de Documentação de Cadastro - Hub BFF",
    "APP_ROOT_PATH": "/api-reconhecimento-documentacao-cadastro",
    "CORS_ALLOWED_ORIGINS": "",
    "APIM_WORKFLOW_URL": "https://wf.test.example.com",
    "APIM_STATUS_URL": "https://status.test.example.com",
    "APIM_FILE_MANAGER": "https://fm.test.example.com",
    "CONTAINER_ID": "test-container-id",
    "STEP_WORKFLOW_CODE": "TEST_STEP_CODE",
    "WORKFLOW_CODE": "TEST_WF_CODE",
    "TENANTID": "test-tenant",
    "GATEWAYAPPID": "test-client-id",
    "GATEWAYCLIENTSECRET": "test-secret",
    "GATEWAYAPPIDURI": "api://test-app",
}

for k, v in DEFAULT_ENV.items():
    if os.getenv(k) is None:
        os.environ[k] = v

# ==============================================================================
# 3) Fixture autouse
# ==============================================================================
@pytest.fixture(autouse=True)
def _setup_test_env(monkeypatch):
    test_envs = {
        "GATEWAY_MOCK": "1",
        "TEST_FAKE_BEARER": "fake-token-for-tests",
    }
    for k, v in test_envs.items():
        monkeypatch.setenv(k, v)
    yield

# ==============================================================================
# 4) Fixtures básicas
# ==============================================================================
@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="function")
def client():
    from fastapi.testclient import TestClient
    from src.main import app
    return TestClient(app)


os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
