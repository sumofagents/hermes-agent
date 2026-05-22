import json
import time
from pathlib import Path

from agent.recall_gate import classify_risk
from plugins.memory.chromadb import ChromaDBMemoryProvider
from plugins.memory.chromadb.g1b_observability import iter_feedback_events
from plugins.memory.chromadb.g2_recall import (
    RecallCandidate,
    dedup_candidates,
    filter_ephemeral,
    render_recall_block,
    render_recall_block_with_candidates,
)


def _candidate(fid, content, *, collection="memories", source="builtin_mirror", target="user", durability="durable", score=0.9):
    return RecallCandidate(
        fact_id=fid,
        collection=collection,
        content=content,
        score=score,
        source=source,
        target=target,
        durability=durability,
        rank=1,
        query_index=0,
    )


def test_dedup_normalized_content_hash_collapses_identical_candidates():
    a = _candidate("a", "Jeremiah is authorized to work in the US.")
    b = _candidate("b", "  jeremiah is authorized to work in the us.  ")
    kept, dropped = dedup_candidates([a, b])
    assert [c.fact_id for c in kept] == ["a"]
    assert dropped == {"b": "duplicate"}


def test_ephemeral_candidates_excluded_unless_fleet_project():
    durable = _candidate("durable", "Long-term preference", durability="durable")
    ephemeral = _candidate("ephemeral", "PR #123 is half done", durability="ephemeral")
    kept, dropped = filter_ephemeral([durable, ephemeral], allow_ephemeral=False)
    assert [c.fact_id for c in kept] == ["durable"]
    assert dropped == {"ephemeral": "ephemeral"}

    kept, dropped = filter_ephemeral([durable, ephemeral], allow_ephemeral=True)
    assert {c.fact_id for c in kept} == {"durable", "ephemeral"}
    assert dropped == {}


def test_render_recall_block_has_required_shape_sources_and_budget():
    risk = classify_risk("same as before")
    block = render_recall_block([_candidate("abc", "User prefers concise technical answers.")], risk, char_budget=3500)
    assert block.startswith("## Enforced Memory Recall")
    assert "Reason: continuity; mandatory=true" in block
    assert "Instruction: Use this retrieved context" in block
    assert "- [memories:abc] score=0.900 source=builtin_mirror target=user durability=durable" in block
    assert "User prefers concise technical answers." in block
    assert len(block) <= 3500


def test_render_recall_block_trims_to_budget():
    risk = classify_risk("same as before")
    candidates = [_candidate(str(i), "x" * 1000, score=1.0 - i / 100) for i in range(10)]
    block = render_recall_block(candidates, risk, char_budget=3500)
    assert len(block) <= 3500
    assert "[memories:0]" in block


class FakeCollection:
    def __init__(self, name):
        self.name = name

    def query(self, **kwargs):
        return {
            "ids": [[f"{self.name}_1", f"{self.name}_dup", f"{self.name}_eph"]],
            "documents": [[
                "Jeremiah has prior SpaceX application work authorization answers.",
                "Jeremiah has prior SpaceX application work authorization answers.",
                "Temporary PR #123 status should not appear in job recall.",
            ]],
            "metadatas": [[
                {"source": "builtin_mirror", "target": "user", "importance": 0.9, "stored_at": 9999999999},
                {"source": "pre_compress_extraction", "target": "memory", "importance": 0.8, "stored_at": 9999999999},
                {"source": "session_turn", "target": "session_turn", "importance": 0.1, "stored_at": 9999999999},
            ]],
            "distances": [[0.05, 0.05, 0.01]],
        }


class FakeProvider(ChromaDBMemoryProvider):
    def __init__(self, tmp_path):
        super().__init__()
        self._available = True
        self._hermes_home = str(tmp_path)
        self._session_id = "sess-test"
        self._platform = "cli"
        self._agent_name = "rilo"
        self._collections = {
            "memories": FakeCollection("memories"),
            "sessions": FakeCollection("sessions"),
            "agent_rilo": FakeCollection("agent_rilo"),
            "team_knowledge": FakeCollection("team_knowledge"),
            "team_ops": FakeCollection("team_ops"),
        }
        self.searched = []

    def _embed_with_timeout(self, queries, timeout_seconds=4.0):
        return [[0.1, 0.2, 0.3] for _ in queries]

    def _query_with_vector(self, collection, embedding, n_results=10, where=None):
        self.searched.append(collection.name)
        return collection.query(query_embeddings=[embedding], n_results=n_results, where=where)


def test_render_recall_block_with_candidates_reports_only_injected_budget_survivors():
    risk = classify_risk("help me fill this job application")
    candidates = [
        _candidate("kept", "short durable fact", score=0.99),
        _candidate("dropped", "x" * 1200, score=0.98),
    ]

    block, injected = render_recall_block_with_candidates(candidates, risk, char_budget=420)

    assert "kept" in block
    assert "dropped" not in block
    assert [c.fact_id for c in injected] == ["kept"]


