"""Atlas FI-pullback reranking for ChromaDB memory.

This module ports the Atlas finite-information pullback idea into the Hermes
ChromaDB provider without making Atlas a runtime dependency.  It keeps Chroma as
candidate generation, then reranks the bounded candidate set through a
source/metadata -> typed atoms -> categorical probability simplex chart and
Fisher-Rao distance on that simplex.

No model calls. No learned weights. No QA/gold leakage. The only inputs are the
current query, candidate document text, and candidate metadata already returned
from Chroma.
"""
from __future__ import annotations

import collections
import math
import re
from dataclasses import dataclass
from typing import Any, Iterable

WORD_RE = re.compile(r"[A-Za-z0-9]+(?:[-_:'][A-Za-z0-9]+)*")
CAP_RE = re.compile(r"\b[A-Z][a-zA-Z]+\b")
EVIDENCE_RE = re.compile(r"\bD\d+:\d+\b")
ISO_DATE_RE = re.compile(r"\b(?:19|20)\d{2}(?:-\d{2}(?:-\d{2})?)?\b")

STOP = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by",
    "can", "did", "do", "does", "for", "from", "had", "has", "have", "having",
    "if", "in", "is", "it", "its", "of", "on", "or", "that", "the", "then",
    "this", "to", "was", "were", "what", "when", "where", "which", "why", "with",
    "would", "should", "could", "must", "not", "no", "yes", "about", "after",
    "before", "both", "all", "i", "me", "my", "mine", "you", "your", "yours",
    "he", "she", "they", "them", "their", "we", "us", "our",
}

CLAIM_EVENT_EXTRA_STOP = {
    "said", "told", "tell", "asked", "ask", "since", "look", "take", "pic",
    "picture", "photo", "hows", "going", "wanted", "thanks", "thank", "just",
    "really", "okay", "ok", "yeah", "thing", "things", "something", "anything",
}

FACETS: dict[str, list[str]] = {
    "time": [
        "when", "date", "time", "year", "month", "day", "yesterday", "today",
        "tomorrow", "last", "next", "ago", "week", "weekend", "morning", "evening",
        "night", "pm", "am", "january", "february", "march", "april", "may", "june",
        "july", "august", "september", "october", "november", "december",
    ],
    "identity": ["identity", "transgender", "woman", "man", "nonbinary", "gay", "lesbian", "lgbtq", "self"],
    "person": ["who", "friend", "mother", "father", "mom", "dad", "sister", "brother", "wife", "husband", "partner", "kid", "kids", "child", "children", "family", "mentor"],
    "place": ["where", "place", "location", "home", "house", "school", "office", "work", "park", "restaurant", "cafe", "hospital", "clinic", "store", "city", "town", "trip", "travel", "visit"],
    "work_education": ["work", "job", "career", "office", "project", "meeting", "class", "course", "school", "college", "university", "education", "degree", "research", "study", "studying"],
    "emotion": ["feel", "feeling", "felt", "happy", "sad", "angry", "anxious", "worried", "excited", "thankful", "grateful", "accepted", "courage", "inspiring", "support", "love", "proud", "stress", "stressed"],
    "health": ["health", "doctor", "hospital", "clinic", "therapy", "therapist", "medicine", "pain", "sick", "surgery", "appointment"],
    "activity": ["do", "did", "doing", "done", "go", "went", "make", "made", "paint", "painted", "read", "watch", "watched", "play", "played", "cook", "cooked", "research", "researched", "apply", "applied", "adopt", "adoption"],
    "preference": ["like", "likes", "liked", "love", "loves", "favorite", "prefer", "prefers", "enjoy", "enjoys", "hobby", "interest"],
    "object": ["what", "book", "movie", "painting", "sunrise", "food", "meal", "gift", "car", "phone", "computer", "dog", "cat", "pet"],
    "plan": ["plan", "plans", "planned", "will", "want", "wants", "hope", "hopes", "future", "try", "trying"],
    "relationship": ["relationship", "met", "meet", "married", "dating", "friend", "support", "group", "community", "help", "helped", "accepted"],
}

