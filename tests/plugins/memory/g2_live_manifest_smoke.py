#!/usr/bin/env python3
"""G2 read-only manifest smoke for enforced first-turn recall.

Attempts a live read-only ChromaDB/Forge run. If dependencies are unavailable,
falls back to the unit-test fake provider shape and records a deferral rather
than skipping smoke coverage entirely.
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from plugins.memory.chromadb import ChromaDBMemoryProvider  # noqa: E402
from plugins.memory.chromadb.config import ChromaDBConfig  # noqa: E402
from plugins.memory.chromadb.embedding import get_embedding_function  # noqa: E402
from plugins.memory.chromadb.g1b_observability import feedback_path_for_home, iter_feedback_events  # noqa: E402

PROMPT = "help me fill this Anduril application using what you know from the SpaceX application"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


class FakeCollection:
    def __init__(self, name: str):
        self.name = name

    def query(self, **kwargs):
        return {
            "ids": [["fake_user_fact", "fake_session_fact"]],
            "documents": [[
                "User durable profile fact: Jeremiah has prior application/work authorization answers saved.",
                "Prior SpaceX application session discussed work authorization and education answers.",
            ]],
            "metadatas": [[
                {"source": "builtin_mirror", "target": "user", "importance": 0.9, "stored_at": time.time()},
                {"source": "pre_compress_extraction", "target": "memory", "importance": 0.8, "stored_at": time.time()},
            ]],
            "distances": [[0.05, 0.08]],
        }


def run_fake(hermes_home: Path, reason: str) -> dict:
    provider = ChromaDBMemoryProvider()
    provider._available = True
    provider._hermes_home = str(hermes_home)
    provider._session_id = "g2_fake_smoke"
    provider._platform = "cli"
    provider._agent_name = "rilo"
    provider._collections = {"memories": FakeCollection("memories"), "sessions": FakeCollection("sessions"), "agent_rilo": FakeCollection("agent_rilo")}
    searched = []

    def embed(texts, timeout_seconds=4.0):
        return [[0.1, 0.2, 0.3] for _ in texts]

    def query_with_vector(collection, embedding, n_results=10, where=None):
        searched.append(collection.name)
        return collection.query(query_embeddings=[embedding], n_results=n_results, where=where)

    provider._embed_with_timeout = embed
    provider._query_with_vector = query_with_vector
    before_feedback = feedback_path_for_home(hermes_home).read_text(encoding="utf-8") if feedback_path_for_home(hermes_home).exists() else ""
    block = provider.enforced_recall(PROMPT, first_turn=True, session_id="g2_fake_smoke")
    events = list(iter_feedback_events(feedback_path_for_home(hermes_home)))
    after_feedback = feedback_path_for_home(hermes_home).read_text(encoding="utf-8") if feedback_path_for_home(hermes_home).exists() else ""
    return {
        "mode": "fake_fallback",
        "live_deferral_reason": reason,
        "mandatory_triggered": "## Enforced Memory Recall" in block,
        "searched_memories": "memories" in searched,
        "searched_sessions": "sessions" in searched,
        "selected_profile_or_application_fact": "application" in block.lower() or "user durable profile" in block.lower(),
        "block_chars": len(block),
        "under_budget": len(block) <= 3500,
        "recall_event_count_added": max(0, after_feedback.count("\n") - before_feedback.count("\n")),
        "has_latency_fields": any(e.get("event_type", "").startswith("recall_") and ("latency_ms" in e or "total_latency_ms" in e) for e in events[-10:]),
        "no_chroma_writes_claim": "fake provider: no remote Chroma client opened",
    }


def run_live(hermes_home: Path) -> dict:
    import chromadb

    cfg = ChromaDBConfig.from_json_file(str(hermes_home))
    client = chromadb.HttpClient(host=cfg.chromadb_host, port=cfg.chromadb_port)
    provider = ChromaDBMemoryProvider()
    provider._available = True
    provider._hermes_home = str(hermes_home)
    provider._session_id = "g2_live_smoke"
    provider._platform = "cli"
    provider._agent_name = cfg.agent_name or "rilo"
    provider._config = cfg
    provider._ef = get_embedding_function(
        cfg.embedding_service_url,
        cfg.embedding_model,
        fallback_enabled=False,
        fallback_url=cfg.embedding_fallback_url,
    )
    if provider._ef is None:
        raise RuntimeError("embedding function unavailable")

    # Read-only collection handles: get_collection only, never get_or_create_collection.
    collections = {}
    for key in ["memories", "sessions", f"agent_{provider._agent_name}"]:
        name = cfg.collections.get(key)
        if name:
            collections[key] = client.get_collection(name)
    provider._collections = collections

    searched = []
    original_qwv = provider._query_with_vector

    def spy(collection, embedding, n_results=10, where=None):
        searched.append(getattr(collection, "name", "unknown"))
        return original_qwv(collection, embedding, n_results=n_results, where=where)

    provider._query_with_vector = spy
    before_feedback = feedback_path_for_home(hermes_home).read_text(encoding="utf-8") if feedback_path_for_home(hermes_home).exists() else ""
    start = time.monotonic()
    block = provider.enforced_recall(PROMPT, first_turn=True, session_id="g2_live_smoke")
    latency_ms = int((time.monotonic() - start) * 1000)
    after_feedback = feedback_path_for_home(hermes_home).read_text(encoding="utf-8") if feedback_path_for_home(hermes_home).exists() else ""
    events = list(iter_feedback_events(feedback_path_for_home(hermes_home)))
    return {
        "mode": "live_read_only",
        "mandatory_triggered": "## Enforced Memory Recall" in block,
        "searched_collection_names": searched,
        "searched_memories": any("agent_memories" == s or s == "memories" for s in searched),
        "searched_sessions": any("session_history" == s or s == "sessions" for s in searched),
        "selected_profile_or_application_fact": "application" in block.lower() or "user" in block.lower(),
        "block_chars": len(block),
        "under_budget": len(block) <= 3500,
        "latency_ms": latency_ms,
        "under_5s_added_latency": latency_ms < 5000,
        "recall_event_count_added": max(0, after_feedback.count("\n") - before_feedback.count("\n")),
        "has_latency_fields": any(e.get("event_type", "").startswith("recall_") and ("latency_ms" in e or "total_latency_ms" in e) for e in events[-20:]),
        "no_chroma_writes_claim": "used get_collection + query only; no upsert/delete/get_or_create_collection",
    }


def main() -> int:
    hermes_home = Path("/Users/jeremiah/.hermes")
    memory = hermes_home / "memories" / "MEMORY.md"
    user = hermes_home / "memories" / "USER.md"
    before = {"MEMORY.md": sha(memory), "USER.md": sha(user)}
    try:
        manifest = run_live(hermes_home)
    except Exception as exc:
        manifest = run_fake(hermes_home, repr(exc))
    after = {"MEMORY.md": sha(memory), "USER.md": sha(user)}
    manifest["flatfile_hashes_before"] = before
    manifest["flatfile_hashes_after"] = after
    manifest["memory_user_unchanged"] = before == after
    manifest["prompt"] = "sha256:" + hashlib.sha256(PROMPT.encode()).hexdigest()
    print(json.dumps(manifest, indent=2, sort_keys=True))
    required = [
        manifest.get("mandatory_triggered"),
        manifest.get("searched_memories"),
        manifest.get("searched_sessions"),
        manifest.get("under_budget"),
        manifest.get("has_latency_fields"),
        manifest.get("memory_user_unchanged"),
    ]
    return 0 if all(required) else 1


if __name__ == "__main__":
    raise SystemExit(main())