def test_enforced_recall_receipts_name_only_candidates_injected_after_budget_trim(tmp_path):
    class BudgetCollection:
        name = "memories"

        def query(self, **kwargs):
            ids = [f"budget_{i}" for i in range(8)]
            docs = ["short durable application fact"] + [f"long durable application fact {i} " + ("x" * 3000) for i in range(1, 8)]
            return {
                "ids": [ids],
                "documents": [docs],
                "metadatas": [[{"source": "builtin_mirror", "target": "user", "importance": 0.9, "stored_at": 9999999999} for _ in ids]],
                "distances": [[0.01 + i * 0.01 for i in range(8)]],
            }

    provider = FakeProvider(tmp_path)
    provider._collections = {"memories": BudgetCollection()}
    block = provider.enforced_recall("help me fill this job application", first_turn=True, session_id="sess-test")

    events = list(iter_feedback_events(Path(tmp_path) / "logs" / "memory_feedback.jsonl"))
    retrieved_ids = [e["fact_id"] for e in events if e["event_type"] == "recall_retrieved"]
    used = next(e for e in events if e["event_type"] == "recall_used")
    dropped = used.get("dropped_ids", {})

    assert retrieved_ids
    assert used["selected_ids"] == retrieved_ids
    assert any(reason == "over_budget" for reason in dropped.values())
    for fact_id in retrieved_ids:
        assert fact_id in block
    for fact_id, reason in dropped.items():
        if reason == "over_budget":
            assert fact_id not in retrieved_ids
            assert fact_id not in block


def test_enforced_recall_searches_memory_sessions_agent_and_writes_privacy_ledger(tmp_path):
    provider = FakeProvider(tmp_path)
    prompt = "help me fill this Anduril application using what you know from the SpaceX application"
    block = provider.enforced_recall(prompt, first_turn=True, session_id="sess-test")

    assert "## Enforced Memory Recall" in block
    assert "SpaceX application work authorization" in block
    assert "Temporary PR #123" not in block
    assert len(block) <= 3500
    assert {"memories", "sessions", "agent_rilo"}.issubset(set(provider.searched))

    events = list(iter_feedback_events(Path(tmp_path) / "logs" / "memory_feedback.jsonl"))
    event_types = [e["event_type"] for e in events]
    assert "recall_needed" in event_types
    assert "recall_retrieved" in event_types
    assert "recall_used" in event_types
    serialized = "\n".join(json.dumps(e, sort_keys=True) for e in events)
    assert prompt not in serialized
    assert "context_sha256" in serialized
    assert all("latency_ms" in e or e["event_type"] == "recall_retrieved" for e in events)


def test_enforced_recall_fail_open_chroma_unavailable_logs_skip_and_degraded_notice(tmp_path):
    provider = ChromaDBMemoryProvider()
    provider._available = False
    provider._hermes_home = str(tmp_path)
    provider._session_id = "sess-test"
    block = provider.enforced_recall("same as before", first_turn=True, session_id="sess-test")
    assert "## Enforced Memory Recall" in block
    assert "stored memory could not be reached" in block.lower()

    events = list(iter_feedback_events(Path(tmp_path) / "logs" / "memory_feedback.jsonl"))
    assert any(e["event_type"] == "recall_needed" for e in events)
    assert any(e["event_type"] == "recall_skipped" and e.get("skip_reason") == "chroma_unavailable" for e in events)


def test_enforced_recall_embed_failure_logs_skip_and_degraded_notice(tmp_path):
    provider = FakeProvider(tmp_path)

    def boom(*args, **kwargs):
        raise TimeoutError("embed timeout")

    provider._embed_with_timeout = boom
    block = provider.enforced_recall("same as before", first_turn=True, session_id="sess-test")
    assert "stored memory could not be reached" in block.lower()
    events = list(iter_feedback_events(Path(tmp_path) / "logs" / "memory_feedback.jsonl"))
    assert any(e["event_type"] == "recall_skipped" and e.get("skip_reason") in {"timeout", "embedding_unavailable"} for e in events)


def test_query_with_vector_timeout_caps_slow_collection_query():
    provider = ChromaDBMemoryProvider()

    def slow_query(collection, embedding, n_results=10, where=None):
        time.sleep(0.2)
        return {}

    provider._query_with_vector = slow_query
    start = time.monotonic()
    try:
        provider._query_with_vector_timeout(object(), [0.1], timeout_seconds=0.01)
        assert False, "expected timeout"
    except TimeoutError:
        pass
    assert time.monotonic() - start < 0.15


def test_enforced_recall_does_not_mutate_memory_or_user_files(tmp_path):
    mem_dir = Path(tmp_path) / "memories"
    mem_dir.mkdir()
    memory = mem_dir / "MEMORY.md"
    user = mem_dir / "USER.md"
    memory.write_text("memory before", encoding="utf-8")
    user.write_text("user before", encoding="utf-8")
    before = (memory.read_bytes(), user.read_bytes())

    provider = FakeProvider(tmp_path)
    provider.enforced_recall("same as before", first_turn=True, session_id="sess-test")

    assert (memory.read_bytes(), user.read_bytes()) == before
