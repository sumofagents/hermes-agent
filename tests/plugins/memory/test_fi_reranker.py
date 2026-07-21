"""Tests for the Fisher-Rao pullback memory reranker.

These tests do NOT require ChromaDB or any external service. They construct
candidate rows as plain dicts and test the reranker directly.
"""

import math
import pytest

from plugins.memory.fi_reranker import (
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
                "id": "infra_sentinel",
                "content": "Sentinel server runs ChromaDB on port 8000 with Forge embeddings on port 8006.",
                "metadata": {"status": "active", "kind": "infrastructure"},
            },
            {
                "id": "constraint_boa",
                "content": "Bank of America no-outside-business policy blocks operating a company until he quits.",
                "metadata": {"status": "active", "kind": "constraint"},
            },
            {
                "id": "correction_naming",
                "content": "Use financial-crimes modeling not cyber investigator for outward framing.",
                "metadata": {"status": "active", "kind": "correction"},
            },
            {
                "id": "event_spacex",
                "content": "SpaceX outreach email ai_eng@spacex.com verified on 2026-06-29.",
                "metadata": {"status": "active", "kind": "event"},
            },
            {
                "id": "identity_role",
                "content": "Targets senior principal AI engineering roles at SpaceX Anduril and Nous Research.",
                "metadata": {"status": "active", "kind": "identity"},
            },
        ]

    @pytest.fixture
    def paraphrase_queries(self):
        return {
            "pref_concise": [
                "what communication style does he like",
                "does he prefer short or long answers",
                "how should I format responses",
                "does he want brief responses",
                "what is his preferred interaction style",
            ],
            "infra_sentinel": [
                "what port is the vector database on",
                "where does the embedding service run",
                "what server hosts ChromaDB",
                "how is the memory infrastructure configured",
                "where are the Qwen embeddings served",
            ],
            "constraint_boa": [
                "can he start a company while employed",
                "what is the Bank of America restriction",
                "is he allowed to operate a business",
                "what blocks him from doing outside business",
                "when can he start a company",
            ],
            "correction_naming": [
                "what should I call his fraud work",
                "is it cyber investigator or something else",
                "how should I describe his professional focus",
                "what is the correct framing for his work",
                "what not to call his background",
            ],
            "event_spacex": [
                "when was the SpaceX email verified",
                "what is the SpaceX contact email",
                "did he reach out to SpaceX",
                "what happened in late June 2026",
                "what is the ai_eng address",
            ],
            "identity_role": [
                "what companies is he targeting",
                "what level of role does he want",
                "is he looking for senior positions",
                "what kind of jobs is he applying for",
                "where does he want to work in AI",
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
        reranked = rerank_rows("does he prefer brief concise responses", rows, score_weight=1.0)
        assert reranked[0]["id"] == "pref_concise"

    def test_distinguishes_infra_ports(self):
        rows = [
            {
                "id": "chroma_port",
                "content": "ChromaDB vector database runs on port 8000 on the Sentinel server.",
                "metadata": {"status": "active"},
            },
            {
                "id": "embed_port",
                "content": "Qwen embedding service runs on port 8006 on the Forge server.",
                "metadata": {"status": "active"},
            },
        ]
        reranked = rerank_rows("what port is the vector database on", rows, score_weight=1.0)
        assert reranked[0]["id"] == "chroma_port"

        reranked2 = rerank_rows("what port is the embedding service on", rows, score_weight=1.0)
        assert reranked2[0]["id"] == "embed_port"


# ---------------------------------------------------------------------------
# Supersession ordering
# ---------------------------------------------------------------------------

class TestSupersessionOrdering:
    """Active rules must outrank superseded rules for the same topic."""

    def test_active_outranks_superseded(self):
        rows = [
            {
                "id": "old_rule",
                "content": "Memory receipt max age is seven days and stale receipts may promote claims.",
                "metadata": {"status": "superseded"},
            },
            {
                "id": "new_rule",
                "content": "Memory receipt must be checked within max-age window and stale receipts cannot promote claims.",
                "metadata": {"status": "active"},
            },
        ]
        reranked = rerank_rows(
            "current active rule about memory receipt max age stale",
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
        reranked = rerank_rows("does he prefer concise answers", rows, score_weight=1.0)
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
        assert "fi_score" in result[0]
        assert "fi_distance" in result[0]
        assert "fi_penalty" in result[0]
        assert "fi_combined_score" in result[0]

    def test_no_annotate_clean(self):
        rows = [
            {"id": "m1", "content": "Test memory about Python development.", "metadata": {"status": "active"}},
        ]
        result = rerank_rows("Python development", rows, annotate=False)
        assert "fi_score" not in result[0]


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------

class TestClaimEventFrame:
    def test_extracts_preferences(self):
        frame = extract_claim_event_frame("User prefers concise responses over verbose ones.", is_query=True)
        assert "preference" in frame["claim_types"]

    def test_extracts_dates(self):
        frame = extract_claim_event_frame("SpaceX outreach verified on 2026-06-29.", is_query=True)
        assert "event_time" in frame["claim_types"]
        assert "2026-06-29" in frame["time_expressions"]

    def test_extracts_infrastructure(self):
        frame = extract_claim_event_frame("ChromaDB runs on port 8000.", is_query=True)
        # Should have some work/infrastructure related atoms
        assert len(frame["keywords"]) > 0

    def test_extracts_identity(self):
        frame = extract_claim_event_frame("His role is senior AI engineer.", is_query=True)
        assert "identity" in frame["claim_types"]