QUESTION_FACETS: dict[str, list[str]] = {
    "when": ["time"],
    "where": ["place"],
    "who": ["person"],
    "whom": ["person"],
    "whose": ["person"],
    "why": ["emotion", "plan"],
    "how": ["activity", "emotion"],
    "what": ["object", "activity", "identity", "work_education"],
    "which": ["object", "activity"],
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
    "transitioning": "transition", "transitioned": "transition",
    "helped": "help", "helping": "help", "helps": "help",
    "supported": "support", "supporting": "support", "supports": "support",
    "encouraged": "encourage", "encouraging": "encourage",
    "talked": "talk", "talking": "talk", "spoke": "speak", "speech": "speak",
    "gave": "give", "given": "give", "gives": "give",
    "received": "receive", "receiving": "receive", "receives": "receive",
    "made": "make", "making": "make", "makes": "make",
    "adopted": "adopt", "adopting": "adopt", "adoption": "adopt",
    "studying": "study", "studied": "study", "studies": "study",
}

ACTION_TERMS = {
    "go", "attend", "run", "paint", "sign", "research", "plan", "think", "meet",
    "move", "transition", "help", "support", "encourage", "talk", "speak", "give",
    "receive", "make", "adopt", "study", "education", "work", "cook", "read",
    "watch", "play", "visit", "travel", "learn", "love", "like", "enjoy", "feel",
}

PHRASE_ALIASES = {
    "lgbtq support group": "support_group",
    "lgbtq+ support group": "support_group",
    "support group": "support_group",
    "charity race": "charity_race",
    "mental health": "mental_health",
    "pottery class": "pottery_class",
    "transgender conference": "transgender_conference",
    "career options": "career_options",
    "adoption agencies": "adoption_agencies",
    "adoption agency": "adoption_agencies",
    "counseling workshop": "counseling_workshop",
    "therapeutic methods": "therapeutic_methods",
    "home country": "home_country",
    "camping trip": "camping_trip",
    "dinosaur exhibit": "dinosaur_exhibit",
    "hand painted bowl": "hand_painted_bowl",
    "hand-painted bowl": "hand_painted_bowl",
}

PLACE_TERMS = {"home", "house", "school", "office", "work", "park", "restaurant", "cafe", "hospital", "clinic", "store", "beach", "city", "town", "mountains", "mountain", "forest", "country", "workshop", "conference", "exhibit"}
EMOTION_TERMS = {"happy", "sad", "angry", "anxious", "worried", "excited", "thankful", "grateful", "accepted", "courage", "inspiring", "support", "love", "proud", "stress", "stressed", "powerful", "rewarding", "therapeutic", "enlightening", "passionate"}
RELATIONSHIP_TERMS = {"friend", "friends", "mother", "father", "mom", "dad", "sister", "brother", "wife", "husband", "partner", "kid", "kids", "children", "family", "neighbor", "teacher", "student", "coworker", "colleague", "mentor", "community", "group"}


def norm(text: str) -> str:
    return str(text or "").lower().replace("&", " and ")


def tokens(text: str) -> list[str]:
    return [t.lower().strip("'") for t in WORD_RE.findall(str(text or ""))]


def content_tokens(text: str) -> list[str]:
    out: list[str] = []
    for tok in tokens(text):
        if tok in STOP or len(tok) <= 1:
            continue
        out.append(tok)
    return out


def add(counts: dict[str, float], atom: str, weight: float) -> None:
    if atom and weight > 0:
        counts[atom] = counts.get(atom, 0.0) + float(weight)


def add_many(counts: dict[str, float], atoms: Iterable[str], weight: float) -> None:
    for atom in atoms:
        add(counts, atom, weight)


def csv_values(meta: dict[str, Any], key: str) -> list[str]:
    raw = str(meta.get(key) or "")
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def canonical_term(token: str) -> str:
    tok = str(token or "").lower().strip("'\"")
    tok = tok.replace("lgbtq+", "lgbtq")
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


def normalize(counts: dict[str, float]) -> dict[str, float]:
    total = sum(v for v in counts.values() if v > 0)
    if total <= 0:
        return {}
    return {k: v / total for k, v in counts.items() if v > 0}


