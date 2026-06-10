from __future__ import annotations

import json

from agent.usage_pricing import CanonicalUsage, CostResult
from agent.usage_ledger import (
    UsageLedgerEvent,
    build_model_call_event,
    classify_billing_route,
    usage_ledger_enabled,
    write_usage_span,
)


def test_usage_span_writer_is_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    event = UsageLedgerEvent(
        event_id="evt_1",
        trace_id="trace_1",
        event_type="model_call",
        status_class="success",
        provider="openai-codex",
        model="gpt-5.5",
    )

    result = write_usage_span(event)

    assert result.written is False
    assert result.reason == "disabled"
    assert not (tmp_path / "usage_ledger" / "spans.jsonl").exists()


def test_usage_ledger_has_config_default_and_writer_honors_config_path(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("HERMES_USAGE_LEDGER_ENABLED", raising=False)
    from hermes_cli.config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["usage_ledger"]["enabled"] is False
    assert usage_ledger_enabled(DEFAULT_CONFIG) is False

    ledger_path = tmp_path / "configured" / "spans.jsonl"
    event = UsageLedgerEvent(
        event_id="evt_config",
        trace_id="trace_config",
        event_type="model_call",
        status_class="success",
        provider="openai-codex",
        model="gpt-5.5",
    )

    result = write_usage_span(
        event,
        config={"usage_ledger": {"enabled": True, "path": str(ledger_path)}},
    )

    assert result.written is True
    assert result.path == ledger_path.resolve()
    assert ledger_path.exists()
    assert not (tmp_path / "home" / "usage_ledger" / "spans.jsonl").exists()


def test_usage_span_writer_drops_sensitive_and_research_evidence_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    event = UsageLedgerEvent(
        event_id="evt_2",
        trace_id="trace_2",
        event_type="model_call",
        status_class="success",
        provider="openrouter",
        model="anthropic/claude-opus-4-7",
        metadata={
            "repo": "hermes-agent",
            "prompt": "raw prompt must not persist",
            "response": "raw response must not persist",
            "api_key": "secret",
            "tool_args": {"token": "secret"},
            "atlas_evidence": "PASS",
            "theorem_verdict": "proved",
            "nested": {"authorization": "safe", "raw_provider_error": "secret payload"},
        },
    )

    result = write_usage_span(event, enabled=True)

    assert result.written is True
    payload = json.loads((tmp_path / "usage_ledger" / "spans.jsonl").read_text())
    assert payload["metadata"] == {"repo": "hermes-agent", "nested": {}}
    assert "prompt" not in json.dumps(payload).lower()
    assert "secret" not in json.dumps(payload).lower()
    assert "theorem" not in json.dumps(payload).lower()


def test_classify_subscription_pools_and_overflow_fallback_requirements():
    assert classify_billing_route("openai-codex", "gpt-5.5").account_pool == "openai_max"
    assert classify_billing_route("claude-cli", "claude-opus-4-7").account_pool == "anthropic_max"
    assert classify_billing_route("cursor", "composer-2.5").account_pool == "cursor_pro"
    assert classify_billing_route("grok", "grok-4.3").account_pool == "supergrok"

    openrouter = classify_billing_route("openrouter", "openai/gpt-5.5")
    assert openrouter.account_pool == "openrouter_prepay"
    assert openrouter.requires_fallback_reason is True

    fireworks = classify_billing_route("fireworks", "accounts/fireworks/models/llama")
    assert fireworks.account_pool == "fireworks_credit"
    assert fireworks.requires_fallback_reason is True


def test_build_model_call_event_records_metadata_only_subscription_and_overflow_fields():
    cost = CostResult(amount_usd=None, status="unknown", source="none", label="unknown")

    event = build_model_call_event(
        session_id="sess_1",
        provider="openrouter",
        model="openai/gpt-5.5",
        api_mode="chat_completions",
        usage=CanonicalUsage(input_tokens=10, output_tokens=5, cache_read_tokens=2),
        cost=cost,
        status_class="success",
        wall_ms=1234,
        source="cli",
        fallback_reason="openai_max_unavailable",
    )

    assert event.event_type == "model_call"
    assert event.session_id == "sess_1"
    assert event.input_tokens == 10
    assert event.output_tokens == 5
    assert event.cache_read_tokens == 2
    assert event.account_pool == "openrouter_prepay"
    assert event.subscription_mode == "prepaid_api"
    assert event.runtime_adapter == "openrouter_api"
    assert event.fallback_reason == "openai_max_unavailable"
    assert event.wall_ms == 1234
