"""Testes do mecanismo central de configuração (src.settings).

Somente valores sintéticos e injetados; nenhuma leitura de ambiente real,
nenhum contrato da Gateway e nenhum segredo. Cobrem validação na carga, falha
fechada de TLS em ambientes produtivos, política de CORS, polling e log.
"""
import pytest

from src.domain.errors import ValidationError
from src.settings import (
    AppSettings,
    Environment,
    load_settings,
    parse_allowed_origins,
    parse_bool,
    parse_float,
    parse_int,
)


# ─── parsers ──────────────────────────────────────────────────────────────────

def test_parse_bool_accepts_usual_forms_and_default():
    assert parse_bool({"F": "true"}, "F", False) is True
    assert parse_bool({"F": "OFF"}, "F", True) is False
    assert parse_bool({}, "F", True) is True


def test_parse_bool_rejects_garbage():
    with pytest.raises(ValidationError):
        parse_bool({"F": "maybe"}, "F", False)


def test_parse_int_enforces_minimum():
    assert parse_int({"N": "5"}, "N", 1, minimum=1) == 5
    with pytest.raises(ValidationError):
        parse_int({"N": "0"}, "N", 1, minimum=1)
    with pytest.raises(ValidationError):
        parse_int({"N": "x"}, "N", 1)


def test_parse_float_enforces_minimum():
    assert parse_float({"N": "2.5"}, "N", 1.0, minimum=0.0) == 2.5
    with pytest.raises(ValidationError):
        parse_float({"N": "-1"}, "N", 1.0, minimum=0.0)


def test_parse_allowed_origins_drops_wildcard_and_blanks():
    assert parse_allowed_origins("https://a, ,*, https://b") == (
        "https://a",
        "https://b",
    )
    assert parse_allowed_origins(None) == ()


# ─── load_settings: identidade e defaults ─────────────────────────────────────

def test_load_settings_defaults_are_safe():
    s = load_settings({"APP_ENV": "TEST"})
    assert isinstance(s, AppSettings)
    assert s.environment is Environment.TEST
    assert s.tls.verify is True
    assert s.cors_allowed_origins == ()
    assert s.app_root_path == "/api-reconhecimento-documentacao-cadastro"


def test_load_settings_rejects_unknown_environment():
    with pytest.raises(ValidationError):
        load_settings({"APP_ENV": "STAGING"})


def test_load_settings_requires_absolute_root_path():
    with pytest.raises(ValidationError):
        load_settings({"APP_ENV": "TEST", "APP_ROOT_PATH": "relativo"})


# ─── TLS fail-closed em ambientes produtivos ──────────────────────────────────

def test_tls_verify_false_is_rejected_in_prod_like():
    for env_name in ("HML", "PRD"):
        with pytest.raises(ValidationError):
            load_settings({"APP_ENV": env_name, "TLS_VERIFY": "false"})


def test_tls_verify_false_allowed_only_in_dev():
    s = load_settings({"APP_ENV": "DSV", "TLS_VERIFY": "false"})
    assert s.tls.verify is False


# ─── polling e log ────────────────────────────────────────────────────────────

def test_polling_defaults_follow_curadoria():
    s = load_settings({"APP_ENV": "TEST"})
    assert s.polling.interval_seconds == 2.0
    assert s.polling.timeout_seconds == 180.0
    assert s.polling.not_found_grace_seconds == 30.0


def test_polling_not_found_grace_is_configurable():
    s = load_settings({"APP_ENV": "TEST", "POLLING_NOT_FOUND_GRACE_SECONDS": "12"})
    assert s.polling.not_found_grace_seconds == 12.0


def test_log_level_is_read_and_uppercased():
    assert load_settings({"APP_ENV": "TEST", "LOG_LEVEL": "debug"}).log_level == "DEBUG"


def test_ca_bundle_takes_precedence_for_requests():
    s = load_settings({"APP_ENV": "TEST", "TLS_CA_BUNDLE_PATH": "/certs/ca.pem"})
    assert s.tls.requests_verify == "/certs/ca.pem"


# ─── limites de upload configuráveis ──────────────────────────────────────────

def test_upload_limits_are_configurable():
    s = load_settings(
        {
            "APP_ENV": "TEST",
            "UPLOAD_MAX_FILES": "3",
            "UPLOAD_MAX_FILE_SIZE_BYTES": "1048576",
            "UPLOAD_ALLOWED_MEDIA_TYPES": "application/pdf, image/png",
        }
    )
    assert s.upload_limits.max_files == 3
    assert s.upload_limits.max_file_size_bytes == 1048576
    assert s.upload_limits.allowed_media_types == ("application/pdf", "image/png")


def test_error_messages_do_not_leak_values():
    # A mensagem cita o nome da chave, nunca o valor problemático.
    try:
        load_settings({"APP_ENV": "TEST", "UPLOAD_MAX_FILES": "supersecretvalue"})
    except ValidationError as exc:
        assert "supersecretvalue" not in str(exc)
    else:
        pytest.fail("Esperava ValidationError para inteiro invalido.")


# ─── scope do client_credentials ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "uri", ["api://recurso", "api://recurso/", "api://recurso/.default", "api://recurso/.default/"]
)
def test_scope_termina_em_default_sem_duplicar(uri):
    # "/.default/.default" é recusado pelo Azure AD.
    s = load_settings({"APP_ENV": "TEST", "GATEWAYAPPIDURI": uri})
    assert s.service_identity.scope() == "api://recurso/.default"


# ─── OCR (caminho B) ──────────────────────────────────────────────────────────

def test_ocr_defaults():
    ocr = load_settings({"APP_ENV": "TEST"}).ocr
    assert ocr.max_parallel == 4
    assert ocr.polling_interval_seconds == 5.0
    assert ocr.timeout_per_document_seconds == 120.0
    assert ocr.budget_seconds == 150.0


def test_ocr_configuravel():
    ocr = load_settings(
        {
            "APP_ENV": "TEST",
            "OCR_MAX_PARALLEL": "2",
            "OCR_POLLING_INTERVAL_SECONDS": "10",
            "OCR_TIMEOUT_PER_DOCUMENT_SECONDS": "60",
            "OCR_BUDGET_SECONDS": "90",
        }
    ).ocr
    assert (ocr.max_parallel, ocr.polling_interval_seconds) == (2, 10.0)
    assert (ocr.timeout_per_document_seconds, ocr.budget_seconds) == (60.0, 90.0)


def test_ocr_intervalo_abaixo_de_3s_e_recusado():
    # A doc da Gateway proíbe polling com intervalo menor que 3 s.
    with pytest.raises(ValidationError):
        load_settings({"APP_ENV": "TEST", "OCR_POLLING_INTERVAL_SECONDS": "2"})


# ─── diagnóstico local (texto que chega à IA) ─────────────────────────────────

def test_diagnostico_desligado_por_padrao():
    assert load_settings({"APP_ENV": "DSV"}).diagnostico_dir is None


def test_diagnostico_ligado_em_dsv():
    s = load_settings({"APP_ENV": "DSV", "DIAGNOSTICO_DIR": "C:/diag"})
    assert s.diagnostico_dir == "C:/diag"


def test_diagnostico_recusado_em_hml_e_prd():
    # Grava texto de documentos (PII) em disco: nunca fora do dev local.
    for env_name in ("HML", "PRD"):
        with pytest.raises(ValidationError):
            load_settings({"APP_ENV": env_name, "DIAGNOSTICO_DIR": "/tmp/diag"})