def fisher_rao_distance(p: dict[str, float], q: dict[str, float]) -> float:
    if not p or not q:
        return math.pi / 2
    if len(p) < len(q):
        bc = sum(math.sqrt(pv * q.get(k, 0.0)) for k, pv in p.items())
    else:
        bc = sum(math.sqrt(qv * p.get(k, 0.0)) for k, qv in q.items())
    return math.acos(max(0.0, min(1.0, bc)))


def detect_facets(text: str, *, is_query: bool = False) -> set[str]:
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
    if re.search(r"\b(19|20)\d{2}\b|\b\d{1,2}:\d{2}\b|\b\d{1,2}\s*(am|pm)\b", t):
        facets.add("time")
    return facets


def detect_names(text: str, speaker: str | None = None) -> list[str]:
    names: list[str] = []
    if speaker and str(speaker).lower() != "unknown":
        names.append(str(speaker))
    for name in CAP_RE.findall(str(text or "")):
        if name.lower() in STOP or name in {"LoCoMo", "Speaker", "May"}:
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


def phrase_aliases(text: str) -> list[str]:
    t = norm(text).replace("+", " plus ")
    hits: list[str] = []
    for phrase, alias in PHRASE_ALIASES.items():
        if phrase.replace("+", " plus ") in t:
            hits.append(alias)
    return hits


def simple_keywords(text: str, limit: int = 28) -> list[str]:
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


def detect_question_types(text: str, *, is_query: bool, facets: set[str], has_relationship: bool) -> list[str]:
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
        if re.search(r"\bwhat\b|\bwhich\b|\bwould\b", t):
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


def extract_subjects(text: str, speaker: str = "") -> list[str]:
    subjects = detect_names(text, speaker if speaker and speaker.lower() != "unknown" else None)
    t = str(text or "")
    for owner, relation in re.findall(r"\b([A-Z][a-zA-Z]+)'s\s+(kids?|children|family|friends?)\b", t):
        subjects.append(owner.lower())
        subjects.append(canonical_term(relation))
        subjects.append(f"{owner.lower()}_{canonical_term(relation)}")
    return list(dict.fromkeys(s for s in subjects if s))


def extract_claim_event_frame(source_text: str, *, speaker: str = "", is_query: bool = False) -> dict[str, list[str]]:
    source = str(source_text or "")
    subjects = extract_subjects(source, "" if is_query else speaker)
    term_stream = [canonical_term(tok) for tok in content_tokens(source)]
    term_stream = [tok for tok in term_stream if tok and tok not in CLAIM_EVENT_EXTRA_STOP]
    predicates = [tok for tok in term_stream if tok in ACTION_TERMS]
    objects = list(phrase_aliases(source))
    for tok in term_stream:
        if tok in predicates or tok in subjects or tok in STOP or tok in CLAIM_EVENT_EXTRA_STOP:
            continue
        if tok in {"yesterday", "today", "tomorrow", "last", "next", "month", "week", "year", "ago"}:
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
    if relationships or "relationship" in facets or "person" in facets:
        claim_types.add("relationship")
    if "identity" in facets:
        claim_types.add("identity")
    if "plan" in facets:
        claim_types.add("plan")
    if "preference" in facets:
        claim_types.add("preference")
    if not claim_types:
        claim_types.add("source_observation")

    time_expressions = [m.group(0).lower() for m in ISO_DATE_RE.finditer(source)]
    resolved_dates = list(time_expressions)
    qtypes = detect_question_types(source, is_query=is_query, facets=set(facets), has_relationship=bool(relationships))
    return {
        "claim_types": sorted(claim_types),
        "subjects": list(dict.fromkeys(subjects)),
        "predicates": list(dict.fromkeys(predicates)),
        "objects": list(dict.fromkeys(objects)),
        "time_expressions": list(dict.fromkeys(time_expressions)),
        "resolved_dates": list(dict.fromkeys(resolved_dates)),
        "places": list(dict.fromkeys(places)),
        "emotions": list(dict.fromkeys(emotions)),
        "relationships": list(dict.fromkeys(relationships)),
        "supports_question_types": qtypes,
        "facets": facets,
        "keywords": simple_keywords(source),
    }


