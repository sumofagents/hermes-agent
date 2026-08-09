"""Tests for the Fisher-Rao pullback memory reranker.

These tests do NOT require ChromaDB, an embedding service, or any external
service. They construct candidate rows as plain dicts and test the reranker
directly.
"""

import math
import pytest

from plugins.memory.md_reranker import (
    fisher_rao_distance,
    normalize,
    rerank_rows,
    validity_penalty,
    memory_atoms,
    extract_claim_event_frame,
    claim_event_atoms,
)


# ---------------------------------------------------------------------------
# Geometric primitives
# ---------------------------------------------------------------------------

class TestFisherRaoDistance:
    def test_identical_distributions_zero(self):
        p = {"a": 0.5, "b": 0.5}
        assert fisher_rao_distance(p, p) == pytest.approx(0.0, abs=1e-9)

    def test_disjoint_distributions_max(self):
        p = {"a": 1.0}
        q = {"b": 1.0}
        assert fisher_rao_distance(p, q) == pytest.approx(math.pi / 2)

    def test_orthogonal_partial_overlap(self):
        p = {"a": 0.5, "b": 0.5}
        q = {"b": 0.5, "c": 0.5}
        # BC = sqrt(0.5*0.5) = 0.5; d = arccos(0.5) = pi/3
        assert fisher_rao_distance(p, q) == pytest.approx(math.pi / 3, abs=1e-6)

    def test_empty_distribution_max(self):
        assert fisher_rao_distance({}, {"a": 1.0}) == pytest.approx(math.pi / 2)

    def test_symmetric(self):
        p = {"a": 0.3, "b": 0.7}
        q = {"b": 0.4, "c": 0.6}
        assert fisher_rao_distance(p, q) == pytest.approx(fisher_rao_distance(q, p))


