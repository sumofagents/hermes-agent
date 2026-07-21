"""Append-only usage/routing span ledger.

Initial Oracle/Rilo+Forge implementation: metadata-only, disabled by default,
and intentionally local-first.  The writer is a primitive for later CLI,
Workspace, and Forge observer surfaces; it is not a research/evidence system.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from hermes_constants import get_hermes_home

EventType = Literal[
    "model_call",
    "fallback",
    "tool_call",
    "subagent",
    "cron_tick",
    "gateway_session",
    "budget_decision",
    "delayed_outcome",
]
StatusClass = Literal[
    "success",
    "provider_error",
    "auth_error",
    "quota_exhausted",
    "timeout",
    "cancelled",
    "blocked",
    "unknown",
]

_FORBIDDEN_METADATA_KEYS = {
    "api_key",
    "authorization",
    "auth",
    "bearer",
    "completion",
    "content",
    "evidence",
    "message",
    "password",
    "prompt",
    "proof",
    "raw_error",
    "raw_provider_error",
    "response",
    "secret",
    "theorem",
    "token",
    "tool_args",
}
_FORBIDDEN_KEY_FRAGMENTS = (
    "api_key",
    "atlas_evidence",
    "auth_token",
    "bearer",
    "claim_verdict",
    "prompt",
    "proof",
    "raw_provider_error",
    "response",
    "secret",
    "theorem",
    "token",
    "tool_arg",
)


@dataclass(frozen=True)
class BillingRouteClassification:
    provider: str
    model: str
    model_family: str = "other"
    account_pool: str = "unknown"
    runtime_adapter: str = "hermes_provider"
    subscription_mode: str = "unknown"
    requires_fallback_reason: bool = False


@dataclass(frozen=True)
class UsageLedgerEvent:
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex}")
    trace_id: str = field(default_factory=lambda: f"trace_{uuid.uuid4().hex}")
    parent_span_id: Optional[str] = None
    session_id: Optional[str] = None
    profile: Optional[str] = None
    agent_id: Optional[str] = None
    host: Optional[str] = None
    source: str = "cli"
    provider: str = "unknown"
    model: str = "unknown"
    model_family: str = "other"
    account_pool: str = "unknown"
    runtime_adapter: str = "hermes_provider"
    api_mode: Optional[str] = None
    credential_account_label: Optional[str] = None
    subscription_mode: str = "unknown"
    session_window_id: Optional[str] = None
    weekly_window_id: Optional[str] = None
    fallback_reason: Optional[str] = None
    event_type: EventType = "model_call"
    status_class: StatusClass = "unknown"
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    estimated_cost_usd: Optional[float] = None
    actual_cost_usd: Optional[float] = None
    billing_mode: str = "unknown"
    wall_ms: Optional[int] = None
    active_model_ms: Optional[int] = None
    tool_ms: Optional[int] = None
    subprocess_ms: Optional[int] = None
    idle_ms: Optional[int] = None
    no_progress_ms: Optional[int] = None
    budget_policy_id: Optional[str] = None
    tags: dict[str, str] = field(default_factory=dict)
    prior_event_id: Optional[str] = None
    supersedes_event_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_json_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["metadata"] = sanitize_metadata(self.metadata)
        payload["tags"] = sanitize_metadata(self.tags)
        return {k: v for k, v in payload.items() if v is not None}


@dataclass(frozen=True)
class UsageLedgerWriteResult:
    written: bool
    path: Optional[Path] = None
    reason: str = "written"


def sanitize_metadata(value: Any) -> Any:
    """Drop raw/sensitive/research-evidence fields from metadata recursively."""
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, inner in value.items():
            key_s = str(key)
            key_l = key_s.lower()
            if key_l in _FORBIDDEN_METADATA_KEYS or any(fragment in key_l for fragment in _FORBIDDEN_KEY_FRAGMENTS):
                continue
            sanitized = sanitize_metadata(inner)
            if sanitized is not None:
                clean[key_s] = sanitized
        return clean
    if isinstance(value, list):
        return [item for item in (sanitize_metadata(item) for item in value) if item is not None]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def classify_billing_route(provider: str | None, model: str | None, base_url: str = "") -> BillingRouteClassification:
    provider_s = (provider or "unknown").lower()
    model_s = model or "unknown"
    model_l = model_s.lower()
    base_l = (base_url or "").lower()

    if provider_s in {"openai-codex", "codex"} or "codex" in base_l:
        return BillingRouteClassification(provider_s, model_s, "openai", "openai_max", "codex_cli", "session_subscription")
    if provider_s in {"claude-cli", "claude-code-cli"}:
        return BillingRouteClassification(provider_s, model_s, "anthropic", "anthropic_max", "claude_code_cli", "session_subscription")
    if provider_s in {"cursor", "cursor-agent", "cursor-composer"} or "composer" in model_l:
        return BillingRouteClassification(provider_s, model_s, "cursor", "cursor_pro", "cursor_composer_cli", "session_subscription")
    if provider_s in {"grok", "xai", "supergrok"} or "grok" in model_l:
        return BillingRouteClassification(provider_s, model_s, "xai", "supergrok", "grok_cli", "session_subscription")
    if provider_s == "openrouter":
        return BillingRouteClassification(provider_s, model_s, _family_from_model(model_l), "openrouter_prepay", "openrouter_api", "prepaid_api", True)
    if provider_s == "fireworks" or "fireworks" in base_l:
        return BillingRouteClassification(provider_s, model_s, _family_from_model(model_l), "fireworks_credit", "fireworks_api", "prepaid_api", True)
    if provider_s in {"ollama", "llama-cpp", "local"}:
        return BillingRouteClassification(provider_s, model_s, "local", "local", "hermes_provider", "local")
    return BillingRouteClassification(provider_s, model_s, _family_from_model(model_l), "unknown", "hermes_provider", "unknown")


def _family_from_model(model_l: str) -> str:
    if "claude" in model_l or "anthropic" in model_l:
        return "anthropic"
    if "gpt" in model_l or "openai" in model_l:
        return "openai"
    if "grok" in model_l or "xai" in model_l:
        return "xai"
    if "composer" in model_l or "cursor" in model_l:
        return "cursor"
    if "fireworks" in model_l:
        return "fireworks"
    return "other"


def build_model_call_event(
    *,
    session_id: str | None,
    provider: str | None,
    model: str | None,
    api_mode: str | None,
    usage: Any,
    cost: Any = None,
    status_class: StatusClass = "success",
    wall_ms: int | None = None,
    source: str = "cli",
    base_url: str = "",
    fallback_reason: str | None = None,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    metadata: Optional[dict[str, Any]] = None,
) -> UsageLedgerEvent:
    route = classify_billing_route(provider, model, base_url)
    amount = getattr(cost, "amount_usd", None)
    return UsageLedgerEvent(
        trace_id=trace_id or f"trace_{uuid.uuid4().hex}",
        parent_span_id=parent_span_id,
        session_id=session_id,
        source=source,
        provider=provider or "unknown",
        model=model or "unknown",
        model_family=route.model_family,
        account_pool=route.account_pool,
        runtime_adapter=route.runtime_adapter,
        api_mode=api_mode,
        subscription_mode=route.subscription_mode,
        fallback_reason=fallback_reason,
        event_type="model_call",
        status_class=status_class,
        input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
        cache_read_tokens=int(getattr(usage, "cache_read_tokens", 0) or 0),
        cache_write_tokens=int(getattr(usage, "cache_write_tokens", 0) or 0),
        reasoning_tokens=int(getattr(usage, "reasoning_tokens", 0) or 0),
        estimated_cost_usd=float(amount) if amount is not None else None,
        billing_mode=route.subscription_mode,
        wall_ms=wall_ms,
        active_model_ms=wall_ms,
        metadata=metadata or {},
    )


def _usage_ledger_config_section(config: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    section = config.get("usage_ledger") or config.get("oracle_usage_ledger") or {}
    return section if isinstance(section, dict) else {}


def _usage_ledger_path_from_config(config: Optional[dict[str, Any]]) -> Optional[Path]:
    section = _usage_ledger_config_section(config)
    configured = section.get("path") or section.get("file")
    if not configured:
        return None
    return Path(os.path.expanduser(str(configured))).resolve()


def usage_ledger_enabled(config: Optional[dict[str, Any]] = None) -> bool:
    env = os.getenv("HERMES_USAGE_LEDGER_ENABLED", "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    if env in {"0", "false", "no", "off"}:
        return False
    section = _usage_ledger_config_section(config)
    return bool(section.get("enabled", False))


def write_usage_span(
    event: UsageLedgerEvent,
    *,
    enabled: Optional[bool] = None,
    path: Optional[Path] = None,
    config: Optional[dict[str, Any]] = None,
) -> UsageLedgerWriteResult:
    if enabled is None:
        enabled = usage_ledger_enabled(config)
    if not enabled:
        return UsageLedgerWriteResult(written=False, reason="disabled")

    ledger_path = path or _usage_ledger_path_from_config(config) or (
        get_hermes_home() / "usage_ledger" / "spans.jsonl"
    )
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with ledger_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event.to_json_dict(), sort_keys=True, separators=(",", ":")) + "\n")
    return UsageLedgerWriteResult(written=True, path=ledger_path)