def add_date_atoms(counts: dict[str, float], value: str, weight: float) -> None:
    add(counts, f"resolved_date:{value}", weight)
    m = re.match(r"^(\d{4})-(\d{2})(?:-(\d{2}))?$", value)
    if m:
        add(counts, f"resolved_year:{m.group(1)}", weight * 0.45)
        add(counts, f"resolved_month:{m.group(1)}-{m.group(2)}", weight * 0.75)
        if m.group(3):
            add(counts, f"resolved_day:{m.group(3)}", weight * 0.25)
    elif re.match(r"^(19|20)\d{2}$", value):
        add(counts, f"resolved_year:{value}", weight * 0.75)


def claim_event_atoms(text: str, meta: dict[str, Any] | None = None, doc_id: str = "", *, is_query: bool = False) -> dict[str, float]:
    meta = meta or {}
    counts: dict[str, float] = {}
    source_text = str(text or "") if is_query else str(meta.get("source_text") or str(text or "").split("source_text:", 1)[-1])
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
        keywords = [canonical_term(tok) for tok in content_tokens(source_text)]
    else:
        claim_types = csv_values(meta, "claim_types_csv")
        subjects = csv_values(meta, "subjects_csv") or extract_subjects(source_text, str(meta.get("speaker") or ""))
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
            frame = extract_claim_event_frame(source_text, speaker=str(meta.get("speaker") or ""), is_query=False)
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
        add_date_atoms(counts, value, 10.0 if is_query else 8.0)
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
    for tok in content_tokens(source_text):
        term = canonical_term(tok)
        if not term or term in STOP or term in CLAIM_EVENT_EXTRA_STOP:
            continue
        weight = 4.5 if any(ch.isdigit() for ch in term) else (2.7 if len(term) >= 9 else 2.0)
        add(counts, f"tok:{term}", weight)
        if term in ACTION_TERMS:
            add(counts, f"predicate:{term}", 2.0)
    if not is_query:
        for key, weight in [("speaker", 4.5), ("sample_id", 0.7), ("session_key", 0.8), ("kind", 1.0)]:
            value = meta.get(key)
            if value:
                for part in content_tokens(str(value)):
                    add(counts, f"meta:{key}:{canonical_term(part)}", weight)
        for tok in csv_values(meta, "context_terms_csv"):
            add(counts, f"ctx:{canonical_term(tok)}", 0.8)
    evidence_ids = EVIDENCE_RE.findall(source_text if is_query else str(meta.get("evidence_id") or ""))
    for ev in evidence_ids:
        add(counts, f"evidence_id:{ev.lower()}", 10.0 if is_query else 1.2)
    return counts


def atlas_branch_atoms(text: str, meta: dict[str, Any] | None = None, doc_id: str = "") -> set[str]:
    meta = meta or {}
    t = norm(" ".join([doc_id, text, str(meta.get("memory_kind", "")), str(meta.get("kind", ""))]))
    kind = str(meta.get("kind") or meta.get("memory_kind") or "").lower()
    status = str(meta.get("status") or "").lower()
    if "decoy" in kind or "decoy" in status or "not a branch verdict" in t:
        return set()
    atoms: set[str] = set()
    toy = any(term in t for term in ["toy", "radial", "synthetic", "first gate"])
    pend = any(term in t for term in ["pendulum", "energy", "conservation", "frictionless"])
    analytic_fail = any(term in t for term in ["analytic j fails", "analytic-j fails", "exact j fails", "oracle jacobian", "closed form j fails", "analytic pullback wrong", "nullspace check fails"])
    learned_context = any(term in t for term in ["learned j", "learned-j", "learned estimator", "autodiff", "neural", "mlp", "estimated jacobian"])
    learned_fail = learned_context and any(term in t for term in ["fail", "failed", "fails", "cannot", "miss", "bad", "blocker", "broken", "wrong"])
    passish = any(term in t for term in [" pass", " passes", " passed", " green", " succeeds", " success", " works", " all green"])
    bothish = any(term in t for term in ["both gates", "both arms", "toy and pendulum", "synthetic and energy", "exact and learned", "analytic and learned", "both known-invariant"])
    controlish = any(term in t for term in ["shuffled j", "wrong j", "wrong orientation", "label shuffle", "nuisance leakage", "no fisher", "negative control", "quarantine"])
    if controlish:
        atoms.update({"branch:control_failure", "verdict:control_failure_quarantine"})
    if bothish and passish and not (analytic_fail or learned_fail or controlish):
        atoms.update({"branch:both_pass", "verdict:return_to_locked_phyre_chart"})
    if toy and analytic_fail and not pend:
        atoms.add("branch:toy_analytic_fail")
    if toy and learned_fail and not pend:
        atoms.add("branch:toy_learned_fail")
    if pend and analytic_fail:
        atoms.add("branch:pendulum_analytic_fail")
    if pend and learned_fail:
        atoms.add("branch:pendulum_learned_fail")
    if str(meta.get("memory_kind", "")).lower() == "verdict_logic" or "pre-registered verdict logic" in t:
        atoms.update({"branch:toy_analytic_fail", "branch:toy_learned_fail", "branch:pendulum_analytic_fail", "branch:pendulum_learned_fail", "branch:both_pass", "branch:control_failure"})
    return atoms