class TestNormalize:
    def test_sums_to_one(self):
        counts = {"a": 3.0, "b": 1.0}
        p = normalize(counts)
        assert sum(p.values()) == pytest.approx(1.0)
        assert p["a"] == pytest.approx(0.75)
        assert p["b"] == pytest.approx(0.25)

    def test_empty(self):
        assert normalize({}) == {}

    def test_negative_filtered(self):
        counts = {"a": 2.0, "b": -1.0, "c": 0.0}
        p = normalize(counts)
        assert "b" not in p
        assert "c" not in p
        assert p["a"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Paraphrase recall: same concept, different wording
# ---------------------------------------------------------------------------

class TestParaphraseRecall:
    """Verify that the reranker surfaces the right memory from paraphrased queries."""

    @pytest.fixture
    def gold_memories(self):
        return [
            {
                "id": "pref_concise",
                "content": "User prefers concise operational answers with directness.",
                "metadata": {"status": "active", "kind": "preference"},
            },
            {
                "id": "infra_db",
                "content": "The vector database runs on port 5432 with the embedding service on port 8080.",
                "metadata": {"status": "active", "kind": "infrastructure"},
            },
            {
                "id": "constraint_policy",
                "content": "Company policy prohibits external business activities while employed.",
                "metadata": {"status": "active", "kind": "constraint"},
            },
            {
                "id": "correction_naming",
                "content": "Use backend engineering not frontend developer for the role description.",
                "metadata": {"status": "active", "kind": "correction"},
            },
            {
                "id": "event_meeting",
                "content": "Team sync scheduled for 2026-03-15 to review the deployment pipeline.",
                "metadata": {"status": "active", "kind": "event"},
            },
            {
                "id": "identity_role",
                "content": "The developer targets senior platform engineering roles at growing startups.",
                "metadata": {"status": "active", "kind": "identity"},
            },
        ]

    @pytest.fixture
    def paraphrase_queries(self):
        return {
            "pref_concise": [
                "what communication style does the user like",
                "does the user prefer short or long answers",
                "how should responses be formatted",
                "does the user want brief responses",
                "what is the preferred interaction style",
            ],
            "infra_db": [
                "what port is the vector database on",
                "where does the embedding service run",
                "what ports are used for infrastructure",
                "how is the database configured",
                "what service runs on port 8080",
            ],
            "constraint_policy": [
                "can the employee start a side business",
                "what is the company policy restriction",
                "is external work allowed while employed",
                "what activities are prohibited",
                "what does the policy block",
            ],
            "correction_naming": [
                "what should the role be called",
                "is it frontend developer or something else",
                "how should the position be described",
                "what is the correct title for the role",
                "what not to call the position",
            ],
            "event_meeting": [
                "when is the team sync scheduled",
                "what meeting is planned for March",
                "was there a deployment review scheduled",
                "what happened on 2026-03-15",
                "what is the team meeting about",
            ],
            "identity_role": [
                "what roles is the developer targeting",
                "what level of position is being sought",
                "is the developer looking for senior roles",
                "what kind of jobs are being pursued",
                "what companies are being targeted",
            ],
        }

    def test_paraphrase_recall_at_3(self, gold_memories, paraphrase_queries):
        """At least 80% of paraphrases should rank their gold memory in top 3."""
        total = 0
        hits = 0
        for gold_id, queries in paraphrase_queries.items():
            for q in queries:
                reranked = rerank_rows(q, gold_memories, score_weight=1.0, annotate=True)
                top3_ids = [r["id"] for r in reranked[:3]]
                total += 1
                if gold_id in top3_ids:
                    hits += 1
        recall = hits / total
        assert recall >= 0.80, f"Paraphrase recall@3 = {recall:.2%} ({hits}/{total}), expected >= 80%"


# ---------------------------------------------------------------------------
# Near-negative discrimination: similar but different memories
# ---------------------------------------------------------------------------

class TestNearNegativeDiscrimination:
    """Verify the reranker can distinguish similar-but-different memories."""

    def test_prefers_concise_over_detailed(self):
        rows = [
            {
                "id": "pref_detailed",
                "content": "User prefers detailed comprehensive answers with full explanations.",
                "metadata": {"status": "active"},
            },
            {
                "id": "pref_concise",
                "content": "User prefers concise short direct answers without filler.",
                "metadata": {"status": "active"},
            },
        ]
        reranked = rerank_rows("does the user prefer brief concise responses", rows, score_weight=1.0)
        assert reranked[0]["id"] == "pref_concise"

    def test_distinguishes_infra_ports(self):
        rows = [
            {
                "id": "db_port",
                "content": "The vector database service runs on port 5432 on the primary server.",
                "metadata": {"status": "active"},
            },
            {
                "id": "cache_port",
                "content": "The Redis cache service runs on port 6379 on the secondary server.",
                "metadata": {"status": "active"},
            },
        ]
        reranked = rerank_rows("what port is the vector database on", rows, score_weight=1.0)
        assert reranked[0]["id"] == "db_port"

        reranked2 = rerank_rows("what port is the Redis cache on", rows, score_weight=1.0)
        assert reranked2[0]["id"] == "cache_port"


# ---------------------------------------------------------------------------
# Supersession ordering
# ---------------------------------------------------------------------------

class TestSupersessionOrdering:
    """Active rules must outrank superseded rules for the same topic."""

    def test_active_outranks_superseded(self):
        rows = [
            {
                "id": "old_rule",
                "content": "Memory cache max age is seven days and stale entries may still be served.",
                "metadata": {"status": "superseded"},
            },
            {
                "id": "new_rule",
                "content": "Memory cache must be checked within max-age window and stale entries cannot be served.",
                "metadata": {"status": "active"},
            },
        ]
        reranked = rerank_rows(
            "current active rule about memory cache max age stale entries",
            rows,
            score_weight=1.0,
            annotate=True,
        )
        ids = [r["id"] for r in reranked]
        active_idx = ids.index("new_rule")
        old_idx = ids.index("old_rule")
        assert active_idx < old_idx, f"Active rule should rank higher; got {ids}"


# ---------------------------------------------------------------------------
# Batch exact recall
# ---------------------------------------------------------------------------

class TestBatchExactRecall:
    """Verify exact recall on unique-token batch items."""

    def test_batch_recall_at_3(self):
        rows = []
        for i in range(50):
            rows.append({
                "id": f"batch_{i:03d}",
                "content": f"BATCH item {i:03d}: unique code RANKER-{i:03d}-token-{49-i:03d}.",
                "metadata": {"status": "active"},
            })
        correct = 0
        for i in range(50):
            want = f"batch_{i:03d}"
            query = f"unique code RANKER-{i:03d}-token-{49-i:03d}"
            reranked = rerank_rows(query, rows, score_weight=1.0)
            top3 = [r["id"] for r in reranked[:3]]
            if want in top3:
                correct += 1
        recall = correct / 50
        assert recall >= 0.95, f"Batch recall@3 = {recall:.0%}, expected >= 95%"


# ---------------------------------------------------------------------------
# Decoy quarantine
# ---------------------------------------------------------------------------

class TestDecoyQuarantine:
    """Decoy-status memories should be penalized."""

    def test_decoy_penalized(self):
        rows = [
            {
                "id": "decoy_1",
                "content": "User prefers concise answers with directness and brevity in all communication.",
                "metadata": {"status": "decoy", "kind": "decoy"},
            },
            {
                "id": "real_pref",
                "content": "User prefers concise answers with directness and brevity.",
                "metadata": {"status": "active"},
            },
        ]
        reranked = rerank_rows("does the user prefer concise answers", rows, score_weight=1.0)
        assert reranked[0]["id"] == "real_pref"


# ---------------------------------------------------------------------------
# Annotation
# ---------------------------------------------------------------------------

class TestAnnotation:
    def test_annotate_fields_present(self):
        rows = [
            {"id": "m1", "content": "Test memory about Python development.", "metadata": {"status": "active"}},
        ]
        result = rerank_rows("Python development", rows, annotate=True)
        assert "md_score" in result[0]
        assert "md_distance" in result[0]
        assert "md_penalty" in result[0]
        assert "md_combined_score" in result[0]

    def test_no_annotate_clean(self):
        rows = [
            {"id": "m1", "content": "Test memory about Python development.", "metadata": {"status": "active"}},
        ]
        result = rerank_rows("Python development", rows, annotate=False)
        assert "md_score" not in result[0]


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------

class TestClaimEventFrame:
    def test_extracts_preferences(self):
        frame = extract_claim_event_frame("User prefers concise responses over verbose ones.", is_query=True)
        assert "preference" in frame["claim_types"]

    def test_extracts_dates(self):
        frame = extract_claim_event_frame("Team meeting scheduled for 2026-03-15.", is_query=True)
        assert "event_time" in frame["claim_types"]
        assert "2026-03-15" in frame["time_expressions"]

    def test_extracts_infrastructure(self):
        frame = extract_claim_event_frame("Database runs on port 5432.", is_query=True)
        assert len(frame["keywords"]) > 0

    def test_extracts_identity(self):
        frame = extract_claim_event_frame("The role is senior platform engineer.", is_query=True)
        assert "identity" in frame["claim_types"]


# ---------------------------------------------------------------------------
# MemoryProvider ABC hook
# ---------------------------------------------------------------------------

class TestPostQueryRerankHook:
    """Verify the optional hook on the MemoryProvider ABC."""

    def test_default_is_noop(self):
        """The default implementation returns rows unchanged."""
        from agent.memory_provider import MemoryProvider

        # Create a minimal concrete subclass
        class StubProvider(MemoryProvider):
            @property
            def name(self) -> str:
                return "stub"

            def is_available(self) -> bool:
                return False

            def initialize(self, session_id: str, **kwargs) -> None:
                pass

            def system_prompt_block(self) -> str:
                return ""

            def prefetch(self, query: str, *, session_id: str = "") -> str:
                return ""

            def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
                pass

            def get_tool_schemas(self) -> list:
                return []

            def handle_tool_call(self, name: str, args: dict) -> dict:
                return {}

            def shutdown(self) -> None:
                pass

        provider = StubProvider()
        rows = [
            {"id": "m1", "content": "test", "metadata": {}},
            {"id": "m2", "content": "other", "metadata": {}},
        ]
        result = provider.post_query_rerank("query", rows)
        assert result is rows  # same list, unchanged

    def test_md_reranker_satisfies_hook(self):
        """The reference implementation can be used via the hook."""
        from plugins.memory.md_reranker import rerank_rows

        rows = [
            {"id": "m1", "content": "User prefers concise answers.", "metadata": {"status": "active"}},
            {"id": "m2", "content": "Database runs on port 5432.", "metadata": {"status": "active"}},
        ]
        # A provider would call rerank_rows inside its post_query_rerank override
        reranked = rerank_rows("communication style preferences", rows)
        assert len(reranked) == 2
        assert reranked[0]["id"] == "m1"  # concise answers matches better
