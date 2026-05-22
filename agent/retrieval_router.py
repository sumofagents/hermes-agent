"""Goal 3 deterministic retrieval planner and context renderer.

Pure helpers only: no ChromaDB, network, config, or filesystem imports. The
runtime can use these helpers before the first LLM call to decide which
read-only retrieval routes are eligible for a turn. Safety-critical decisions
stay deterministic; optional LLM refinement may only narrow/reorder/rewrite
queries for routes already allowed by this planner.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlparse

from agent.recall_gate import RiskResult, build_queries, classify_risk, sanitize_for_query


class ComplexityTier(str, Enum):
    NONE = "none"
    SIMPLE = "simple"
    STANDARD = "standard"
    COMPLEX = "complex"


class RouteKind(str, Enum):
    MEMORY_SEMANTIC = "memory_semantic"
    SESSION_SEMANTIC = "session_semantic"
    SESSION_FTS = "session_fts"
    WEB_SEARCH = "web_search"
    WEB_EXTRACT = "web_extract"
    FILE_SEARCH = "file_search"
    FILE_READ = "file_read"
    TOOL_RECALL = "tool_recall"


@dataclass(frozen=True)
class RetrievalRoute:
    kind: RouteKind
    queries: tuple[str, ...]
    mandatory: bool = False
    read_only: bool = True
    private_query: bool = True
    char_budget: int = 1200
    timeout_ms: int = 1500
    reason: str = ""


@dataclass(frozen=True)
class RetrievalPlan:
    enabled: bool
    mandatory: bool
    labels: tuple[str, ...]
    risk: RiskResult
    complexity_tier: ComplexityTier
    routes: tuple[RetrievalRoute, ...]
    char_budget: int = 3500
    latency_budget_ms: int = 5000
    reason: str = ""


_URL_RE = re.compile(r"https?://[^\s)>\"']+", re.I)
_CURRENT_PUBLIC_RE = re.compile(
    r"\b(latest|current|today|news|recent|now|price|weather|who\s+won|standings|public)\b",
    re.I,
)
_FILE_RE = re.compile(
    r"\b(this\s+repo|repository|codebase|file|files|path|search\s+(?:this\s+)?repo|read\s+file|open\s+file)\b",
    re.I,
)
_SECRETISH_FILE_RE = re.compile(
    r"(^|[=/])("
    r"\.env(?:\..*)?|\.npmrc(?:\..*)?|credentials(?:\..*)?|id_rsa(?:\..*)?|id_ed25519(?:\..*)?|"
    r"[^\s/]*(?:secret|token|api[_-]?key|access[_-]?key|password|passwd|pwd)[^\s/]*"
    r")$",
    re.I,
)
_SECRETISH_URL_RE = re.compile(
    r"\b(token|secret|api[_-]?key|access[_-]?key|password|passwd|pwd|auth|session|bearer|"
    r"jeremiah|profile|medical|diagnosis|health|private|personal|resume|application)\b",
    re.I,
)
_PRIVATE_WEB_BLOCK_RE = re.compile(
    r"\b(my|mine|about\s+me|jeremiah|medical|diagnosis|health|doctor|therapy|ssn|social\s+security|"
    r"password|token|secret|api\s*key|private|personal|profile|resume|application|job|role|applied|"
    r"address|phone|email|family|legal|bank|credit|income|salary)\b",
    re.I,
)
_SPACE = re.compile(r"\s+")
_NUMERICISH_HOST_RE = re.compile(
    r"^(?:0x[0-9a-f]+|[0-9a-f]+)(?:\.(?:0x[0-9a-f]+|[0-9a-f]+))*$",
    re.I,
)
_SECRET_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_SECRET_URL_TOKENS = (
    "token",
    "secret",
    "apikey",
    "accesskey",
    "password",
    "passwd",
    "pwd",
    "auth",
    "session",
    "bearer",
)
_SECRET_FILE_EXACT = {".env", ".npmrc", "credentials", "id_rsa", "id_ed25519"}


def _dedup(items: Iterable[str], *, limit: int = 6) -> tuple[str, ...]:
    out: list[str] = []
    for item in items:
        cleaned = sanitize_for_query(str(item or ""))
        if cleaned and cleaned not in out:
            out.append(cleaned)
        if len(out) >= limit:
            break
    return tuple(out)


def _route_kinds(routes: Iterable[RetrievalRoute]) -> set[RouteKind]:
    return {route.kind for route in routes}


def _complexity_for(message: str, risk: RiskResult, route_count: int) -> ComplexityTier:
    if risk.mandatory:
        if len(risk.risk_classes) > 1 or route_count >= 3:
            return ComplexityTier.COMPLEX
        return ComplexityTier.STANDARD
    text = sanitize_for_query(message)
    if not route_count:
        return ComplexityTier.NONE
    if len(text) < 120 and route_count == 1:
        return ComplexityTier.SIMPLE
    return ComplexityTier.STANDARD


def _message_without_urls(message: str) -> str:
    return _URL_RE.sub(" ", message or "")


def _secret_keyish(value: str) -> bool:
    normalized = _SECRET_NORMALIZE_RE.sub("", (value or "").lower())
    return any(token in normalized for token in _SECRET_URL_TOKENS)


def _public_web_allowed(message: str, risk: RiskResult) -> bool:
    text = sanitize_for_query(_message_without_urls(message))
    if risk.mandatory:
        return False
    if _FILE_RE.search(text):
        return False
    if _PRIVATE_WEB_BLOCK_RE.search(text):
        return False
    return bool(_CURRENT_PUBLIC_RE.search(text))


def _public_url_extract_allowed(message: str, risk: RiskResult) -> bool:
    text = sanitize_for_query(_message_without_urls(message))
    if risk.mandatory:
        return False
    if _FILE_RE.search(text):
        return False
    if _PRIVATE_WEB_BLOCK_RE.search(text):
        return False
    return True


def _strip_token_punctuation(token: str) -> str:
    # Preserve leading dots for dotfiles such as .env while removing trailing
    # sentence punctuation that users often include in natural-language prompts.
    return token.strip("()[]{}<>\"'").rstrip(".,;:!?")


def _host_is_public(hostname: str | None) -> bool:
    if not hostname:
        return False
    host = hostname.strip().lower().rstrip(".")
    if not host or host == "localhost" or host.endswith(".local"):
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Numeric-looking hostnames can be accepted by platform resolvers as
        # alternate IPv4 spellings (for example 2130706433 or 127.1 -> loopback).
        # They are never needed for Phase A public web descriptors, so fail closed.
        if _NUMERICISH_HOST_RE.fullmatch(host):
            return False
        return True
    return ip.is_global and not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or bool(getattr(ip, "is_site_local", False))
    )


def _url_is_public(url: str) -> bool:
    text = sanitize_for_query(url)
    if not _URL_RE.fullmatch(text):
        return False
    try:
        parsed = urlparse(text)
    except ValueError:
        return False
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return False
    if parsed.username or parsed.password:
        return False
    if not _host_is_public(parsed.hostname):
        return False
    haystack = " ".join(
        part
        for part in [parsed.netloc, parsed.path, parsed.fragment]
        if part
    )
    if _SECRETISH_URL_RE.search(haystack):
        return False
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if _SECRETISH_URL_RE.search(key) or _SECRETISH_URL_RE.search(value):
            return False
        if _secret_keyish(key) or _secret_keyish(value):
            return False
    return True


def _web_query_is_public(query: str) -> bool:
    text = sanitize_for_query(query)
    if not text or _PRIVATE_WEB_BLOCK_RE.search(text) or _FILE_RE.search(text):
        return False
    if _URL_RE.fullmatch(text):
        return _url_is_public(text)
    return True


def _extract_urls(message: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_URL_RE.findall(message or "")))


def _file_token_is_secret(token: str) -> bool:
    normalized = _strip_token_punctuation(token)
    parts = [part for chunk in normalized.split("=") for part in chunk.split("/") if part]
    for part in parts:
        lowered = part.lower()
        if lowered in _SECRET_FILE_EXACT or lowered.startswith(".env."):
            return True
        if _SECRETISH_FILE_RE.search(lowered):
            return True
        if _secret_keyish(lowered):
            return True
    return False


def _file_query(message: str) -> str:
    text = sanitize_for_query(message)
    # Keep this conservative: route descriptor only, no filesystem access here.
    for token in text.split():
        if _file_token_is_secret(token):
            return ""
    return text


def _file_queries_are_safe(queries: Iterable[str]) -> bool:
    return all(bool(_file_query(query)) for query in queries)


def build_deterministic_plan(
    message: str,
    *,
    enabled: bool = True,
    char_budget: int = 3500,
    latency_budget_ms: int = 5000,
) -> RetrievalPlan:
    """Build a deterministic, read-only retrieval plan for a user turn.

    G2 mandatory memory recall remains the floor. G3 can add session/file/web
    route descriptors, but private/profile/job/continuity classes never route to
    web and all produced routes are read-only.
    """
    risk = classify_risk(message)
    if not enabled:
        return RetrievalPlan(
            enabled=False,
            mandatory=False,
            labels=(),
            risk=risk,
            complexity_tier=ComplexityTier.NONE,
            routes=(),
            char_budget=char_budget,
            latency_budget_ms=latency_budget_ms,
            reason="disabled",
        )

    routes: list[RetrievalRoute] = []
    memory_queries = _dedup(build_queries(message, risk))
    if risk.mandatory and memory_queries:
        routes.extend(
            [
                RetrievalRoute(
                    kind=RouteKind.MEMORY_SEMANTIC,
                    queries=memory_queries,
                    mandatory=True,
                    private_query=True,
                    reason="g2_mandatory_memory",
                ),
                RetrievalRoute(
                    kind=RouteKind.SESSION_SEMANTIC,
                    queries=memory_queries,
                    mandatory=True,
                    private_query=True,
                    reason="g2_mandatory_session_semantic",
                ),
                RetrievalRoute(
                    kind=RouteKind.SESSION_FTS,
                    queries=memory_queries[:2],
                    mandatory=True,
                    private_query=True,
                    reason="g2_mandatory_session_fts",
                ),
            ]
        )

    raw_urls = _extract_urls(message)
    urls = tuple(url for url in raw_urls if _url_is_public(url))
    has_rejected_urls = bool(raw_urls) and len(urls) != len(raw_urls)
    if urls and _public_url_extract_allowed(message, risk):
        routes.append(
            RetrievalRoute(
                kind=RouteKind.WEB_EXTRACT,
                queries=urls,
                mandatory=False,
                private_query=False,
                reason="explicit_public_url",
            )
        )
    elif not has_rejected_urls and _public_web_allowed(message, risk):
        routes.append(
            RetrievalRoute(
                kind=RouteKind.WEB_SEARCH,
                queries=_dedup([message], limit=1),
                mandatory=False,
                private_query=False,
                reason="current_public_fact",
            )
        )

    file_text = sanitize_for_query(_message_without_urls(message))
    if not risk.mandatory and _FILE_RE.search(file_text):
        query = _file_query(message)
        if query:
            kind = RouteKind.FILE_READ if "read file" in query.lower() or "open file" in query.lower() else RouteKind.FILE_SEARCH
            routes.append(
                RetrievalRoute(
                    kind=kind,
                    queries=(query,),
                    mandatory=False,
                    private_query=True,
                    reason="explicit_local_file_context",
                )
            )

    # Deterministic de-dupe by route kind. First route wins so URL extraction
    # stays more specific than generic public web search.
    deduped: list[RetrievalRoute] = []
    seen: set[RouteKind] = set()
    for route in routes:
        if route.kind not in seen and route.queries:
            seen.add(route.kind)
            deduped.append(route)

    reason = "planned" if deduped else "no_retrieval_needed"
    return RetrievalPlan(
        enabled=True,
        mandatory=risk.mandatory,
        labels=risk.labels,
        risk=risk,
        complexity_tier=_complexity_for(message, risk, len(deduped)),
        routes=tuple(deduped),
        char_budget=char_budget,
        latency_budget_ms=latency_budget_ms,
        reason=reason,
    )


def merge_llm_refinement(plan: RetrievalPlan, refinement: dict[str, Any] | None) -> RetrievalPlan:
    """Merge schema-validated LLM refinements without granting authority.

    The LLM may only rewrite queries for route kinds already allowed by the
    deterministic plan. It cannot add a route, make a route public/private in a
    looser direction, raise budgets, or lower mandatory recall obligations.
    """
    if not isinstance(refinement, dict) or not plan.routes:
        return plan

    allowed = {route.kind for route in plan.routes}
    by_kind = {route.kind: route for route in plan.routes}
    merged: list[RetrievalRoute] = list(plan.routes)

    for raw in refinement.get("routes") or []:
        if not isinstance(raw, dict):
            continue
        try:
            kind = RouteKind(str(raw.get("kind") or ""))
        except ValueError:
            continue
        if kind not in allowed:
            continue
        route = by_kind[kind]
        queries = _dedup(raw.get("queries") or (), limit=len(route.queries) or 1)
        if not queries:
            continue
        if kind == RouteKind.WEB_SEARCH and not all(_web_query_is_public(q) and not _URL_RE.fullmatch(q) for q in queries):
            continue
        if kind == RouteKind.WEB_EXTRACT and not all(_url_is_public(q) for q in queries):
            continue
        if kind in {RouteKind.FILE_SEARCH, RouteKind.FILE_READ} and not _file_queries_are_safe(queries):
            continue
        replacement = replace(route, queries=queries)
        merged = [replacement if item.kind == kind else item for item in merged]
        by_kind[kind] = replacement

    # Optional reordering, constrained to existing route kinds only.
    order: list[RouteKind] = []
    for raw_kind in refinement.get("route_order") or []:
        try:
            kind = RouteKind(str(raw_kind))
        except ValueError:
            continue
        if kind in allowed and kind not in order:
            order.append(kind)
    if order:
        ordered = [by_kind[kind] for kind in order]
        ordered.extend(route for route in merged if route.kind not in set(order))
        merged = ordered

    return replace(plan, routes=tuple(merged))


def _normalize_content(content: str) -> str:
    return _SPACE.sub(" ", (content or "").strip().lower())


def _snippet(content: str, max_chars: int = 500) -> str:
    text = _SPACE.sub(" ", (content or "").strip())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"


def render_route_context(results: Iterable[dict[str, Any]], *, char_budget: int = 3500) -> str:
    """Render route results into a bounded ephemeral context block.

    Input shape is intentionally generic so executors can adapt memory/session,
    file, web, or tool results without coupling this pure module to those tools.
    """
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for result in results:
        content = str(result.get("content") or "") if isinstance(result, dict) else ""
        key = _normalize_content(content)
        if not key or key in seen:
            continue
        seen.add(key)
        rows.append(result)

    def build(selected: list[dict[str, Any]], *, compact: bool = False) -> str:
        if compact:
            lines = ["## Unified Retrieval Context", "Sources:"]
        else:
            lines = [
                "## Unified Retrieval Context",
                "Instruction: Use this retrieved context before asking the user to repeat information. If a needed value is not present, ask only for the missing value and say what was already found.",
                "",
                "Sources:",
            ]
        for result in selected:
            route = str(result.get("route") or "unknown")
            fact_id = str(result.get("id") or "")
            score = float(result.get("score", 0.0) or 0.0)
            if compact:
                lines.append(f"- [{route}:{fact_id}] {score:.3f} {_snippet(str(result.get('content') or ''), 80)}")
            else:
                lines.append(f"- [{route}:{fact_id}] score={score:.3f}")
                lines.append(f"  {_snippet(str(result.get('content') or ''), 240)}")
        return "\n".join(lines).strip()

    selected = rows[:]
    block = build(selected)
    compact = False
    while len(block) > char_budget and selected:
        if not compact:
            compact = True
            block = build(selected, compact=True)
            continue
        selected.pop()
        block = build(selected, compact=True)
    if len(block) > char_budget:
        return block[:char_budget]
    return block