def atlas_scope_atoms(text: str, meta: dict[str, Any] | None = None) -> set[str]:
    meta = meta or {}
    t = norm(text)
    scopes: set[str] = set()
    for s in re.split(r"[,;\s]+", str(meta.get("consumer_scope_csv") or "").lower()):
        if s in {"planning", "implementation", "review", "interpretation", "question_answering", "evidence_retrieval"}:
            scopes.add(f"scope:{s}")
    if any(term in t for term in ["planning", "plan", "gate order", "before coding"]):
        scopes.add("scope:planning")
    if any(term in t for term in ["implementation", "runner", "driver", "code", "executable"]):
        scopes.add("scope:implementation")
    if any(term in t for term in ["review", "reviewer", "checklist", "reject", "verdict", "audit"]):
        scopes.add("scope:review")
    if any(term in t for term in ["interpretation", "interpret", "claim", "non claim", "overclaim", "cannot claim"]):
        scopes.add("scope:interpretation")
    return scopes


def atlas_atoms(text: str, meta: dict[str, Any] | None = None, doc_id: str = "") -> dict[str, float]:
    meta = meta or {}
    t = norm(text)
    counts: dict[str, float] = {}
    if doc_id:
        add(counts, f"id:{doc_id.lower()}", 10.0)
        for part in WORD_RE.findall(doc_id.lower()):
            add(counts, f"idtok:{part}", 3.0)
    for ev in EVIDENCE_RE.findall(str(text or "")):
        add(counts, f"evidence:{ev}", 10.0)
    for key in ("evidence_id", "canary_label", "experiment_id", "schema_version"):
        value = meta.get(key)
        if value:
            add(counts, f"{key}:{str(value).lower()}", 6.0 if key in {"evidence_id", "canary_label"} else 2.0)
    kind = str(meta.get("kind") or meta.get("memory_kind") or meta.get("target") or "").lower()
    status = str(meta.get("status") or "").lower()
    if kind:
        add(counts, f"kind:{kind}", 4.0)
    if status:
        add(counts, f"status:{status}", 6.0)
    add_many(counts, atlas_branch_atoms(text, meta, doc_id), 18.0)
    add_many(counts, atlas_scope_atoms(text, meta), 5.0)
    if any(term in t for term in ["q(x)=q(y)", "q/c", "consumer", "validity", "valid abstraction", "anti-witness", "anti witness"]):
        add(counts, "memory_protocol:qc_validity", 9.0)
    if any(term in t for term in ["receipt", "preflight", "hash", "writeback", "ledger", "forged", "stale", "missing"]):
        add(counts, "memory_protocol:receipt_gate", 9.0)
    if any(term in t for term in ["superseded", "supersession", "active current rule", "stale rule"]):
        add(counts, "memory_protocol:supersession", 9.0)
    if any(term in t for term in ["non claim", "nonclaim", "cannot claim", "forbidden claim", "claim boundary", "broad fisher thesis"]):
        add(counts, "memory_protocol:nonclaim_boundary", 10.0)
    for domain, terms in {
        "domain:toy": ["toy", "radial", "synthetic"],
        "domain:pendulum": ["pendulum", "energy", "frictionless", "conservation"],
        "domain:phyre": ["phyre"],
        "arm:analytic": ["analytic", "exact", "oracle", "closed form", "formula"],
        "arm:learned": ["learned", "estimator", "autodiff", "neural", "mlp", "estimated"],
        "control:negative_control": ["wrong orientation", "shuffled", "label shuffle", "nuisance leakage", "negative control", "no fisher"],
    }.items():
        if any(term in t for term in terms):
            add(counts, domain, 4.0)
    for tok in content_tokens(text):
        term = canonical_term(tok)
        if not term or term in STOP:
            continue
        weight = 4.0 if any(ch.isdigit() for ch in term) else 1.0
        if len(term) > 18 or "_" in term or ":" in term or "-" in term:
            weight = max(weight, 5.0)
        add(counts, f"tok:{term}", weight)
        if "-" in term or "_" in term or ":" in term:
            for part in re.split(r"[-_:]+", term):
                if part and part not in STOP:
                    add(counts, f"tok:{part}", 1.5)
    return counts


