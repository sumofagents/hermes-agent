"""Tests for the read-only gateway validation CLI."""

from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import pytest


class FakeResponse:
    def __init__(self, status: int, data: object | None = None, error: str | None = None):
        self.status = status
        self.data = data if data is not None else {}
        self.error = error


def _args(**overrides):
    defaults = dict(
        json=False,
        probe_memory=False,
        chat_smoke=False,
        api_key_env="API_SERVER_KEY",
        expect_auth=False,
        log_bytes=4096,
        log_offset=None,
        timeout=2.0,
        base_url="http://127.0.0.1:8642",
    )
    defaults.update(overrides)
    return Namespace(**defaults)


def test_validate_default_does_not_probe_memory_or_chat(monkeypatch):
    from hermes_cli import gateway_validate

    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(gateway_validate, "get_env_value", lambda key: "")
    monkeypatch.setattr(
        gateway_validate,
        "get_gateway_runtime_snapshot",
        lambda: SimpleNamespace(
            manager="launchd",
            service_installed=True,
            service_running=True,
            gateway_pids=(123,),
            running=True,
        ),
    )
    monkeypatch.setattr(
        gateway_validate,
        "http_json",
        lambda url, *, token=None, timeout=2.0, method="GET", payload=None: calls.append((url, token))
        or FakeResponse(200, {"ok": True}),
    )
    monkeypatch.setattr(gateway_validate, "run_memory_check", lambda probe=False: pytest.fail("memory probe should be skipped"))
    monkeypatch.setattr(gateway_validate, "run_chat_smoke", lambda *a, **k: pytest.fail("chat smoke should be skipped"))
    monkeypatch.setattr(gateway_validate, "scan_gateway_log", lambda *a, **k: {"entries": []})
    monkeypatch.setattr(gateway_validate, "inspect_git", lambda: {"ok": True})

    report = gateway_validate.build_validation_report(_args())

    assert report["ok"] is True
    assert report["memory"]["skipped"] is True
    assert report["chat_smoke"]["skipped"] is True
    assert [url for url, _ in calls] == [
        "http://127.0.0.1:8642/health",
        "http://127.0.0.1:8642/health/detailed",
        "http://127.0.0.1:8642/v1/models",
        "http://127.0.0.1:8642/v1/capabilities",
    ]


def test_validate_reads_api_key_from_hermes_env_and_redacts_output(monkeypatch):
    from hermes_cli import gateway_validate

    secret = "sk-test-secret-1234567890"
    observed_tokens: list[str | None] = []

    monkeypatch.setattr(gateway_validate, "get_env_value", lambda key: secret if key == "API_SERVER_KEY" else "")
    monkeypatch.setattr(
        gateway_validate,
        "get_gateway_runtime_snapshot",
        lambda: SimpleNamespace(
            manager="launchd",
            service_installed=True,
            service_running=True,
            gateway_pids=(123,),
            running=True,
        ),
    )

    def fake_http(url, *, token=None, timeout=2.0, method="GET", payload=None):
        observed_tokens.append(token)
        if url.endswith("/v1/capabilities"):
            return FakeResponse(200, {"auth": {"required": True}})
        return FakeResponse(200, {"ok": True})

    monkeypatch.setattr(gateway_validate, "http_json", fake_http)
    monkeypatch.setattr(gateway_validate, "scan_gateway_log", lambda *a, **k: {"entries": [f"Authorization: Bearer {secret}"]})
    monkeypatch.setattr(gateway_validate, "inspect_git", lambda: {"ok": True})

    report = gateway_validate.build_validation_report(_args())
    rendered = gateway_validate.render_json(report)

    assert observed_tokens == [None, None, secret, secret]
    assert report["auth"]["api_key_env"] == "API_SERVER_KEY"
    assert report["auth"]["key_resolved"] is True
    assert report["auth"]["auth_header_sent"] is True
    assert secret not in rendered
    assert "[REDACTED]" in rendered


