"""``hermes memory`` subcommand parser.

Extracted from ``hermes_cli/main.py:main()`` (god-file Phase 2 follow-up).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_memory_parser(subparsers, *, cmd_memory: Callable) -> None:
    """Attach the ``memory`` subcommand to ``subparsers``."""
    memory_parser = subparsers.add_parser(
        "memory",
        help="Configure external memory provider",
        description=(
            "Set up and manage external memory provider plugins.\n\n"
            "Available providers: honcho, openviking, mem0, hindsight,\n"
            "holographic, retaindb, byterover.\n\n"
            "Only one external provider can be active at a time.\n"
            "Built-in memory (MEMORY.md/USER.md) is always active."
        ),
    )
    memory_sub = memory_parser.add_subparsers(dest="memory_command")
    _setup_parser = memory_sub.add_parser(
        "setup", help="Interactive provider selection and configuration"
    )
    _setup_parser.add_argument(
        "provider",
        nargs="?",
        default=None,
        help="Provider to configure directly (e.g. honcho), skipping the picker",
    )
    memory_sub.add_parser("status", help="Show current memory provider config")
    receipts_parser = memory_sub.add_parser(
        "receipts",
        help="Summarize local boot synthesis receipts",
        description="Read-only summary of ~/.hermes/logs/boot_synthesis.jsonl",
    )
    receipts_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON summary",
    )
    receipts_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of most-recent receipts to summarize (default: 100)",
    )
    doctor_parser = memory_sub.add_parser(
        "doctor",
        help="Read-only memory readiness report and optional Chroma/Forge probe",
        description=(
            "Summarize local boot/feedback memory observability and, with --probe, "
            "verify configured ChromaDB and Forge embedding endpoints read-only."
        ),
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON report",
    )
    doctor_parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum number of most-recent JSONL records to summarize (default: 100)",
    )
    doctor_parser.add_argument(
        "--probe",
        action="store_true",
        help="Opt in to read-only ChromaDB/Forge network probes",
    )
    doctor_parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit nonzero if any executed readiness check fails",
    )
    memory_sub.add_parser("off", help="Disable external provider (built-in only)")
    _reset_parser = memory_sub.add_parser(
        "reset",
        help="Erase all built-in memory (MEMORY.md and USER.md)",
    )
    _reset_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )
    _reset_parser.add_argument(
        "--target",
        choices=["all", "memory", "user"],
        default="all",
        help="Which store to reset: 'all' (default), 'memory', or 'user'",
    )
    memory_parser.set_defaults(func=cmd_memory)
