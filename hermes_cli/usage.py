"""Local read surface for the Hermes usage ledger."""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import Any, Iterable

from hermes_constants import get_hermes_home

_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)


def default_usage_ledger_path() -> Path:
    return get_hermes_home() / "usage_ledger" / "spans.jsonl"


def iter_usage_spans(path: str | Path | None = None) -> Iterable[dict[str, Any]]:
    ledger_path = Path(path) if path else default_usage_ledger_path()
    if not ledger_path.exists():
        return
    with ledger_path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {ledger_path}:{line_no}: {exc}") from exc
            if isinstance(payload, dict):
                yield payload


def _matches(span: dict[str, Any], args: Namespace) -> bool:
    provider = getattr(args, "provider", None)
    if provider and span.get("provider") != provider:
        return False
    account_pool = getattr(args, "account_pool", None)
    if account_pool and span.get("account_pool") != account_pool:
        return False
    session_id = getattr(args, "session_id", None)
    if session_id and span.get("session_id") != session_id:
        return False
    return True


def read_usage_spans(args: Namespace) -> list[dict[str, Any]]:
    rows = [span for span in iter_usage_spans(getattr(args, "path", None)) if _matches(span, args)]
    limit = getattr(args, "limit", None)
    if limit is not None and limit > 0:
        rows = rows[-limit:]
    return rows


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


def summarize_usage_spans(rows: list[dict[str, Any]]) -> dict[str, Any]:
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


def _print_spans_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No usage spans found.")
        return
    print("created_at provider model account_pool in out cost event_id")
    for row in rows:
        print(
            " ".join(
                [
                    str(row.get("created_at", "-")),
                    str(row.get("provider", "-")),
                    str(row.get("model", "-")),
                    str(row.get("account_pool", "-")),
                    str(row.get("input_tokens", 0)),
                    str(row.get("output_tokens", 0)),
                    str(row.get("estimated_cost_usd", "-")),
                    str(row.get("event_id", "-")),
                ]
            )
        )


def _print_summary_table(summary: dict[str, Any]) -> None:
    totals = summary["totals"]
    print(
        "Total: "
        f"events={totals['events']} "
        f"input_tokens={totals['input_tokens']} "
        f"output_tokens={totals['output_tokens']} "
        f"cache_read_tokens={totals['cache_read_tokens']} "
        f"cache_write_tokens={totals['cache_write_tokens']} "
        f"reasoning_tokens={totals['reasoning_tokens']} "
        f"estimated_cost_usd={totals['estimated_cost_usd']:.6f}"
    )
    if summary["by_account_pool"]:
        print("\nBy account pool:")
        for pool, row in sorted(summary["by_account_pool"].items()):
            print(
                f"  {pool}: events={row['events']} "
                f"input={row['input_tokens']} output={row['output_tokens']} "
                f"cost={row['estimated_cost_usd']:.6f}"
            )


def _usage_proof(args: Namespace) -> dict[str, Any]:
    from agent.usage_ledger import UsageLedgerEvent, write_usage_span
    from agent.usage_ledger_forge import (
        build_forge_observer_packet,
        write_forge_observer_packet,
    )

    ledger_path = Path(getattr(args, "path", None) or default_usage_ledger_path())
    forge_packet_path = Path(
        getattr(args, "out", None)
        or (get_hermes_home() / "usage_ledger" / "forge-observer-proof.json")
    )
    event = UsageLedgerEvent(
        event_id="evt_usage_ledger_local_proof",
        trace_id="trace_usage_ledger_local_proof",
        session_id="usage-ledger-local-proof",
        source="cli",
        provider="openai-codex",
        model="gpt-5.5",
        model_family="openai",
        account_pool="openai_max",
        runtime_adapter="codex_cli",
        subscription_mode="session_subscription",
        event_type="model_call",
        status_class="success",
        input_tokens=1,
        output_tokens=1,
        metadata={"check_kind": "synthetic_usage_ledger_local_check"},
    )
    result = write_usage_span(event, enabled=True, path=ledger_path)
    rows = list(iter_usage_spans(ledger_path))
    packet = build_forge_observer_packet(rows)
    written = write_forge_observer_packet(packet, forge_packet_path)
    return {
        "span_written": result.written,
        "ledger_path": str(result.path or ledger_path),
        "forge_packet_path": str(written),
        "forge_event_count": packet["event_count"],
        "adapter_mode": packet["adapter_mode"],
    }


