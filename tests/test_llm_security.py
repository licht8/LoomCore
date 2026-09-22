"""Unit tests for the provider-URL SSRF guard."""

import socket

import pytest

from src.llm import security as llm_security
from src.llm.security import validate_provider_url


def _stub_resolve(monkeypatch, addresses):
    monkeypatch.setattr(llm_security, "resolve_host", lambda host: list(addresses))


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/v1",
        "file:///etc/passwd",
        "example.com/v1",
        "http://",
    ],
)
def test_rejects_bad_scheme_or_missing_host(url):
    with pytest.raises(ValueError):
        validate_provider_url(url, resolve=False)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/v1",
        "http://10.0.0.5/v1",
        "http://192.168.1.1/v1",
        "http://169.254.169.254/latest/meta-data",
        "http://100.64.0.1/v1",
        "http://128.0.0.1/v1",
        "http://192.0.0.1/v1",
        "http://[::1]/v1",
        "http://[::7f00:1]/v1",
        "http://[::a9fe:a9fe]/v1",
        "http://[::]/v1",
        "http://[::ffff:127.0.0.1]/v1",
        "http://[::ffff:169.254.169.254]/v1",
        "http://[2002:7f00:0001::]/v1",
        "http://[64:ff9b::7f00:1]/v1",
        "http://0.0.0.0/v1",
        "http://localhost:11434/v1",
        "http://localhost./v1",
        "http://foo.localhost/v1",
        "http://foo.internal/v1",
        "http://foo.internal./v1",
        "http://printer.local/v1",
        "http://2130706433:11434/v1",
        "http://0x7f000001/v1",
        "http://0177.0.0.1/v1",
        "http://127.1/v1",
        "http://127.0.0.1./v1",
        "http://metadata.google.internal/v1",
        "http://metadata.goog/v1",
        "http://metadata.goog./v1",
    ],
)
def test_rejects_internal_targets_without_resolving(url):
    with pytest.raises(ValueError):
        validate_provider_url(url, resolve=False)


def test_accepts_public_literal_and_hostname():
    validate_provider_url("https://93.184.216.34/v1", resolve=False)
    validate_provider_url("https://api.example.com/v1", resolve=False)
    validate_provider_url("https://[::ffff:93.184.216.34]/v1", resolve=False)
    # A numeric encoding of 8.8.8.8 is still a public literal.
    validate_provider_url("https://134744072/v1", resolve=False)


def test_rejects_non_string_url():
    with pytest.raises(ValueError, match="must be a string"):
        validate_provider_url(None, resolve=False)


def test_resolve_rejects_private_range(monkeypatch):
    _stub_resolve(monkeypatch, ["10.0.0.7"])

    with pytest.raises(ValueError, match="resolves to a non-public address"):
        validate_provider_url("https://api.example.com/v1", resolve=True)


def test_resolve_accepts_global_range(monkeypatch):
    _stub_resolve(monkeypatch, ["93.184.216.34"])

    validate_provider_url("https://api.example.com/v1", resolve=True)


def test_resolve_rejects_if_any_address_is_non_global(monkeypatch):
    _stub_resolve(monkeypatch, ["93.184.216.34", "127.0.0.1"])

    with pytest.raises(ValueError):
        validate_provider_url("https://api.example.com/v1", resolve=True)


def test_resolve_host_dns_failure_raises_value_error(monkeypatch):
    def boom(*args, **kwargs):
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr(llm_security.socket, "getaddrinfo", boom)

    with pytest.raises(ValueError, match="could not be resolved"):
        llm_security.resolve_host("nope.invalid")


def test_resolve_true_converts_dns_failure_to_value_error(monkeypatch):
    def boom(*args, **kwargs):
        raise socket.gaierror("name resolution failed")

    monkeypatch.setattr(llm_security.socket, "getaddrinfo", boom)

    with pytest.raises(ValueError, match="could not be resolved"):
        validate_provider_url("https://api.example.com/v1", resolve=True)
