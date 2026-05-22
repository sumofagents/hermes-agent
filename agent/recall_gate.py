"""Deterministic Goal 2 first-turn semantic-recall gate.

Pure helpers only: no ChromaDB, network, config, or filesystem imports. The
runtime uses this before the first LLM call for a turn to decide whether a
synchronous memory recall pass is mandatory, opportunistic, or unnecessary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

RISK_NO_RECALL = "no_recall"
RISK_OPPORTUNISTIC = "opportunistic"
RISK_MANDATORY = "mandatory"

_JOB_TERMS = re.compile(
    r"\b(job\s+applications?|employment\s+forms?|employment\s+applications?|resumes?|cover\s+letters?|"
    r"recruiter\s+repl(?:y|ies)|recruiter|work\s+authorization|clearance|sponsorship|compensation|demographics|"
    r"essential[-\s]+functions?|application\s+portals?)\b",
    re.I,
)
_APPLICATION_WITH_EMPLOYMENT_CONTEXT = re.compile(
    r"\b(fill|submit|complete|answer|employment|job|authorization|clearance|sponsorship|"
    r"resume|education|cover\s+letter|recruiter|anduril|spacex)\b[\s\S]{0,80}\bapplication\b|"
    r"\bapplication\b[\s\S]{0,80}\b(fill|submit|complete|answer|employment|job|authorization|"
    r"clearance|sponsorship|resume|education|cover\s+letter|recruiter|anduril|spacex)\b",
    re.I,
)
_CONTINUITY = re.compile(
    r"\b(same\s+as\s+before|use\s+what\s+we\s+used\s+before|as\s+discussed|"
    r"already\s+discussed|you\s+know\s+this|why\s+are\s+you\s+asking|"
    r"don'?t\s+you\s+remember|continue\s+from\s+last\s+time|resume\s+where\s+we\s+left\s+off|"
    r"reuse|using\s+what\s+you\s+know\s+from|what\s+you\s+know\s+from\s+the|"
    r"from\s+the\s+previous\s+application|from\s+the\s+spacex\s+application)\b",
    re.I,
)
_PROFILE = re.compile(
    r"\b(identity|who\s+am\s+i|my\s+education|education\s+history|employment\s+history|"
    r"where\s+do\s+i\s+live|my\s+location|my\s+preferences?|legal\s+constraints?|"
    r"long[-\s]+term\s+goals?|family|personal\s+facts?)\b",
    re.I,
)
_FLEET_NAMES = re.compile(r"\b(Rilo|Scout|Caddie|Ledger|Librarian|Wanderer|Foundry|Atlas|Hermes)\b")
_FLEET_CONTEXT = re.compile(r"\b(status|continue|resume|prior|previous|decision|plan|work|project|agent|fleet|what\s+we\s+did)\b", re.I)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class RiskResult:
    level: str
    risk_classes: tuple[str, ...]
    labels: tuple[str, ...]
    mandatory: bool


def sanitize_for_query(message: str) -> str:
    text = _CONTROL_CHARS.sub(" ", message or "")
    return _SPACE.sub(" ", text).strip()


def classify_risk(message: str) -> RiskResult:
    text = sanitize_for_query(message)
    classes: list[str] = []

    if _JOB_TERMS.search(text) or _APPLICATION_WITH_EMPLOYMENT_CONTEXT.search(text):
        classes.append("job_application")
    if _CONTINUITY.search(text):
        classes.append("continuity")
    if _PROFILE.search(text):
        classes.append("personal_profile")
    if _FLEET_NAMES.search(text) and _FLEET_CONTEXT.search(text):
        classes.append("fleet_project")

    deduped: list[str] = []
    for cls in classes:
        if cls not in deduped:
            deduped.append(cls)

    if deduped:
        labels = tuple(deduped)
        return RiskResult(
            level=RISK_MANDATORY,
            risk_classes=tuple(deduped),
            labels=labels,
            mandatory=True,
        )
    return RiskResult(level=RISK_NO_RECALL, risk_classes=(), labels=(), mandatory=False)


def build_queries(message: str, risk: RiskResult) -> list[str]:
    base = sanitize_for_query(message)
    queries: list[str] = []
    if base:
        queries.append(base)
    classes = set(risk.risk_classes)
    if "job_application" in classes:
        queries.append(
            "job application resume work authorization clearance sponsorship education employment history prior application answers SpaceX Anduril"
        )
    if "continuity" in classes:
        queries.append("same as before previous answer prior session user preferences durable facts")
    if classes.intersection({"job_application", "personal_profile"}):
        queries.append("durable user profile identity preferences legal constraints education employment location")
    if "fleet_project" in classes:
        queries.append("project status prior decisions durable conventions current agent context")

    out: list[str] = []
    for q in queries:
        cleaned = sanitize_for_query(q)
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out[:4]


def append_ephemeral_context_to_user_message(
    api_msg: dict,
    injections: list[str],
) -> dict:
    """Append ephemeral context to an API-message copy without mutating source.

    ``api_msg`` is expected to already be a shallow copy of the stored message.
    Returning it makes the behavior easy to unit-test and keeps G2 injection out
    of the persisted conversation/session DB path.
    """
    clean = [part for part in injections if isinstance(part, str) and part.strip()]
    if not clean:
        return api_msg
    base = api_msg.get("content", "")
    if isinstance(base, str):
        api_msg["content"] = base + "\n\n" + "\n\n".join(clean)
    return api_msg


def render_degraded_notice(risk: RiskResult, *, char_budget: int = 3500) -> str:
    reason = "+".join(risk.risk_classes) if risk.risk_classes else "mandatory_recall"
    notice = (
        "## Enforced Memory Recall\n"
        f"Reason: {reason}; mandatory={str(bool(risk.mandatory)).lower()}\n"
        "Instruction: Stored memory could not be reached before this memory-dependent turn. "
        "Tell the user that stored memory could not be reached before relying on prior facts; "
        "ask only for missing information and do not pretend recall succeeded.\n\n"
        "Sources:\n"
        "- [degraded:memory_unavailable] score=0.000 source=none target=none durability=unknown\n"
        "  Stored memory could not be reached for this turn.\n"
    )
    return notice[:char_budget]
