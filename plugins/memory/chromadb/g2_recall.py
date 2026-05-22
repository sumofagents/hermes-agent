"""Goal 2 provider-side recall helpers for ChromaDB memory."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from agent.recall_gate import RiskResult

_NORMALIZE_SPACE = re.compile(r"\s+")


@dataclass
class RecallCandidate:
    fact_id: str
    collection: str
    content: str
    score: float
    source: str
    target: str
    durability: str
    rank: int
    query_index: int


def _normalize_content(content: str) -> str:
    return _NORMALIZE_SPACE.sub(" ", (content or "").strip().lower())


def normalized_hash(content: str) -> str:
    return hashlib.sha256(_normalize_content(content).encode("utf-8")).hexdigest()


def dedup_candidates(candidates: Iterable[RecallCandidate]) -> tuple[list[RecallCandidate], dict[str, str]]:
    seen: set[str] = set()
    kept: list[RecallCandidate] = []
    dropped: dict[str, str] = {}
    for candidate in candidates:
        h = normalized_hash(candidate.content)
        if h in seen:
            dropped[candidate.fact_id] = "duplicate"
            continue
        seen.add(h)
        kept.append(candidate)
    return kept, dropped


def filter_ephemeral(candidates: Iterable[RecallCandidate], *, allow_ephemeral: bool) -> tuple[list[RecallCandidate], dict[str, str]]:
    kept: list[RecallCandidate] = []
    dropped: dict[str, str] = {}
    for candidate in candidates:
        if candidate.durability == "ephemeral" and not allow_ephemeral:
            dropped[candidate.fact_id] = "ephemeral"
            continue
        kept.append(candidate)
    return kept, dropped


def _reason(risk: RiskResult) -> str:
    return "+".join(risk.risk_classes) if risk.risk_classes else "opportunistic"


def _snippet(content: str, max_chars: int = 500) -> str:
    text = _NORMALIZE_SPACE.sub(" ", (content or "").strip())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def render_recall_block_with_candidates(
    candidates: Iterable[RecallCandidate],
    risk: RiskResult,
    *,
    char_budget: int = 3500,
) -> tuple[str, list[RecallCandidate]]:
    selected = list(candidates)

    def build(rows: list[RecallCandidate]) -> str:
        lines = [
            "## Enforced Memory Recall",
            f"Reason: {_reason(risk)}; mandatory={str(bool(risk.mandatory)).lower()}",
            "Instruction: Use this retrieved context before asking the user to repeat information. If a needed value is not present, ask only for the missing value and say what was already found.",
            "",
            "Sources:",
        ]
        for c in rows:
            lines.append(
                f"- [{c.collection}:{c.fact_id}] score={float(c.score):.3f} source={c.source or ''} target={c.target or ''} durability={c.durability or 'unknown'}"
            )
            lines.append(f"  {_snippet(c.content)}")
        return "\n".join(lines).strip()

    block = build(selected)
    while len(block) > char_budget and selected:
        selected.pop()
        block = build(selected)
    if len(block) > char_budget:
        return block[:char_budget], []
    return block, selected


def render_recall_block(
    candidates: Iterable[RecallCandidate],
    risk: RiskResult,
    *,
    char_budget: int = 3500,
) -> str:
    block, _selected = render_recall_block_with_candidates(
        candidates, risk, char_budget=char_budget
    )
    return block


def candidate_from_row(row: dict[str, Any], *, collection: str, rank: int, query_index: int) -> RecallCandidate:
    meta = dict(row.get("metadata") or {})
    return RecallCandidate(
        fact_id=str(row.get("id") or ""),
        collection=collection,
        content=str(row.get("content") or ""),
        score=float(row.get("composite_score", row.get("score", 0.0)) or 0.0),
        source=str(meta.get("source") or ""),
        target=str(meta.get("target") or meta.get("collection_type") or ""),
        durability=str(row.get("durability_label") or row.get("durability") or "unknown"),
        rank=rank,
        query_index=query_index,
    )


def select_within_limits(
    candidates: Iterable[RecallCandidate],
    *,
    max_memory: int = 8,
    max_sessions: int = 5,
    max_team: int = 5,
) -> list[RecallCandidate]:
    counts = {"memory": 0, "session": 0, "team": 0}
    selected: list[RecallCandidate] = []
    for c in sorted(candidates, key=lambda item: item.score, reverse=True):
        kind = "memory"
        if c.collection == "sessions":
            kind = "session"
        elif c.collection in {"team_knowledge", "team_ops"}:
            kind = "team"
        limit = {"memory": max_memory, "session": max_sessions, "team": max_team}[kind]
        if counts[kind] >= limit:
            continue
        counts[kind] += 1
        selected.append(c)
    return selected
