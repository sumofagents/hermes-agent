from __future__ import annotations

import json

from agent.usage_ledger_forge import ForgeObserverAdapter, build_forge_observer_packet


def test_forge_observer_packet_is_metadata_only_and_aggregates():
    rows = [
        {
            "event_id": "evt_1",
            "trace_id": "trace_1",
            "session_id": "sess_1",
            "created_at": "2026-05-26T00:00:00+00:00",
            "provider": "openai-codex",
            "model": "gpt-5.5",
            "account_pool": "openai_max",
            "subscription_mode": "session_subscription",
            "runtime_adapter": "codex_cli",
            "input_tokens": 10,
            "output_tokens": 5,
            "cache_read_tokens": 2,
            "cache_write_tokens": 0,
            "reasoning_tokens": 1,
            "estimated_cost_usd": None,
            "metadata": {
                "repo": "hermes-agent",
                "prompt": "raw prompt must not leave local ledger",
                "response": "raw response must not leave local ledger",
                "api_key": "secret",
            },
        },
        {
            "event_id": "evt_2",
            "trace_id": "trace_2",
            "created_at": "2026-05-26T00:01:00+00:00",
            "provider": "openrouter",
            "model": "anthropic/claude-opus-4-7",
            "account_pool": "openrouter_prepay",
            "subscription_mode": "prepaid_api",
            "runtime_adapter": "openrouter_api",
            "fallback_reason": "anthropic_max_unavailable",
            "input_tokens": 7,
            "output_tokens": 3,
            "estimated_cost_usd": 0.25,
        },
    ]

    packet = build_forge_observer_packet(rows, source_host="macbook", generated_at="2026-05-26T00:02:00+00:00")

    assert packet["schema_version"] == 1
    assert packet["observer"] == "forge"
    assert packet["source_host"] == "macbook"
    assert packet["event_count"] == 2
    assert packet["summary"]["totals"]["input_tokens"] == 17
    assert packet["summary"]["by_account_pool"]["openrouter_prepay"]["estimated_cost_usd"] == 0.25
    assert packet["spans"][0]["event_id"] == "evt_1"
    assert packet["spans"][0]["account_pool"] == "openai_max"
    assert "metadata" not in packet["spans"][0]
    serialized = json.dumps(packet).lower()
    assert "raw prompt" not in serialized
    assert "raw response" not in serialized
    assert "secret" not in serialized


def test_forge_observer_adapter_exports_only_new_spans_and_updates_cursor(tmp_path):
    ledger = tmp_path / "usage_ledger" / "spans.jsonl"
    packet_dir = tmp_path / "forge_packets"
    state_path = tmp_path / "forge_observer_state.json"
    _append_jsonl(ledger, {"event_id": "evt_1", "provider": "openai-codex", "account_pool": "openai_max", "input_tokens": 10})
    _append_jsonl(ledger, {"event_id": "evt_2", "provider": "openrouter", "account_pool": "openrouter_prepay", "input_tokens": 5})

    adapter = ForgeObserverAdapter(
        ledger_path=ledger,
        packet_dir=packet_dir,
        state_path=state_path,
        source_host="macbook",
    )

    first = adapter.export_new_spans()

    assert first.written is True
    assert first.event_count == 2
    assert first.adapter_mode == "observer_only_file_export"
    assert first.network_io is False
    first_packet = json.loads(first.packet_path.read_text())
    assert [row["event_id"] for row in first_packet["spans"]] == ["evt_1", "evt_2"]
    state = json.loads(state_path.read_text())
    assert state["last_event_id"] == "evt_2"

    _append_jsonl(ledger, {"event_id": "evt_3", "provider": "claude-cli", "account_pool": "anthropic_max", "input_tokens": 3})
    second = adapter.export_new_spans()

    assert second.written is True
    assert second.event_count == 1
    second_packet = json.loads(second.packet_path.read_text())
    assert [row["event_id"] for row in second_packet["spans"]] == ["evt_3"]
    assert json.loads(state_path.read_text())["last_event_id"] == "evt_3"

    third = adapter.export_new_spans()

    assert third.written is False
    assert third.event_count == 0


def _append_jsonl(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")
