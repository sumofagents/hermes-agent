from __future__ import annotations

import json
from argparse import Namespace

from hermes_cli.usage import usage_command


def _write_span(path, **overrides):
    payload = {
        "event_id": "evt_1",
        "trace_id": "trace_1",
        "created_at": "2026-05-26T00:00:00+00:00",
        "event_type": "model_call",
        "status_class": "success",
        "provider": "openai-codex",
        "model": "gpt-5.5",
        "account_pool": "openai_max",
        "subscription_mode": "session_subscription",
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 2,
        "cache_write_tokens": 0,
        "reasoning_tokens": 1,
        "estimated_cost_usd": None,
        "metadata": {"repo": "hermes-agent"},
    }
    payload.update(overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")


def test_usage_spans_json_reads_local_ledger_with_filters(tmp_path, capsys):
    ledger = tmp_path / "usage_ledger" / "spans.jsonl"
    _write_span(ledger, event_id="evt_codex", provider="openai-codex", account_pool="openai_max")
    _write_span(ledger, event_id="evt_or", provider="openrouter", account_pool="openrouter_prepay")

    exit_code = usage_command(
        Namespace(
            usage_command="spans",
            path=str(ledger),
            limit=10,
            provider="openrouter",
            account_pool=None,
            session_id=None,
            json=True,
        )
    )

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert [row["event_id"] for row in out] == ["evt_or"]
    assert out[0]["account_pool"] == "openrouter_prepay"


def test_usage_summary_aggregates_tokens_by_account_pool(tmp_path, capsys):
    ledger = tmp_path / "usage_ledger" / "spans.jsonl"
    _write_span(ledger, event_id="evt_a", account_pool="openai_max", input_tokens=10, output_tokens=5)
    _write_span(ledger, event_id="evt_b", account_pool="openai_max", input_tokens=7, output_tokens=3)
    _write_span(ledger, event_id="evt_c", account_pool="openrouter_prepay", input_tokens=2, output_tokens=1, estimated_cost_usd=0.25)

    exit_code = usage_command(
        Namespace(
            usage_command="summary",
            path=str(ledger),
            provider=None,
            account_pool=None,
            session_id=None,
            json=True,
        )
    )

    assert exit_code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["totals"]["events"] == 3
    assert out["totals"]["input_tokens"] == 19
    assert out["totals"]["output_tokens"] == 9
    assert out["by_account_pool"]["openai_max"]["events"] == 2
    assert out["by_account_pool"]["openrouter_prepay"]["estimated_cost_usd"] == 0.25
