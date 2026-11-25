from fastapi.testclient import TestClient

from src.domain.errors import ContractViolationError, ValidationError
from src.main import app, create_app
from src.settings import load_settings


def _app_de_teste():
    application = create_app(load_settings({"APP_ENV": "TEST"}))

    @application.get("/_teste/validacao")
    def _validacao():
        raise ValidationError("Envie ao menos um documento.")

    @application.get("/_teste/contrato")
    def _contrato():
        raise ContractViolationError("detalhe interno HTTP 400")

    @application.get("/_teste/inesperado")
    def _inesperado():
        raise RuntimeError("segredo interno")

    return application


def test_validation_error_returns_400_with_its_message():
    response = TestClient(_app_de_teste()).get("/_teste/validacao")
    assert response.status_code == 400
    assert response.json() == {"message": "Envie ao menos um documento."}


class _LoggerEspiao:
    def __init__(self):
        self.eventos = []

    def warning(self, event, **fields):
        self.eventos.append((event, fields))

    def error(self, event, **fields):
        self.eventos.append((event, fields))


def test_rejected_request_log_never_carries_user_message(monkeypatch):
    # Mensagens de validação citam o nome do arquivo, que pode conter o nome
    # de uma pessoa: vão para o cliente, mas não para o log.
    espiao = _LoggerEspiao()
    monkeypatch.setattr("src.main._logger", espiao)
    application = _app_de_teste()

    @application.get("/_teste/arquivo")
    def _arquivo():
        raise ValidationError("O documento 'CNH FULANO DE TAL.pdf' está vazio.")

    TestClient(application).get("/_teste/arquivo")
    evento, campos = espiao.eventos[-1]
    assert evento == "request_rejected"
    assert "FULANO" not in str(campos)
    assert campos["category"] == "VALIDATION"


def test_failed_request_log_keeps_internal_detail(monkeypatch):
    espiao = _LoggerEspiao()
    monkeypatch.setattr("src.main._logger", espiao)
    TestClient(_app_de_teste()).get("/_teste/contrato")
    evento, campos = espiao.eventos[-1]
    assert evento == "request_failed"
    assert campos["detail"] == "detalhe interno HTTP 400"


def test_contract_violation_returns_502_without_internal_detail():
    response = TestClient(_app_de_teste()).get("/_teste/contrato")
    assert response.status_code == 502
    assert "detalhe interno" not in response.text


def test_unexpected_error_returns_generic_500():
    client = TestClient(_app_de_teste(), raise_server_exceptions=False)
    response = client.get("/_teste/inesperado")
    assert response.status_code == 500
    assert "segredo" not in response.text


def test_cors_allows_only_needed_methods_and_headers():
    cors = next(m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware")
    assert cors.kwargs["allow_methods"] == ["GET", "POST"]
    assert cors.kwargs["allow_headers"] == ["Authorization", "Content-Type"]


def test_app_metadata_and_openapi():
    # A identidade agora é a do agente Reconhecimento de Documentação de Cadastro.
    assert "Reconhecimento de Documentação de Cadastro" in app.title
    assert "Reembolso" not in app.title
    assert app.openapi_url == "/openapi.json"

    client = TestClient(app)
    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    assert openapi.json()["info"]["title"] == app.title

    assert client.get("/docs").status_code == 200
    assert client.get("/redoc").status_code == 200


def test_default_root_path_is_the_new_agent():
    assert app.root_path == "/api-reconhecimento-documentacao-cadastro"
    assert "reembolso" not in app.root_path.lower()


def test_cors_middleware_present_and_health_alive():
    assert any(m.cls.__name__ == "CORSMiddleware" for m in app.user_middleware)

    client = TestClient(app)
    assert client.get("/health/liveness").status_code == 200


def test_create_app_applies_configured_origins():
    settings = load_settings(
        {
            "APP_ENV": "TEST",
            "CORS_ALLOWED_ORIGINS": "https://hub.example.com,https://hub2.example.com",
        }
    )
    application = create_app(settings)

    cors = next(
        m for m in application.user_middleware if m.cls.__name__ == "CORSMiddleware"
    )
    assert cors.kwargs["allow_origins"] == [
        "https://hub.example.com",
        "https://hub2.example.com",
    ]


def test_create_app_denies_cors_by_default():
    settings = load_settings({"APP_ENV": "TEST"})
    application = create_app(settings)

    cors = next(
        m for m in application.user_middleware if m.cls.__name__ == "CORSMiddleware"
    )
    assert cors.kwargs["allow_origins"] == []