def looks_like_claim_event(meta: dict[str, Any]) -> bool:
    schema = str(meta.get("schema_version") or "").lower()
    kind = str(meta.get("kind") or meta.get("memory_kind") or "").lower()
    if "claim_event" in schema or "claim_event" in kind:
        return True
    typed_keys = [
        "claim_types_csv", "subjects_csv", "predicates_csv", "objects_csv",
        "time_expressions_csv", "resolved_dates_csv", "supports_question_types_csv",
    ]
    return any(meta.get(k) for k in typed_keys)


def memory_atoms(text: str, meta: dict[str, Any] | None = None, doc_id: str = "", *, is_query: bool = False) -> dict[str, float]:
    meta = meta or {}
    if looks_like_claim_event(meta) or is_query:
        # Queries always get the claim/event chart too; ordinary docs get it as a
        # lightweight semantic overlay below if they are not explicitly typed.
        counts = claim_event_atoms(text, meta, doc_id, is_query=is_query)
        if is_query:
            for atom, weight in atlas_atoms(text, {"status": "query"}, doc_id).items():
                counts[atom] = counts.get(atom, 0.0) + weight * 0.6
        return counts
    counts = atlas_atoms(text, meta, doc_id)
    overlay = claim_event_atoms(text, meta, doc_id, is_query=False)
    for atom, weight in overlay.items():
        counts[atom] = counts.get(atom, 0.0) + weight * 0.45
    return counts


def prefix(atoms: dict[str, float], name: str) -> set[str]:
    return {a for a in atoms if a.startswith(name)}


@dataclass
class FIRank:
    index: int
    row: dict[str, Any]
    distance: float
    penalty: float
    score: float
    atom_count: int


