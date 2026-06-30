"""Fisher-Rao pullback reranker for agent memory retrieval.

Implements the consumer-relative validity criterion and Fisher-Rao retrieval
geometry from:

    Thompson & Horowitz, "Manifold Destiny: Continuous Learning by Consumption
    of Truth-Verified Structure from the Zero-Information Floor" (2026).

Core idea: memory retrieval should rank by information-geometric distance on a
categorical probability simplex, not just vector cosine similarity. Each memory
is mapped to weighted semantic atoms (subjects, predicates, claim types,
facets, dates, keywords). These atoms form a probability distribution. The
retrieval distance between query and memory is the Fisher-Rao geodesic on this
simplex — the square-root Bhattacharyya angle:

    d(p, q) = arccos( sum_i sqrt(p_i * q_i) )

Validity penalties (status, supersession, scope match) are layered on top of
the geometric distance as declarative constraints, not learned weights.

No model calls. No learned weights. No external dependencies beyond the Python
standard library.
"""

from __future__ import annotations

import collections
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Text processing utilities
# ---------------------------------------------------------------------------

WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-_:'][A-Za-z0-9]+)*")
CAP_RE = re.compile(r"\b[A-Z][a-zA-Z]+\b")
EVIDENCE_RE = re.compile(r"\bD\d+:\d+\b")
ISO_DATE_RE = re.compile(r"\b(?:19|20)\d{2}(?:-\d{2}(?:-\d{2})?)?\b")

STOP = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by",
    "can", "did", "do", "does", "for", "from", "had", "has", "have", "having",
    "if", "in", "is", "it", "its", "of", "on", "or", "that", "the", "then",
    "this", "to", "was", "were", "what", "when", "where", "which", "why",
    "with", "would", "should", "could", "must", "not", "no", "yes", "about",
    "after", "before", "both", "all", "i", "me", "my", "mine", "you", "your",
    "yours", "he", "she", "they", "them", "their", "we", "us", "our",
}

CLAIM_EVENT_EXTRA_STOP = {
    "said", "told", "tell", "asked", "ask", "since", "look", "take",
    "wanted", "thanks", "thank", "just", "really", "okay", "ok", "yeah",
    "thing", "things", "something", "anything",
}

ACTION_ALIASES = {
    "went": "go", "goes": "go", "gone": "go", "going": "go",
    "attended": "attend", "attending": "attend", "attends": "attend",
    "ran": "run", "running": "run", "runs": "run",
    "painted": "paint", "painting": "paint", "paints": "paint",
    "signed": "sign", "signup": "sign", "signing": "sign", "registered": "sign",
    "researched": "research", "researching": "research", "researches": "research",
    "planned": "plan", "planning": "plan", "plans": "plan",
    "thought": "think", "thinking": "think", "thinks": "think",
    "met": "meet", "meeting": "meet", "meets": "meet",
    "moved": "move", "moving": "move", "moves": "move",
    "helped": "help", "helping": "help", "helps": "help",
    "supported": "support", "supporting": "support", "supports": "support",
    "encouraged": "encourage", "encouraging": "encourage",
    "talked": "talk", "talking": "talk", "spoke": "speak", "speech": "speak",
    "gave": "give", "given": "give", "gives": "give",
    "received": "receive", "receiving": "receive", "receives": "receive",
    "made": "make", "making": "make", "makes": "make",
    "studied": "study", "studies": "study", "studying": "study",
}

ACTION_TERMS = {
    "go", "attend", "run", "paint", "sign", "research", "plan", "think",
    "meet", "move", "help", "support", "encourage", "talk", "speak", "give",
    "receive", "make", "study", "work", "cook", "read", "watch", "play",
    "visit", "travel", "learn", "love", "like", "enjoy", "feel", "prefer",
    "build", "deploy", "configure", "install", "debug", "fix", "test",
    "write", "edit", "delete", "create", "update", "run", "start", "stop",
}

