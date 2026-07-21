"""Read-only gateway post-restart validation.

This module intentionally performs no service restarts and no configuration
writes. It packages the checks operators already run manually after a gateway
restart into one mockable command.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from hermes_cli.config import get_env_value, get_hermes_home, load_config
from hermes_cli.gateway import PROJECT_ROOT, get_gateway_runtime_snapshot

_TOKEN_RE = re.compile(
    r"(Authorization\s*:\s*Bearer\s+)([^\s,;\]})]+)|"
    r"(Bearer\s+)(sk-[A-Za-z0-9._-]+|[A-Za-z0-9._-]{20,})|"
    r"(sk-[A-Za-z0-9._-]{8,})",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HTTPJSONResponse:
    status: int
    data: Any
    error: str | None = None


def redact(value: Any, *, secrets: list[str] | tuple[str, ...] = ()) -> str:
    """Return *value* as text with bearer secrets redacted.

    Pattern-based redaction catches common Authorization/Bearer forms. Exact
    secret redaction catches raw token values that appear unprefixed in logs or
    error bodies.
    """
    text = "" if value is None else str(value)

    def repl(match: re.Match[str]) -> str:
        if match.group(1):
            return f"{match.group(1)}[REDACTED]"
        if match.group(3):
            return f"{match.group(3)}[REDACTED]"
        return "[REDACTED]"

    text = _TOKEN_RE.sub(repl, text)
    for secret in sorted({str(s) for s in secrets if s}, key=len, reverse=True):
        if len(secret) >= 4:
            text = text.replace(secret, "[REDACTED]")
    return text


def _default_base_url() -> str:
    host = (get_env_value("API_SERVER_HOST") or "127.0.0.1").strip() or "127.0.0.1"
    port = (get_env_value("API_SERVER_PORT") or "8642").strip() or "8642"
    # 0.0.0.0 is a bind address, not a useful client destination.
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    return f"http://{host}:{port}"


def _normalize_base_url(raw: str | None) -> str:
    base = (raw or _default_base_url()).strip().rstrip("/")
    return base or _default_base_url()


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def http_json(
    url: str,
    *,
    token: str | None = None,
    timeout: float = 2.0,
    method: str = "GET",
    payload: Any | None = None,
) -> HTTPJSONResponse:
    """GET/POST JSON and return status/data without raising on HTTP errors."""
    data: bytes | None = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib_request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib_request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - local/operator URL
            body = resp.read().decode("utf-8", errors="replace")
            try:
                parsed: Any = json.loads(body) if body else {}
            except json.JSONDecodeError:
                parsed = {"raw": body}
            return HTTPJSONResponse(status=int(resp.status), data=parsed)
    except urllib_error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed = {"raw": body}
        return HTTPJSONResponse(status=int(exc.code), data=parsed, error=redact(body))
    except Exception as exc:  # noqa: BLE001 - validation must report, not crash
        return HTTPJSONResponse(status=0, data={}, error=redact(exc))


def _check(name: str, ok: bool, detail: str = "", **extra: Any) -> dict[str, Any]:
    item: dict[str, Any] = {"name": name, "ok": bool(ok)}
    if detail:
        item["detail"] = redact(detail)
    item.update(extra)
    return item


def run_memory_check(*, probe: bool = False) -> dict[str, Any]:
    """Run G1C memory readiness in-process."""
    try:
        from plugins.memory.chromadb.g1c_readiness import build_readiness_report

        report = build_readiness_report(
            hermes_home=get_hermes_home(),
            config=load_config(),
            limit=100,
            probe=probe,
            strict=False,
        )
        return {"skipped": False, "ok": bool(report.get("ok")), "report": report}
    except Exception as exc:  # noqa: BLE001
        return {"skipped": False, "ok": False, "error": redact(exc)}


def run_chat_smoke(base_url: str, *, token: str | None, timeout: float) -> dict[str, Any]:
    """Run a tiny OpenAI-compatible chat smoke test."""
    payload = {
        "model": "hermes-agent",
        "messages": [
            {"role": "system", "content": "Reply with exactly: pong"},
            {"role": "user", "content": "pong"},
        ],
        "max_tokens": 8,
        "temperature": 0,
    }
    started = time.monotonic()
    resp = http_json(
        _join_url(base_url, "/v1/chat/completions"),
        token=token,
        timeout=timeout,
        method="POST",
        payload=payload,
    )
    latency_ms = int((time.monotonic() - started) * 1000)
    content = ""
    if isinstance(resp.data, dict):
        try:
            content = str(resp.data["choices"][0]["message"]["content"])
        except Exception:
            content = ""
    ok = resp.status == 200 and content.strip().lower() == "pong"
    return {
        "skipped": False,
        "ok": ok,
        "status": resp.status,
        "latency_ms": latency_ms,
        "matched_exact_pong": content.strip().lower() == "pong",
        "error": resp.error,
    }


def scan_gateway_log(*, log_bytes: int = 4096, log_offset: int | None = None) -> dict[str, Any]:
    """Read recent gateway warnings/errors from ~/.hermes/logs/gateway.log."""
    path = get_hermes_home() / "logs" / "gateway.log"
    if not path.exists():
        return {"path": str(path), "missing": True, "entries": []}
    try:
        size = path.stat().st_size
        with path.open("rb") as handle:
            if log_offset is not None:
                handle.seek(max(0, min(log_offset, size)))
            else:
                handle.seek(max(0, size - max(0, int(log_bytes))))
            raw = handle.read(max(0, int(log_bytes)) if log_offset is not None else -1)
        text = raw.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return {"path": str(path), "missing": False, "entries": [], "error": redact(exc)}
    entries = [
        redact(line)
        for line in text.splitlines()
        if "WARNING" in line or "ERROR" in line or "Traceback" in line
    ]
    return {"path": str(path), "missing": False, "entries": entries[-50:]}


def inspect_git() -> dict[str, Any]:
    """Inspect local git HEAD/upstream without fetching."""
    root = PROJECT_ROOT
    if not (root / ".git").exists():
        return {"ok": True, "skipped": True, "reason": "not a git checkout", "root": str(root)}

    def run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    status = run(["status", "--short"])
    branch = run(["branch", "--show-current"])
    head = run(["rev-parse", "--short", "HEAD"])
    upstream = run(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    report = {
        "ok": status.returncode == 0,
        "root": str(root),
        "branch": branch.stdout.strip(),
        "head": head.stdout.strip(),
        "dirty": bool(status.stdout.strip()),
        "upstream": upstream.stdout.strip() if upstream.returncode == 0 else None,
    }
    if status.returncode != 0:
        report["error"] = redact(status.stderr)
    return report


def _runtime_report() -> dict[str, Any]:
    try:
        snap = get_gateway_runtime_snapshot()
        return {
            "ok": bool(getattr(snap, "running", False)),
            "manager": getattr(snap, "manager", None),
            "service_installed": bool(getattr(snap, "service_installed", False)),
            "service_running": bool(getattr(snap, "service_running", False)),
            "gateway_pids": list(getattr(snap, "gateway_pids", ()) or ()),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": redact(exc)}


def _http_checks(base_url: str, *, token: str | None, timeout: float) -> tuple[list[dict[str, Any]], dict[str, HTTPJSONResponse]]:
    responses: dict[str, HTTPJSONResponse] = {}
    checks: list[dict[str, Any]] = []
    for name, path, expected, send_auth in [
        ("http.health", "/health", 200, False),
        ("http.health_detailed", "/health/detailed", 200, False),
        ("http.models", "/v1/models", 200, True),
        ("http.capabilities", "/v1/capabilities", 200, True),
    ]:
        resp = http_json(_join_url(base_url, path), token=token if send_auth else None, timeout=timeout)
        responses[path] = resp
        checks.append(
            _check(
                name,
                resp.status == expected,
                resp.error or f"status={resp.status}",
                status=resp.status,
            )
        )
    return checks, responses


def _auth_checks(base_url: str, *, token: str | None, timeout: float, expect_auth: bool, api_key_env: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    auth: dict[str, Any] = {
        "api_key_env": api_key_env,
        "key_resolved": bool(token),
        "auth_header_sent": bool(token),
        "expect_auth": bool(expect_auth),
    }
    checks: list[dict[str, Any]] = []
    if expect_auth and not token:
        msg = f"--expect-auth requires {api_key_env} to be set in Hermes environment"
        auth.update({"ok": False, "error": msg})
        checks.append(_check("auth.key_present", False, msg))
        return checks, auth

    if not expect_auth:
        auth["ok"] = True
        return checks, auth

    unauth = http_json(_join_url(base_url, "/v1/models"), token=None, timeout=timeout)
    authed = http_json(_join_url(base_url, "/v1/models"), token=token, timeout=timeout)
    caps = http_json(_join_url(base_url, "/v1/capabilities"), token=token, timeout=timeout)
    caps_required = False
    if isinstance(caps.data, dict):
        auth_data = caps.data.get("auth")
        if isinstance(auth_data, dict):
            caps_required = bool(auth_data.get("required"))
    auth.update(
        {
            "unauthenticated_models_status": unauth.status,
            "authenticated_models_status": authed.status,
            "capabilities_auth_required": caps_required,
        }
    )
    checks.extend(
        [
            _check("auth.unauthenticated_models_401", unauth.status == 401, f"status={unauth.status}", status=unauth.status),
            _check("auth.authenticated_models_200", authed.status == 200, f"status={authed.status}", status=authed.status),
            _check("auth.capabilities_required", caps_required, f"required={caps_required}"),
        ]
    )
    auth["ok"] = all(check["ok"] for check in checks)
    return checks, auth


def build_validation_report(args: Any) -> dict[str, Any]:
    """Build a read-only gateway validation report."""
    api_key_env = str(getattr(args, "api_key_env", "API_SERVER_KEY") or "API_SERVER_KEY")
    token = get_env_value(api_key_env) or ""
    expect_auth = bool(getattr(args, "expect_auth", False))
    timeout = float(getattr(args, "timeout", 2.0) or 2.0)
    base_url = _normalize_base_url(getattr(args, "base_url", None))

    checks: list[dict[str, Any]] = []
    auth_checks, auth = _auth_checks(
        base_url,
        token=token or None,
        timeout=timeout,
        expect_auth=expect_auth,
        api_key_env=api_key_env,
    )
    checks.extend(auth_checks)
    if expect_auth and not token:
        return {
            "ok": False,
            "base_url": base_url,
            "auth": auth,
            "checks": checks,
            "runtime": {"skipped": True},
            "http": {"skipped": True},
            "memory": {"skipped": True},
            "chat_smoke": {"skipped": True},
            "logs": {"skipped": True},
            "git": {"skipped": True},
        }

    runtime = _runtime_report()
    checks.append(_check("runtime.running", bool(runtime.get("ok")), runtime.get("error", "")))

    http_check_items, http_responses = _http_checks(base_url, token=token or None, timeout=timeout)
    checks.extend(http_check_items)

    memory = {"skipped": True}
    if bool(getattr(args, "probe_memory", False)):
        memory = run_memory_check(probe=True)
        checks.append(_check("memory.readiness", bool(memory.get("ok")), memory.get("error", "")))

    chat_smoke = {"skipped": True}
    if bool(getattr(args, "chat_smoke", False)):
        chat_smoke = run_chat_smoke(base_url, token=token or None, timeout=timeout)
        checks.append(_check("chat_smoke.exact_pong", bool(chat_smoke.get("ok")), chat_smoke.get("error", "")))

    logs = scan_gateway_log(
        log_bytes=int(getattr(args, "log_bytes", 4096) or 4096),
        log_offset=getattr(args, "log_offset", None),
    )
    checks.append(_check("logs.scan", not bool(logs.get("error")), logs.get("error", ""), entries=len(logs.get("entries", []))))

    git = inspect_git()
    checks.append(_check("git.inspect", bool(git.get("ok")), git.get("error", ""), dirty=bool(git.get("dirty"))))

    report = {
        "ok": all(check.get("ok") for check in checks),
        "base_url": base_url,
        "auth": auth,
        "runtime": runtime,
        "http": {path: {"status": resp.status, "error": resp.error} for path, resp in http_responses.items()},
        "memory": memory,
        "chat_smoke": chat_smoke,
        "logs": logs,
        "git": git,
        "checks": checks,
    }
    return _redact_obj(report, secrets=[token] if token else [])


def _redact_obj(value: Any, *, secrets: list[str] | tuple[str, ...] = ()) -> Any:
    if isinstance(value, dict):
        return {k: _redact_obj(v, secrets=secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_obj(v, secrets=secrets) for v in value]
    if isinstance(value, tuple):
        return [_redact_obj(v, secrets=secrets) for v in value]
    if isinstance(value, str):
        return redact(value, secrets=secrets)
    return value


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(_redact_obj(report), sort_keys=True, indent=2)


def render_text(report: dict[str, Any]) -> str:
    lines = ["Gateway validate", "────────────────────────────────────────"]
    lines.append(f"  ok: {report.get('ok')}")
    lines.append(f"  base_url: {report.get('base_url')}")
    auth = report.get("auth", {}) if isinstance(report.get("auth"), dict) else {}
    lines.append(
        "  auth: "
        f"key_resolved={auth.get('key_resolved')} "
        f"header_sent={auth.get('auth_header_sent')} "
        f"expect_auth={auth.get('expect_auth')}"
    )
    for check in report.get("checks", []):
        if not isinstance(check, dict):
            continue
        mark = "✓" if check.get("ok") else "✗"
        detail = f" — {check.get('detail')}" if check.get("detail") else ""
        lines.append(f"  {mark} {check.get('name')}{detail}")
    log_entries = (report.get("logs") or {}).get("entries", []) if isinstance(report.get("logs"), dict) else []
    if log_entries:
        lines.append("  recent warning/error log lines:")
        for entry in log_entries[-10:]:
            lines.append(f"    {redact(entry)}")
    return "\n".join(lines) + "\n"


def cmd_validate(args: Any) -> None:
    report = build_validation_report(args)
    if bool(getattr(args, "json", False)):
        print(render_json(report))
    else:
        print(render_text(report), end="")
    if not report.get("ok"):
        raise SystemExit(1)


__all__ = [
    "HTTPJSONResponse",
    "build_validation_report",
    "cmd_validate",
    "http_json",
    "inspect_git",
    "redact",
    "render_json",
    "render_text",
    "run_chat_smoke",
    "run_memory_check",
    "scan_gateway_log",
]