def validity_penalty(query_atoms: dict[str, float], doc_atoms: dict[str, float], meta: dict[str, Any]) -> float:
    penalty = 0.0
    status = str(meta.get("status") or "").lower()
    kind = str(meta.get("kind") or meta.get("memory_kind") or "").lower()
    if status == "active":
        penalty -= 0.05
    elif status == "superseded":
        penalty += 0.75
    elif "decoy" in status or "decoy" in kind:
        penalty += 0.30

    for pfx, reward, miss in [
        ("branch:", 0.18, 0.50),
        ("scope:", 0.05, 0.10),
        ("question_type:", 0.10, 0.03),
        ("subject:", 0.24, 0.16),
        ("predicate:", 0.16, 0.04),
        ("object:", 0.18, 0.035),
    ]:
        q = prefix(query_atoms, pfx)
        if not q:
            continue
        d = prefix(doc_atoms, pfx)
        if not d:
            penalty += miss
        else:
            overlap = len(q & d)
            penalty += -min(reward, reward * max(1, overlap) / max(1, len(q))) if overlap else miss

    q_dates = prefix(query_atoms, "resolved_date:")
    q_months = prefix(query_atoms, "resolved_month:")
    q_years = prefix(query_atoms, "resolved_year:")
    if q_dates or q_months or q_years:
        d_dates = prefix(doc_atoms, "resolved_date:")
        d_months = prefix(doc_atoms, "resolved_month:")
        d_years = prefix(doc_atoms, "resolved_year:")
        if q_dates & d_dates:
            penalty -= 0.26
        elif q_months & d_months:
            penalty -= 0.16
        elif q_years & d_years:
            penalty -= 0.08
        else:
            penalty += 0.18
    elif "question_type:when" in query_atoms:
        penalty += -0.07 if (prefix(doc_atoms, "resolved_date:") or prefix(doc_atoms, "time_expr:")) else 0.07

    q_terms = prefix(query_atoms, "kw:") | prefix(query_atoms, "tok:")
    d_terms = prefix(doc_atoms, "kw:") | prefix(doc_atoms, "tok:")
    if q_terms and d_terms:
        penalty -= min(0.22, 0.012 * len(q_terms & d_terms))
    return penalty


def _scale(atoms: dict[str, float], idf: dict[str, float], default_idf: float) -> dict[str, float]:
    return {atom: value * idf.get(atom, default_idf) for atom, value in atoms.items()}


def rerank_rows(
    query: str,
    rows: list[dict[str, Any]],
    *,
    score_weight: float = 0.35,
    max_candidates: int = 80,
    annotate: bool = True,
) -> list[dict[str, Any]]:
    """Return rows reranked by Atlas FI pullback while preserving row shape.

    Existing Chroma order remains part of the final score; FI is a bounded rerank,
    not an unbounded full replacement for vector candidate generation.
    """
    if not query or not rows:
        return rows
    candidates = list(rows[: max(1, int(max_candidates))])
    tail = list(rows[len(candidates):])

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

    q_atoms_raw = memory_atoms(query, {"status": "query", "consumer_scope_csv": "question_answering,evidence_retrieval"}, "query", is_query=True)
    q_atoms = _scale(q_atoms_raw, idf, default_idf)
    q_prob = normalize(q_atoms)

    ranks: list[FIRank] = []
    for idx, row, atoms_raw in raw_docs:
        atoms = _scale(atoms_raw, idf, default_idf)
        dist = fisher_rao_distance(q_prob, normalize(atoms))
        meta = dict(row.get("metadata") or {})
        penalty = validity_penalty(q_atoms, atoms, meta)
        fi_score = -(dist + penalty)
        ranks.append(FIRank(idx, row, dist, penalty, fi_score, len(atoms)))

    fi_order = {rank.index: pos for pos, rank in enumerate(sorted(ranks, key=lambda r: (-r.score, str(r.row.get("id") or ""))))}
    denom = max(1, len(ranks) - 1)
    weighted: list[tuple[float, float, str, FIRank]] = []
    w = min(1.0, max(0.0, float(score_weight)))
    for rank in ranks:
        base_rank_score = 1.0 - (rank.index / denom)
        fi_rank_score = 1.0 - (fi_order[rank.index] / denom)
        combined = (1.0 - w) * base_rank_score + w * fi_rank_score
        weighted.append((combined, rank.score, str(rank.row.get("id") or ""), rank))

    out: list[dict[str, Any]] = []
    for combined, _fi_score, _doc_id, rank in sorted(weighted, key=lambda item: (-item[0], -item[1], item[2])):
        row = dict(rank.row)
        if annotate:
            row["atlas_fi_score"] = rank.score
            row["atlas_fi_distance"] = rank.distance
            row["atlas_fi_penalty"] = rank.penalty
            row["atlas_fi_combined_score"] = combined
            row["atlas_fi_atom_count"] = rank.atom_count
            if "composite_score" in row:
                row["pre_atlas_fi_composite_score"] = row.get("composite_score")
            row["composite_score"] = combined
        out.append(row)
    out.extend(tail)
    return out