FACETS: dict[str, list[str]] = {
    "time": [
        "when", "date", "time", "year", "month", "day", "yesterday", "today",
        "tomorrow", "last", "next", "ago", "week", "morning", "evening",
        "night", "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    ],
    "person": [
        "who", "friend", "mother", "father", "mom", "dad", "sister", "brother",
        "wife", "husband", "partner", "kid", "child", "family", "mentor",
        "team", "colleague", "user", "agent", "developer",
    ],
    "place": [
        "where", "place", "location", "home", "house", "school", "office",
        "work", "server", "host", "port", "database", "cloud", "repository",
    ],
    "work": [
        "work", "job", "career", "project", "meeting", "task", "code",
        "commit", "branch", "pull request", "deploy", "build", "test",
        "production", "staging", "development",
    ],
    "emotion": [
        "feel", "feeling", "happy", "sad", "angry", "anxious", "worried",
        "excited", "frustrated", "grateful", "proud", "stressed",
    ],
    "preference": [
        "like", "likes", "liked", "love", "loves", "favorite", "prefer",
        "prefers", "enjoy", "hobby", "interest", "concise", "detailed",
    ],
    "plan": [
        "plan", "plans", "planned", "will", "want", "wants", "hope", "hopes",
        "future", "try", "trying", "schedule",
    ],
    "identity": [
        "identity", "name", "role", "title", "position", "company",
    ],
}

QUESTION_FACETS: dict[str, list[str]] = {
    "when": ["time"],
    "where": ["place"],
    "who": ["person"],
    "whom": ["person"],
    "whose": ["person"],
    "why": ["emotion", "plan"],
    "how": ["emotion", "work"],
    "what": ["work", "person", "identity"],
    "which": ["work", "preference"],
}

PLACE_TERMS = {
    "home", "house", "school", "office", "work", "server", "host", "cloud",
    "repository", "database", "production", "staging", "development",
}

EMOTION_TERMS = {
    "happy", "sad", "angry", "anxious", "worried", "excited", "frustrated",
    "grateful", "proud", "stressed",
}

RELATIONSHIP_TERMS = {
    "friend", "friends", "mother", "father", "mom", "dad", "sister", "brother",
    "wife", "husband", "partner", "kid", "kids", "children", "family",
    "neighbor", "teacher", "student", "coworker", "colleague", "mentor",
    "community", "group", "team",
}


def norm(text: str) -> str:
    """Lowercase and normalize ampersands."""
    return str(text or "").lower().replace("&", " and ")


def tokens(text: str) -> list[str]:
    """Tokenize text into lowercase word tokens."""
    return [t.lower().strip("'") for t in WORD_RE.findall(str(text or ""))]


def content_tokens(text: str) -> list[str]:
    """Return content tokens, minus stopwords and single chars."""
    out: list[str] = []
    for tok in tokens(text):
        if tok in STOP or len(tok) <= 1:
            continue
        out.append(tok)
    return out


def add(counts: dict[str, float], atom: str, weight: float) -> None:
    """Accumulate a weighted atom into the count dict."""
    if atom and weight > 0:
        counts[atom] = counts.get(atom, 0.0) + float(weight)


def add_many(counts: dict[str, float], atoms: Iterable[str], weight: float) -> None:
    """Accumulate multiple atoms with the same weight."""
    for atom in atoms:
        add(counts, atom, weight)


def csv_values(meta: dict[str, Any], key: str) -> list[str]:
    """Parse a CSV metadata field into a list of lowercased values."""
    raw = str(meta.get(key) or "")
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def canonical_term(token: str) -> str:
    """Morphological normalization: plurals, -ing, -ed, -ies -> base form."""
    tok = str(token or "").lower().strip("'\"")
    tok = ACTION_ALIASES.get(tok, tok)
    if len(tok) > 5 and tok.endswith("ies"):
        tok = tok[:-3] + "y"
    elif len(tok) > 6 and tok.endswith("ing"):
        base = tok[:-3]
        if len(base) >= 2 and base[-1] == base[-2]:
            base = base[:-1]
        tok = ACTION_ALIASES.get(base, base)
    elif len(tok) > 5 and tok.endswith("ed"):
        tok = ACTION_ALIASES.get(tok[:-2], tok[:-2])
    elif len(tok) > 4 and tok.endswith("s") and not tok.endswith("ss"):
        tok = tok[:-1]
    return ACTION_ALIASES.get(tok, tok)


# ---------------------------------------------------------------------------
# Probability simplex and Fisher-Rao distance
# ---------------------------------------------------------------------------

def normalize(counts: dict[str, float]) -> dict[str, float]:
    """L1-normalize atom counts to a probability distribution."""
    total = sum(v for v in counts.values() if v > 0)
    if total <= 0:
        return {}
    return {k: v / total for k, v in counts.items() if v > 0}


def fisher_rao_distance(p: dict[str, float], q: dict[str, float]) -> float:
    """Categorical Fisher-Rao geodesic distance on the probability simplex.

    The Fisher-Rao metric is the Riemannian metric of the Fisher information
    matrix on a statistical manifold. For categorical distributions it reduces
    to the square-root Bhattacharyya angle:

        d(p, q) = arccos( BC(p, q) )

    where BC(p, q) = sum_i sqrt(p_i * q_i) is the Bhattacharyya coefficient.

    Range: [0, pi/2]. Zero means identical distributions; pi/2 means
    disjoint support (no shared atoms).

    This is the same metric used in Section 8 of Thompson & Horowitz (2026)
    for the information manifold of verified memory retrieval.
    """
    if not p or not q:
        return math.pi / 2
    if len(p) < len(q):
        bc = sum(math.sqrt(pv * q.get(k, 0.0)) for k, pv in p.items())
    else:
        bc = sum(math.sqrt(qv * p.get(k, 0.0)) for k, qv in q.items())
    return math.acos(max(0.0, min(1.0, bc)))


# ---------------------------------------------------------------------------
# Claim/event frame extraction (write-path enrichment)
# ---------------------------------------------------------------------------

def detect_facets(text: str, *, is_query: bool = False) -> set[str]:
    """Detect semantic facets (time, person, place, etc.) from text."""
    t = norm(text)
    toks = set(content_tokens(text))
    facets: set[str] = set()
    if is_query:
        for qword, mapped in QUESTION_FACETS.items():
            if re.search(rf"\b{re.escape(qword)}\b", t):
                facets.update(mapped)
    for facet, terms in FACETS.items():
        for term in terms:
            if " " in term:
                if term in t:
                    facets.add(facet)
                    break
            elif term in toks or re.search(rf"\b{re.escape(term)}\b", t):
                facets.add(facet)
                break
    if re.search(r"\b(19|20)\d{2}\b|\b\d{1,2}:\d{2}\b", t):
        facets.add("time")
    return facets


def detect_names(text: str) -> list[str]:
    """Extract potential proper-noun names from text."""
    names: list[str] = []
    for name in CAP_RE.findall(str(text or "")):
        if name.lower() in STOP:
            continue
        names.append(name)
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        key = name.lower()
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out[:12]


def simple_keywords(text: str, limit: int = 28) -> list[str]:
    """Extract top content keywords, morphologically normalized."""
    seen: set[str] = set()
    scored: list[tuple[float, str]] = []
    for i, tok in enumerate(content_tokens(text)):
        term = canonical_term(tok)
        if not term or term in seen or term in STOP or term in CLAIM_EVENT_EXTRA_STOP:
            continue
        seen.add(term)
        score = len(term) + (4 if any(ch.isdigit() for ch in term) else 0) - i * 0.01
        scored.append((score, term))
    scored.sort(reverse=True)
    return [tok for _, tok in scored[:limit]]


def detect_question_types(
    text: str, *, is_query: bool, facets: set[str], has_relationship: bool
) -> list[str]:
    """Detect question types from text (when/where/who/what/why/how)."""
    t = norm(text)
    qtypes: set[str] = set()
    if is_query:
        if re.search(r"\bwhen\b|\bhow long\b", t):
            qtypes.add("when")
        if re.search(r"\bwhere\b", t):
            qtypes.add("where")
        if re.search(r"\bwho\b|\bwhom\b|\bwhose\b", t):
            qtypes.add("who")
        if re.search(r"\bwhy\b", t):
            qtypes.add("why")
        if re.search(r"\bhow\b", t):
            qtypes.add("how")
        if re.search(r"\bwhat\b|\bwhich\b", t):
            qtypes.add("what")
    else:
        if "time" in facets:
            qtypes.add("when")
        if "place" in facets:
            qtypes.add("where")
        if has_relationship or "person" in facets:
            qtypes.add("who")
        if "emotion" in facets:
            qtypes.update({"why", "how"})
        qtypes.add("what")
    return sorted(qtypes or {"what"})


def extract_subjects(text: str) -> list[str]:
    """Extract subject entities (names, possessive relations)."""
    subjects = detect_names(text)
    for owner, relation in re.findall(
        r"\b([A-Z][a-zA-Z]+)'s\s+(\w+)\b", str(text or "")
    ):
        subjects.append(owner.lower())
        subjects.append(canonical_term(relation))
    return list(dict.fromkeys(s for s in subjects if s))


def extract_claim_event_frame(
    source_text: str, *, is_query: bool = False
) -> dict[str, list[str]]:
    """Extract a structured claim/event frame from text.

    This is used at write time to enrich stored memory metadata, and at query
    time to build the query's atom chart. The frame decomposes text into typed
    semantic coordinates that form the chart atoms for Fisher-Rao retrieval.

    Returns a dict with keys:
        claim_types, subjects, predicates, objects, time_expressions,
        resolved_dates, places, emotions, relationships,
        supports_question_types, facets, keywords
    """
    source = str(source_text or "")
    subjects = extract_subjects(source)
    term_stream = [canonical_term(tok) for tok in content_tokens(source)]
    term_stream = [tok for tok in term_stream if tok and tok not in CLAIM_EVENT_EXTRA_STOP]
    predicates = [tok for tok in term_stream if tok in ACTION_TERMS]

    objects: list[str] = []
    for tok in term_stream:
        if tok in predicates or tok in subjects or tok in STOP or tok in CLAIM_EVENT_EXTRA_STOP:
            continue
        objects.append(tok)
    places = [tok for tok in term_stream if tok in PLACE_TERMS]
    emotions = [tok for tok in term_stream if tok in EMOTION_TERMS]
    relationships = [tok for tok in term_stream if tok in RELATIONSHIP_TERMS]
    facets = sorted(detect_facets(source, is_query=is_query))

    claim_types: set[str] = set()
    if predicates or objects:
        claim_types.add("event_activity")
    if ISO_DATE_RE.search(source) or "time" in facets:
        claim_types.add("event_time")
    if places or "place" in facets:
        claim_types.add("place")
    if emotions or "emotion" in facets:
        claim_types.add("emotion")
    if relationships or "person" in facets:
        claim_types.add("relationship")
    if "identity" in facets:
        claim_types.add("identity")
    if "plan" in facets:
        claim_types.add("plan")
    if "preference" in facets:
        claim_types.add("preference")
    if "work" in facets:
        claim_types.add("work")
    if not claim_types:
        claim_types.add("source_observation")

    time_expressions = [m.group(0).lower() for m in ISO_DATE_RE.finditer(source)]
    qtypes = detect_question_types(
        source, is_query=is_query, facets=set(facets), has_relationship=bool(relationships)
    )
    return {
        "claim_types": sorted(claim_types),
        "subjects": list(dict.fromkeys(subjects)),
        "predicates": list(dict.fromkeys(predicates)),
        "objects": list(dict.fromkeys(objects)),
        "time_expressions": list(dict.fromkeys(time_expressions)),
        "resolved_dates": list(dict.fromkeys(time_expressions)),
        "places": list(dict.fromkeys(places)),
        "emotions": list(dict.fromkeys(emotions)),
        "relationships": list(dict.fromkeys(relationships)),
        "supports_question_types": qtypes,
        "facets": facets,
        "keywords": simple_keywords(source),
    }


# ---------------------------------------------------------------------------
# Chart atoms (the observation chart for the FI pullback)
# ---------------------------------------------------------------------------

def _add_date_atoms(counts: dict[str, float], value: str, weight: float) -> None:
    """Add hierarchical date atoms (year, month, day)."""
    add(counts, f"resolved_date:{value}", weight)
    m = re.match(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$", value)
    if m:
        add(counts, f"resolved_year:{m.group(1)}", weight * 0.45)
        add(counts, f"resolved_month:{m.group(1)}-{m.group(2)}", weight * 0.75)
        if m.group(3):
            add(counts, f"resolved_day:{m.group(3)}", weight * 0.25)
    elif re.match(r"^(19|20)\d{2}$", value):
        add(counts, f"resolved_year:{value}", weight * 0.75)


def claim_event_atoms(
    text: str,
    meta: dict[str, Any] | None = None,
    doc_id: str = "",
    *,
    is_query: bool = False,
) -> dict[str, float]:
    """Build weighted chart atoms from memory text and metadata.

    This is the observation chart — it maps memory content to typed semantic
    coordinates on the probability simplex. The chart is declarative, not
    learned: atom weights are fixed by the typed structure of the claim/event
    frame, not fit to data.

    When metadata contains pre-computed CSV fields (from write-path enrichment),
    those are used directly. Otherwise the frame is extracted from text at
    retrieval time.
    """
    meta = meta or {}
    counts: dict[str, float] = {}
    source_text = str(text or "")

    if is_query:
        frame = extract_claim_event_frame(source_text, is_query=True)
        claim_types = frame["claim_types"]
        subjects = frame["subjects"]
        predicates = frame["predicates"]
        objects = frame["objects"]
        time_expressions = frame["time_expressions"]
        resolved_dates = frame["resolved_dates"]
        places = frame["places"]
        emotions = frame["emotions"]
        relationships = frame["relationships"]
        qtypes = frame["supports_question_types"]
        facets = frame["facets"]
        keywords = frame["keywords"]
    else:
        claim_types = csv_values(meta, "claim_types_csv")
        subjects = csv_values(meta, "subjects_csv") or extract_subjects(source_text)
        predicates = csv_values(meta, "predicates_csv")
        objects = csv_values(meta, "objects_csv")
        time_expressions = csv_values(meta, "time_expressions_csv")
        resolved_dates = csv_values(meta, "resolved_dates_csv")
        places = csv_values(meta, "places_csv")
        emotions = csv_values(meta, "emotions_csv")
        relationships = csv_values(meta, "relationships_csv")
        qtypes = csv_values(meta, "supports_question_types_csv")
        facets = csv_values(meta, "facets_csv")
        keywords = csv_values(meta, "keywords_csv")
        if not (claim_types or predicates or objects or facets or keywords):
            frame = extract_claim_event_frame(source_text, is_query=False)
            claim_types = frame["claim_types"]
            predicates = frame["predicates"]
            objects = frame["objects"]
            time_expressions = frame["time_expressions"]
            resolved_dates = frame["resolved_dates"]
            places = frame["places"]
            emotions = frame["emotions"]
            relationships = frame["relationships"]
            qtypes = frame["supports_question_types"]
            facets = frame["facets"]
            keywords = frame["keywords"]

    for value in claim_types:
        add(counts, f"claim_type:{value}", 6.0 if is_query else 5.0)
    for value in qtypes:
        add(counts, f"question_type:{value}", 8.0 if is_query else 6.5)
    for value in subjects:
        add(counts, f"subject:{value}", 12.0)
        for part in value.split("_"):
            add(counts, f"subject_part:{part}", 3.0)
    for value in predicates:
        add(counts, f"predicate:{value}", 8.5)
    for value in objects:
        add(counts, f"object:{value}", 8.5)
        for part in value.split("_"):
            add(counts, f"object_part:{part}", 2.8)
    for value in time_expressions:
        add(counts, f"time_expr:{value}", 8.5)
    for value in resolved_dates:
        _add_date_atoms(counts, value, 10.0 if is_query else 8.0)
    for value in places:
        add(counts, f"place:{value}", 7.5)
    for value in emotions:
        add(counts, f"emotion:{value}", 6.5)
    for value in relationships:
        add(counts, f"relationship:{value}", 6.5)
    for value in facets:
        add(counts, f"facet:{value}", 5.0)
    for value in keywords:
        term = canonical_term(value)
        if term and term not in STOP and term not in CLAIM_EVENT_EXTRA_STOP:
            add(counts, f"kw:{term}", 3.0 if is_query else 2.4)
            add(counts, f"tok:{term}", 1.6)

    # Lexical chart: content tokens with digit/length boosting
    for tok in content_tokens(source_text):
        term = canonical_term(tok)
        if not term or term in STOP or term in CLAIM_EVENT_EXTRA_STOP:
            continue
        weight = 4.5 if any(ch.isdigit() for ch in term) else (2.7 if len(term) >= 9 else 2.0)
        add(counts, f"tok:{term}", weight)
        if term in ACTION_TERMS:
            add(counts, f"predicate:{term}", 2.0)

    # Evidence IDs (exact match boost)
    for ev in EVIDENCE_RE.findall(source_text):
        add(counts, f"evidence_id:{ev.lower()}", 10.0 if is_query else 1.2)

    return counts


def memory_atoms(
    text: str,
    meta: dict[str, Any] | None = None,
    doc_id: str = "",
    *,
    is_query: bool = False,
) -> dict[str, float]:
    """Build chart atoms for a memory document or query.

    For queries (is_query=True), this builds the consumer's chart — the
    semantic coordinates that define what distinctions the current task needs.
    For documents, it builds the memory's chart from stored text and metadata.

    The consumer-relative validity criterion from Thompson & Horowitz (2026,
    Definition 1) states that a memory is relevant only if it preserves the
    distinctions the consumer requires. The chart atoms operationalize this:
    Fisher-Rao distance measures how much the memory's distribution differs
    from the query's, and validity penalties enforce hard constraints
    (supersession, scope) on top of the geometric distance.
    """
    meta = meta or {}
    counts = claim_event_atoms(text, meta, doc_id, is_query=is_query)

    # ID and metadata atoms for documents
    if doc_id:
        add(counts, f"id:{doc_id.lower()}", 10.0)
    for key in ("evidence_id", "kind", "target"):
        value = meta.get(key)
        if value:
            add(counts, f"{key}:{str(value).lower()}", 6.0 if key == "evidence_id" else 2.0)

    status = str(meta.get("status") or "").lower()
    if status:
        add(counts, f"status:{status}", 6.0)

    return counts


# ---------------------------------------------------------------------------
# Validity penalties (declarative constraints on the Fisher-Rao distance)
# ---------------------------------------------------------------------------

def _prefix(atoms: dict[str, float], name: str) -> set[str]:
    """Return all atom keys that start with the given prefix."""
    return {a for a in atoms if a.startswith(name)}


def validity_penalty(
    query_atoms: dict[str, float],
    doc_atoms: dict[str, float],
    meta: dict[str, Any],
) -> float:
    """Compute validity penalties layered on top of Fisher-Rao distance.

    These are declarative constraints, not learned weights. They enforce:

    - **Status gates**: active memories are boosted, superseded ones
      quarantined, decoys penalized.
    - **Consumer-relative scope**: if the query asks about a specific claim
      type (e.g., "preference"), memories with matching claim types are
      rewarded and non-matching ones penalized.
    - **Temporal alignment**: date overlap between query and memory.
    - **Keyword relevance**: shared content terms boost score.

    The penalty is added to the Fisher-Rao distance: lower is better.
    """
    penalty = 0.0
    status = str(meta.get("status") or "").lower()
    kind = str(meta.get("kind") or meta.get("memory_kind") or "").lower()

    if status == "active":
        penalty -= 0.05
    elif status == "superseded":
        penalty += 0.75
    elif "decoy" in status or "decoy" in kind:
        penalty += 0.30

    # Consumer-relative scope matching: reward overlap for each typed group
    for pfx, reward, miss in [
        ("claim_type:", 0.18, 0.50),
        ("scope:", 0.05, 0.10),
        ("question_type:", 0.10, 0.03),
        ("subject:", 0.24, 0.16),
        ("predicate:", 0.16, 0.04),
        ("object:", 0.18, 0.035),
    ]:
        q = _prefix(query_atoms, pfx)
        if not q:
            continue
        d = _prefix(doc_atoms, pfx)
        if not d:
            penalty += miss
        else:
            overlap = len(q & d)
            if overlap:
                penalty -= min(reward, reward * max(1, overlap) / max(1, len(q)))
            else:
                penalty += miss

    # Temporal alignment
    q_dates = _prefix(query_atoms, "resolved_date:")
    q_months = _prefix(query_atoms, "resolved_month:")
    q_years = _prefix(query_atoms, "resolved_year:")
    if q_dates or q_months or q_years:
        d_dates = _prefix(doc_atoms, "resolved_date:")
        d_months = _prefix(doc_atoms, "resolved_month:")
        d_years = _prefix(doc_atoms, "resolved_year:")
        if q_dates & d_dates:
            penalty -= 0.26
        elif q_months & d_months:
            penalty -= 0.16
        elif q_years & d_years:
            penalty -= 0.08
        else:
            penalty += 0.18

    # Keyword relevance
    q_terms = _prefix(query_atoms, "kw:") | _prefix(query_atoms, "tok:")
    d_terms = _prefix(doc_atoms, "kw:") | _prefix(doc_atoms, "tok:")
    if q_terms and d_terms:
        penalty -= min(0.22, 0.012 * len(q_terms & d_terms))

    return penalty


# ---------------------------------------------------------------------------
# Reranking engine
# ---------------------------------------------------------------------------

@dataclass
class FIRank:
    """Reranking result for a single candidate row."""
    index: int
    row: dict[str, Any]
    distance: float
    penalty: float
    score: float
    atom_count: int


def _idf_scale(
    atoms: dict[str, float], idf: dict[str, float], default_idf: float
) -> dict[str, float]:
    """Apply inverse-document-frequency scaling to atom weights."""
    return {atom: value * idf.get(atom, default_idf) for atom, value in atoms.items()}


def rerank_rows(
    query: str,
    rows: list[dict[str, Any]],
    *,
    score_weight: float = 0.35,
    max_candidates: int = 80,
    annotate: bool = True,
) -> list[dict[str, Any]]:
    """Rerank candidate rows using Fisher-Rao pullback geometry.

    This is the main entry point for the FI reranker. It takes a query string
    and a list of candidate rows (typically from a vector database) and
    reranks them by Fisher-Rao distance on the categorical probability
    simplex, with declarative validity penalties.

    The reranking is a bounded post-processing step on existing vector
    candidates — it does not replace vector candidate generation. The
    ``score_weight`` parameter controls the blend: 0.0 means pure original
    order, 1.0 means pure FI order, 0.35 (default) is a moderate blend.

    Args:
        query: The query string.
        rows: Candidate rows, each a dict with keys like ``content`` (or
            ``text``), ``metadata``, ``id``, ``composite_score``.
        score_weight: Blend weight for FI rank vs original rank (0.0–1.0).
        max_candidates: Maximum number of top candidates to rerank.
        annotate: If True, annotate each row with ``fi_score``,
            ``fi_distance``, ``fi_penalty`` fields.

    Returns:
        Reranked list of rows (same dicts, possibly annotated).
    """
    if not query or not rows:
        return rows

    candidates = list(rows[: max(1, int(max_candidates))])
    tail = list(rows[len(candidates):])

    # Build document atom charts and compute IDF across the candidate set
    raw_docs: list[tuple[int, dict[str, Any], dict[str, float]]] = []
    df: collections.Counter[str] = collections.Counter()
    for idx, row in enumerate(candidates):
        meta = dict(row.get("metadata") or {})
        text = str(row.get("content") or row.get("text") or "")
        doc_id = str(row.get("id") or "")
        atoms = memory_atoms(text, meta, doc_id, is_query=False)
        raw_docs.append((idx, row, atoms))
        df.update(atoms.keys())

    n = max(1, len(raw_docs))
    idf = {atom: math.log((n + 1.0) / (freq + 0.5)) + 1.0 for atom, freq in df.items()}
    default_idf = math.log((n + 1.0) / 0.5) + 1.0

    # Build query atom chart
    q_atoms_raw = memory_atoms(query, {"status": "query"}, "query", is_query=True)
    q_atoms = _idf_scale(q_atoms_raw, idf, default_idf)
    q_prob = normalize(q_atoms)

    # Compute Fisher-Rao distance + validity penalty for each candidate
    ranks: list[FIRank] = []
    for idx, row, atoms_raw in raw_docs:
        atoms = _idf_scale(atoms_raw, idf, default_idf)
        dist = fisher_rao_distance(q_prob, normalize(atoms))
        meta = dict(row.get("metadata") or {})
        penalty = validity_penalty(q_atoms, atoms, meta)
        fi_score = -(dist + penalty)
        ranks.append(FIRank(idx, row, dist, penalty, fi_score, len(atoms)))

    # FI-only ordering
    fi_order = {
        rank.index: pos
        for pos, rank in enumerate(
            sorted(ranks, key=lambda r: (-r.score, str(r.row.get("id") or "")))
        )
    }

    # Blend FI rank with original Chroma rank
    denom = max(1, len(ranks) - 1)
    w = min(1.0, max(0.0, float(score_weight)))
    weighted: list[tuple[float, float, str, FIRank]] = []
    for rank in ranks:
        base_rank_score = 1.0 - (rank.index / denom)
        fi_rank_score = 1.0 - (fi_order[rank.index] / denom)
        combined = (1.0 - w) * base_rank_score + w * fi_rank_score
        weighted.append((combined, rank.score, str(rank.row.get("id") or ""), rank))

    # Build output
    out: list[dict[str, Any]] = []
    for combined, _fi_score, _doc_id, rank in sorted(
        weighted, key=lambda item: (-item[0], -item[1], item[2])
    ):
        row = dict(rank.row)
        if annotate:
            row["fi_score"] = rank.score
            row["fi_distance"] = rank.distance
            row["fi_penalty"] = rank.penalty
            row["fi_combined_score"] = combined
            row["fi_atom_count"] = rank.atom_count
            if "composite_score" in row:
                row["pre_fi_composite_score"] = row.get("composite_score")
            row["composite_score"] = combined
        out.append(row)
    out.extend(tail)
    return out