def test_exact_token_redaction_covers_raw_hex_and_bare_sk_values(monkeypatch):
    from hermes_cli import gateway_validate

    raw_hex = "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
    bare_sk = "sk-bare-token-value-1234567890"

    monkeypatch.setattr(gateway_validate, "get_env_value", lambda key: raw_hex if key == "API_SERVER_KEY" else "")
    monkeypatch.setattr(
        gateway_validate,
        "get_gateway_runtime_snapshot",
        lambda: SimpleNamespace(
            manager="launchd",
            service_installed=True,
            service_running=True,
            gateway_pids=(123,),
            running=True,
        ),
    )
    monkeypatch.setattr(gateway_validate, "http_json", lambda *a, **k: FakeResponse(200, {"ok": True}, error=f"body leaked {raw_hex} and {bare_sk}"))
    monkeypatch.setattr(gateway_validate, "scan_gateway_log", lambda *a, **k: {"entries": [f"raw={raw_hex}", f"sk={bare_sk}"]})
    monkeypatch.setattr(gateway_validate, "inspect_git", lambda: {"ok": True})

    rendered = gateway_validate.render_json(gateway_validate.build_validation_report(_args()))

    assert raw_hex not in rendered
    assert bare_sk not in rendered
    assert "[REDACTED]" in rendered


def test_expect_auth_fails_fast_when_key_is_absent(monkeypatch):
    from hermes_cli import gateway_validate

    monkeypatch.setattr(gateway_validate, "get_env_value", lambda key: "")

    report = gateway_validate.build_validation_report(_args(expect_auth=True))

    assert report["ok"] is False
    assert report["auth"]["ok"] is False
    assert "API_SERVER_KEY" in report["auth"]["error"]
    assert report["checks"][0]["name"] == "auth.key_present"
    assert report["checks"][0]["ok"] is False


def test_expect_auth_requires_unauthenticated_401_and_authenticated_200(monkeypatch):
    from hermes_cli import gateway_validate

    secret = "sk-expected-auth-secret"
    probes: list[tuple[str, str | None]] = []

    monkeypatch.setattr(gateway_validate, "get_env_value", lambda key: secret)
    monkeypatch.setattr(
        gateway_validate,
        "get_gateway_runtime_snapshot",
        lambda: SimpleNamespace(
            manager="launchd",
            service_installed=True,
            service_running=True,
            gateway_pids=(123,),
            running=True,
        ),
    )

    def fake_http(url, *, token=None, timeout=2.0, method="GET", payload=None):
        probes.append((url, token))
        if url.endswith("/v1/models") and token is None:
            return FakeResponse(401, {"error": "Missing or invalid API key"})
        if url.endswith("/v1/capabilities"):
            return FakeResponse(200, {"auth": {"required": True}})
        return FakeResponse(200, {"ok": True})

    monkeypatch.setattr(gateway_validate, "http_json", fake_http)
    monkeypatch.setattr(gateway_validate, "scan_gateway_log", lambda *a, **k: {"entries": []})
    monkeypatch.setattr(gateway_validate, "inspect_git", lambda: {"ok": True})

    report = gateway_validate.build_validation_report(_args(expect_auth=True))

    assert report["ok"] is True
    assert report["auth"]["unauthenticated_models_status"] == 401
    assert report["auth"]["authenticated_models_status"] == 200
    assert report["auth"]["capabilities_auth_required"] is True
    assert ("http://127.0.0.1:8642/v1/models", None) in probes
    assert ("http://127.0.0.1:8642/v1/models", secret) in probes


def test_default_base_url_uses_env_host_port_and_loopback_for_wildcard(monkeypatch):
    from hermes_cli import gateway_validate

    values = {"API_SERVER_HOST": "0.0.0.0", "API_SERVER_PORT": "9999"}
    monkeypatch.setattr(gateway_validate, "get_env_value", lambda key: values.get(key, ""))

    assert gateway_validate._normalize_base_url(None) == "http://127.0.0.1:9999"


def test_run_returns_nonzero_when_report_not_ok(monkeypatch, capsys):
    from hermes_cli import gateway_validate

    monkeypatch.setattr(
        gateway_validate,
        "build_validation_report",
        lambda args: {"ok": False, "checks": [{"name": "auth.key_present", "ok": False, "detail": "missing"}], "auth": {}},
    )

    with pytest.raises(SystemExit) as exc:
        gateway_validate.cmd_validate(_args(json=True))

    assert exc.value.code == 1
    output = capsys.readouterr().out
    assert '"ok": false' in output
