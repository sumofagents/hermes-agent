"""Observer-only Forge adapter for usage ledger spans.

This module prepares metadata-only packets that a Forge-side observer can
consume later.  It intentionally performs no network I/O and makes no routing,
service, Sentinel, Manifold, or Workspace changes.
"""

from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ALLOWED_SPAN_FIELDS = (
    "event_id",
    "trace_id",
    "parent_span_id",
    "session_id",
    "profile",
    "agent_id",
    "host",
    "source",
    "provider",
    "model",
    "model_family",
    "account_pool",
    "runtime_adapter",
    "api_mode",
    "credential_account_label",
    "subscription_mode",
    "session_window_id",
    "weekly_window_id",
    "fallback_reason",
    "event_type",
    "status_class",
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
    "estimated_cost_usd",
    "actual_cost_usd",
    "billing_mode",
    "wall_ms",
    "active_model_ms",
    "tool_ms",
    "subprocess_ms",
    "idle_ms",
    "no_progress_ms",
    "budget_policy_id",
    "prior_event_id",
    "supersedes_event_id",
    "created_at",
)
_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)


def build_forge_observer_packet(
    spans: list[dict[str, Any]],
    *,
    source_host: str | None = None,
    generated_at: str | None = None,
    max_spans: int = 1000,
) -> dict[str, Any]:
    """Build a metadata-only local packet for a Forge usage observer."""
    selected = spans[-max_spans:] if max_spans > 0 else spans
    safe_spans = [_sanitize_span(row) for row in selected]
    return {
        "schema_version": 1,
        "observer": "forge",
        "adapter_mode": "observer_only_file_export",
        "source_host": source_host or socket.gethostname(),
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "event_count": len(safe_spans),
        "summary": _summarize(safe_spans),
        "spans": safe_spans,
    }


def write_forge_observer_packet(
    packet: dict[str, Any],
    path: str | Path,
) -> Path:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out_path


def _sanitize_span(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row[field] for field in _ALLOWED_SPAN_FIELDS if field in row and row[field] is not None}


def _empty_counter() -> dict[str, Any]:
    return {
        "events": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
        "estimated_cost_usd": 0.0,
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = _empty_counter()
    by_account_pool: dict[str, dict[str, Any]] = {}
    by_provider: dict[str, dict[str, Any]] = {}
    for row in rows:
        _add_row(totals, row)
        pool = str(row.get("account_pool") or "unknown")
        provider = str(row.get("provider") or "unknown")
        _add_row(by_account_pool.setdefault(pool, _empty_counter()), row)
        _add_row(by_provider.setdefault(provider, _empty_counter()), row)
    return {
        "totals": totals,
        "by_account_pool": by_account_pool,
        "by_provider": by_provider,
    }


def _add_row(bucket: dict[str, Any], row: dict[str, Any]) -> None:
    bucket["events"] += 1
    for field in _TOKEN_FIELDS:
        bucket[field] += int(row.get(field) or 0)
    cost = row.get("estimated_cost_usd")
    if cost is not None:
        bucket["estimated_cost_usd"] += float(cost)
