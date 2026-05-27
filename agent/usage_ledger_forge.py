"""Observer-only Forge adapter for usage ledger spans.

This module prepares metadata-only packets that a Forge-side observer can
consume later.  It intentionally performs no network I/O and makes no routing,
service, Sentinel, Manifold, or Workspace changes.
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass
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


@dataclass(frozen=True)
class ForgeObserverExportResult:
    """Result from an observer-only local Forge export cycle."""

    written: bool
    event_count: int
    packet_path: Path
    state_path: Path
    adapter_mode: str = "observer_only_file_export"
    network_io: bool = False


class ForgeObserverAdapter:
    """Stateful, file-only Forge observer adapter for local usage spans.

    The adapter reads the append-only local ledger, writes sanitized packet files,
    and advances a cursor. It intentionally performs no network I/O and cannot
    mutate Forge, Sentinel, Manifold, Workspace, routing policy, or budgets.
    """

    def __init__(
        self,
        *,
        ledger_path: str | Path,
        packet_dir: str | Path,
        state_path: str | Path,
        source_host: str | None = None,
        max_spans: int = 1000,
    ) -> None:
        self.ledger_path = Path(ledger_path)
        self.packet_dir = Path(packet_dir)
        self.state_path = Path(state_path)
        self.source_host = source_host
        self.max_spans = max_spans

    def export_new_spans(self) -> ForgeObserverExportResult:
        rows = _read_jsonl(self.ledger_path)
        state = _read_state(self.state_path)
        new_rows = _rows_after_event_id(rows, state.get("last_event_id"))
        if not new_rows:
            return ForgeObserverExportResult(
                written=False,
                event_count=0,
                packet_path=self.packet_dir / "forge-observer-noop.json",
                state_path=self.state_path,
            )

        packet = build_forge_observer_packet(
            new_rows,
            source_host=self.source_host,
            max_spans=self.max_spans,
        )
        packet_path = self.packet_dir / _packet_filename(packet["generated_at"])
        written = write_forge_observer_packet(packet, packet_path)
        _write_state(
            self.state_path,
            {
                "schema_version": 1,
                "adapter_mode": "observer_only_file_export",
                "last_event_id": new_rows[-1].get("event_id"),
                "last_exported_at": packet["generated_at"],
                "last_packet_path": str(written),
                "network_io": False,
            },
        )
        return ForgeObserverExportResult(
            written=True,
            event_count=packet["event_count"],
            packet_path=written,
            state_path=self.state_path,
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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            payload = json.loads(raw)
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _rows_after_event_id(rows: list[dict[str, Any]], last_event_id: Any) -> list[dict[str, Any]]:
    if not last_event_id:
        return rows
    for index, row in enumerate(rows):
        if row.get("event_id") == last_event_id:
            return rows[index + 1 :]
    return rows


def _packet_filename(generated_at: str) -> str:
    safe = generated_at.replace(":", "").replace("+", "Z").replace(".", "-")
    return f"forge-observer-{safe}.json"


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