def usage_command(args: Namespace) -> int:
    command = getattr(args, "usage_command", None) or "summary"
    json_output = bool(getattr(args, "json", False))

    if command == "proof":
        proof = _usage_proof(args)
        if json_output:
            print(json.dumps(proof, indent=2, sort_keys=True))
        else:
            print(
                "Usage ledger local proof wrote "
                f"span={proof['span_written']} "
                f"ledger={proof['ledger_path']} "
                f"forge_packet={proof['forge_packet_path']} "
                f"events={proof['forge_event_count']}"
            )
        return 0

    try:
        rows = read_usage_spans(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if command == "spans":
        if json_output:
            print(json.dumps(rows, indent=2, sort_keys=True))
        else:
            _print_spans_table(rows)
        return 0

    if command == "summary":
        summary = summarize_usage_spans(rows)
        if json_output:
            print(json.dumps(summary, indent=2, sort_keys=True))
        else:
            _print_summary_table(summary)
        return 0

    if command == "forge-export":
        from agent.usage_ledger_forge import (
            build_forge_observer_packet,
            write_forge_observer_packet,
        )

        out_path = Path(
            getattr(args, "out", None)
            or (get_hermes_home() / "usage_ledger" / "forge-observer.json")
        )
        packet = build_forge_observer_packet(rows)
        written = write_forge_observer_packet(packet, out_path)
        if json_output:
            print(
                json.dumps(
                    {"written": str(written), "event_count": packet["event_count"]},
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            print(f"Wrote Forge observer packet: {written} ({packet['event_count']} spans)")
        return 0

    if command == "forge-observe":
        from agent.usage_ledger_forge import ForgeObserverAdapter

        ledger_path = Path(getattr(args, "path", None) or default_usage_ledger_path())
        packet_dir = Path(
            getattr(args, "packet_dir", None)
            or (get_hermes_home() / "usage_ledger" / "forge-observer-packets")
        )
        state_path = Path(
            getattr(args, "state", None)
            or (get_hermes_home() / "usage_ledger" / "forge-observer-state.json")
        )
        result = ForgeObserverAdapter(
            ledger_path=ledger_path,
            packet_dir=packet_dir,
            state_path=state_path,
        ).export_new_spans()
        payload = {
            "written": result.written,
            "event_count": result.event_count,
            "packet_path": str(result.packet_path),
            "state_path": str(result.state_path),
            "adapter_mode": result.adapter_mode,
            "network_io": result.network_io,
        }
        if json_output:
            print(json.dumps(payload, indent=2, sort_keys=True))
        elif result.written:
            print(f"Wrote Forge observer packet: {result.packet_path} ({result.event_count} new spans)")
        else:
            print("No new usage spans for Forge observer export.")
        return 0

    print(f"unknown usage command: {command}", file=sys.stderr)
    return 2


def register_usage_parser(subparsers) -> ArgumentParser:
    parser = subparsers.add_parser(
        "usage",
        help="Inspect local usage ledger spans",
        description="Read metadata-only usage ledger spans from the local append-only JSONL ledger.",
    )
    usage_subparsers = parser.add_subparsers(dest="usage_command")

    def add_common_args(p: ArgumentParser) -> None:
        p.add_argument("--path", default=None, help="Ledger JSONL path (default: $HERMES_HOME/usage_ledger/spans.jsonl)")
        p.add_argument("--provider", default=None, help="Filter by provider")
        p.add_argument("--account-pool", default=None, help="Filter by account pool")
        p.add_argument("--session-id", default=None, help="Filter by Hermes session id")
        p.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    spans = usage_subparsers.add_parser("spans", help="List usage ledger spans")
    add_common_args(spans)
    spans.add_argument("--limit", type=int, default=50, help="Maximum spans to print (default: 50)")

    summary = usage_subparsers.add_parser("summary", help="Summarize usage ledger spans")
    add_common_args(summary)

    forge = usage_subparsers.add_parser(
        "forge-export",
        help="Write a metadata-only local packet for a Forge observer",
        description=(
            "Export usage spans to a metadata-only JSON packet. "
            "This does not contact Forge or mutate services."
        ),
    )
    add_common_args(forge)
    forge.add_argument(
        "--out",
        default=None,
        help="Output JSON packet path (default: $HERMES_HOME/usage_ledger/forge-observer.json)",
    )

    observe = usage_subparsers.add_parser(
        "forge-observe",
        help="Export only new usage spans for a local Forge observer",
        description=(
            "Read the local usage ledger, write a metadata-only packet containing only spans "
            "after the observer cursor, and update local cursor state. This performs no network I/O."
        ),
    )
    observe.add_argument("--path", default=None, help="Ledger JSONL path (default: $HERMES_HOME/usage_ledger/spans.jsonl)")
    observe.add_argument(
        "--packet-dir",
        default=None,
        help="Directory for observer packets (default: $HERMES_HOME/usage_ledger/forge-observer-packets)",
    )
    observe.add_argument(
        "--state",
        default=None,
        help="Observer cursor state path (default: $HERMES_HOME/usage_ledger/forge-observer-state.json)",
    )
    observe.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    proof = usage_subparsers.add_parser(
        "proof",
        help="Run a synthetic local proof of the ledger reader/writer/export path",
        description=(
            "Write one synthetic metadata-only span and a Forge observer packet. "
            "This does not call a model, contact Forge, or mutate services."
        ),
    )
    proof.add_argument("--path", default=None, help="Ledger JSONL path (default: $HERMES_HOME/usage_ledger/spans.jsonl)")
    proof.add_argument(
        "--out",
        default=None,
        help="Output JSON packet path (default: $HERMES_HOME/usage_ledger/forge-observer-proof.json)",
    )
    proof.add_argument("--json", action="store_true", help="Print machine-readable JSON")

    parser.set_defaults(func=lambda args: sys.exit(usage_command(args)), usage_command="summary")
    return parser
