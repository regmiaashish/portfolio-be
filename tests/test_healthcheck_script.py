"""scripts/healthcheck.py (the Docker HEALTHCHECK) follows the container's TLS setting."""

import importlib.util
import ssl
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "healthcheck.py"


def load():
    spec = importlib.util.spec_from_file_location("healthcheck", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plain_http_without_tls(monkeypatch):
    monkeypatch.delenv("TLS_CERT_FILE", raising=False)
    monkeypatch.setenv("PORT", "8001")

    url, context = load().health_url_and_context()

    assert url == "http://127.0.0.1:8001/health"
    assert context is None


def test_https_to_itself_when_tls_is_on(monkeypatch):
    monkeypatch.setenv("TLS_CERT_FILE", "/run/tls/origin.pem")
    monkeypatch.setenv("PORT", "8443")

    url, context = load().health_url_and_context()

    assert url == "https://127.0.0.1:8443/health"
    # The origin certificate is for the public hostname, not 127.0.0.1.
    assert context.verify_mode == ssl.CERT_NONE
    assert context.check_hostname is False


def test_empty_cert_setting_means_http(monkeypatch):
    monkeypatch.setenv("TLS_CERT_FILE", "")

    url, _ = load().health_url_and_context()

    assert url.startswith("http://")
