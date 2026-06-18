import pytest

from app.config import Settings
from app.security import is_allowed_origin, is_loopback_host, validate_runtime_security


def test_loopback_hosts_are_allowed() -> None:
    assert is_loopback_host("127.0.0.1:8000")
    assert is_loopback_host("localhost")
    assert is_loopback_host("[::1]:8000")


def test_non_loopback_requires_server_mode() -> None:
    settings = Settings(HOST="0.0.0.0", OPENFLOW_SERVER_MODE=False)
    with pytest.raises(RuntimeError, match="OPENFLOW_SERVER_MODE"):
        validate_runtime_security(settings)


def test_server_mode_requires_token() -> None:
    settings = Settings(HOST="0.0.0.0", OPENFLOW_SERVER_MODE=True, REQUIRE_API_TOKEN=False)
    with pytest.raises(RuntimeError, match="API_TOKEN"):
        validate_runtime_security(settings)


def test_server_mode_with_token_is_allowed() -> None:
    settings = Settings(
        HOST="0.0.0.0",
        OPENFLOW_SERVER_MODE=True,
        REQUIRE_API_TOKEN=True,
        API_TOKEN="secret",
    )
    validate_runtime_security(settings)


def test_local_origins_are_allowed_by_default() -> None:
    settings = Settings(_env_file=None)
    assert is_allowed_origin("file://", settings)
    assert is_allowed_origin("null", settings)
    assert is_allowed_origin("http://127.0.0.1:3000", settings)
    assert is_allowed_origin("http://localhost:3000", settings)


def test_remote_origin_requires_allowlist() -> None:
    settings = Settings(_env_file=None)
    assert not is_allowed_origin("https://example.com", settings)
    settings = Settings(_env_file=None, ALLOWED_ORIGINS="https://example.com")
    assert is_allowed_origin("https://example.com", settings)
