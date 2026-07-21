import json

import pytest

from plugins.memory.chromadb import ChromaDBMemoryProvider
from plugins.memory.chromadb.config import ChromaDBConfig


def test_chromadb_tool_schemas_exposed_before_runtime_available():
    provider = ChromaDBMemoryProvider()
    provider._available = False

    schemas = provider.get_tool_schemas()

    assert {schema["name"] for schema in schemas} == {"team_memory", "vector_search"}

    result = json.loads(provider.handle_tool_call("vector_search", {"query": "hello"}))
    assert result == {"error": "ChromaDB is not available."}


def test_chromadb_tool_schemas_hidden_in_cron_context():
    provider = ChromaDBMemoryProvider()
    provider._available = False
    provider._cron_skipped = True

    assert provider.get_tool_schemas() == []
    assert json.loads(provider.handle_tool_call("vector_search", {"query": "hello"})) == {
        "error": "ChromaDB is not active (cron context)."
    }


# ---------------------------------------------------------------------------
# Generated profile initialize kwargs (Phase 1)
# ---------------------------------------------------------------------------


def test_initialize_accepts_prompt_source_and_generated_enabled(monkeypatch, tmp_path):
    provider = ChromaDBMemoryProvider()
    # Avoid touching real ChromaDB
    monkeypatch.setattr(provider, "_init_client",
                        lambda: setattr(provider, "_available", True))
    monkeypatch.setattr(provider, "_load_team_context", lambda: None)

    provider.initialize(
        "sess-1",
        hermes_home=str(tmp_path),
        agent_identity="rilo",
        platform="cli",
        agent_context="primary",
        prompt_source="shadow",
        generated_prompt_enabled=True,
    )
    assert provider._prompt_source == "shadow"
    assert provider._generated_profile_enabled is True
    assert provider._agent_context == "primary"


def test_initialize_defaults_legacy_when_kwargs_missing(monkeypatch, tmp_path):
    provider = ChromaDBMemoryProvider()
    monkeypatch.setattr(provider, "_init_client",
                        lambda: setattr(provider, "_available", True))
    monkeypatch.setattr(provider, "_load_team_context", lambda: None)

    provider.initialize(
        "sess-1",
        hermes_home=str(tmp_path),
        agent_identity="rilo",
        platform="cli",
    )
    assert provider._prompt_source == "legacy"
    assert provider._generated_profile_enabled is False


def test_cron_initialize_sets_cron_skipped_and_no_generation(monkeypatch, tmp_path):
    provider = ChromaDBMemoryProvider()

    embed_calls: list = []
    monkeypatch.setattr(provider, "_init_client",
                        lambda: setattr(provider, "_available", True))
    monkeypatch.setattr(provider, "_embed",
                        lambda texts: embed_calls.append(texts) or [[0.0]])

    provider.initialize(
        "sess",
        hermes_home=str(tmp_path),
        agent_identity="rilo",
        platform="cron",
        agent_context="cron",
        prompt_source="provider_with_legacy_fallback",
        generated_prompt_enabled=True,
    )
    assert provider._cron_skipped is True
    assert provider.system_prompt_block() == ""
    assert embed_calls == []


# ---------------------------------------------------------------------------
# Atlas FI runtime reranking
# ---------------------------------------------------------------------------


def test_config_accepts_atlas_fi_runtime_controls():
    cfg = ChromaDBConfig.from_dict({
        "atlas_fi_runtime": {
            "enabled": True,
            "score_weight": 0.7,
            "candidate_multiplier": 5,
            "max_candidates": 42,
            "min_candidates": 2,
            "annotate_results": False,
        }
    })

    assert cfg.atlas_fi_runtime.enabled is True
    assert cfg.atlas_fi_runtime.score_weight == 0.7
    assert cfg.atlas_fi_runtime.candidate_multiplier == 5
    assert cfg.atlas_fi_runtime.max_candidates == 42
    assert cfg.atlas_fi_runtime.min_candidates == 2
    assert cfg.atlas_fi_runtime.annotate_results is False
    assert cfg.to_dict()["atlas_fi_runtime"]["enabled"] is True


def test_atlas_fi_runtime_expands_candidates_and_reranks_team_memory(monkeypatch):
    provider = ChromaDBMemoryProvider()
    provider._available = True
    provider._collections = {"team_knowledge": object()}
    provider._config = ChromaDBConfig.from_dict({
        "atlas_fi_runtime": {
            "enabled": True,
            "score_weight": 1.0,
            "candidate_multiplier": 3,
            "max_candidates": 8,
            "min_candidates": 1,
            "annotate_results": True,
        }
    })

    requested_counts = []

    def fake_query(collection, query_text: str, n_results=10, where=None, include=None):
        requested_counts.append(n_results)
        return {
            "ids": [["decoy_control", "gold_both_pass", "plain_note"]],
            "documents": [[
                "Control failure quarantine: wrong orientation / shuffled J means do not return to locked PHYRE chart.",
                "Both gates passed: toy and pendulum, analytic and learned arms pass. Verdict: return to locked PHYRE chart.",
                "Plain Atlas note about unrelated memory routing.",
            ]],
            "metadatas": [[
                {"status": "active", "kind": "control_decoy"},
                {"status": "active", "kind": "verdict_result"},
                {"status": "active", "kind": "note"},
            ]],
            "distances": [[0.01, 0.90, 0.20]],
        }

    monkeypatch.setattr(provider, "_query", fake_query)

    rows = provider.search_team_knowledge(
        "Which memory says both gates pass and we return to the locked PHYRE chart?",
        n_results=2,
    )

    assert requested_counts == [6]
    assert rows[0]["id"] == "gold_both_pass"
    assert rows[1]["id"] == "decoy_control"
    assert rows[0]["atlas_fi_score"] > rows[1]["atlas_fi_score"]
    assert "pre_atlas_fi_composite_score" not in rows[0]


def test_vector_search_tool_reports_atlas_fi_receipts(monkeypatch):
    provider = ChromaDBMemoryProvider()
    provider._available = True
    provider._collections = {"team_knowledge": object()}
    provider._config = ChromaDBConfig.from_dict({
        "atlas_fi_runtime": {
            "enabled": True,
            "score_weight": 1.0,
            "candidate_multiplier": 2,
            "max_candidates": 6,
            "min_candidates": 1,
            "annotate_results": True,
        }
    })

    def fake_query(collection, query_text: str, n_results=10, where=None, include=None):
        return {
            "ids": [["decoy_control", "gold_both_pass"]],
            "documents": [[
                "Control failure quarantine: wrong orientation / shuffled J.",
                "Both gates passed: toy and pendulum both pass. Verdict: return to locked PHYRE chart.",
            ]],
            "metadatas": [[
                {"status": "active", "kind": "control_decoy"},
                {"status": "active", "kind": "verdict_result"},
            ]],
            "distances": [[0.01, 0.90]],
        }

    monkeypatch.setattr(provider, "_query", fake_query)

    payload = json.loads(provider.handle_tool_call("vector_search", {
        "query": "both gates pass return to locked PHYRE chart",
        "collection": "team_knowledge",
        "n_results": 1,
    }))

    assert payload["status"] == "ok"
    assert payload["count"] == 1
    assert "Both gates passed" in payload["results"][0]["content"]
    assert set(payload["results"][0]["atlas_fi"]) == {"score", "distance", "penalty"}


# ---------------------------------------------------------------------------
# Atlas FI write-path metadata
# ---------------------------------------------------------------------------


def _capture_single_upsert(monkeypatch, provider):
    captured = {}

    def fake_upsert(collection, ids, documents, metadatas=None):
        captured["collection"] = collection
        captured["ids"] = ids
        captured["documents"] = documents
        captured["metadatas"] = metadatas or []

    monkeypatch.setattr(provider, "_upsert", fake_upsert)
    return captured


def test_store_memory_enriches_write_metadata_with_fi_claim_event_frame(monkeypatch):
    provider = ChromaDBMemoryProvider()
    provider._available = True
    provider._collections = {"memories": object()}
    captured = _capture_single_upsert(monkeypatch, provider)

    doc_id = provider.store_memory(
        "Jeremiah prefers Rilo-led QRMv2 training on Forge RTX 3090 when explicitly authorized.",
        "memory",
        {"source": "test"},
    )

    assert doc_id.startswith("memory_")
    meta = captured["metadatas"][0]
    assert meta["fi_schema_version"] == "atlas_fi_claim_event_v1"
    assert meta["fi_write_enriched"] is True
    assert meta["source_text"] == captured["documents"][0]
    assert "preference" in meta["claim_types_csv"] or "plan" in meta["claim_types_csv"]
    assert "jeremiah" in meta["subjects_csv"]
    assert "qrmv2" in meta["objects_csv"]
    assert "work_education" in meta["facets_csv"] or "preference" in meta["facets_csv"]
    assert "target" in meta and meta["target"] == "memory"


def test_team_and_session_writes_are_fi_enriched(monkeypatch):
    provider = ChromaDBMemoryProvider()
    provider._available = True
    provider._collections = {
        "team_knowledge": object(),
        "team_ops": object(),
        "sessions": object(),
        "agent_rilo": object(),
    }
    captured = []

    def fake_upsert(collection, ids, documents, metadatas=None):
        captured.append({
            "collection": collection,
            "ids": ids,
            "documents": documents,
            "metadatas": metadatas or [],
        })

    monkeypatch.setattr(provider, "_upsert", fake_upsert)

    assert provider.store_team_knowledge(
        "QRMv2 Iteration 2 uses source-owned fixtures and excludes CLEVR until a later gate.",
        metadata={"source_agent": "rilo"},
    )
    assert provider.store_team_ops(
        "QRMv2 I2 Forge smoke should emit manifest, summary, and checkpoint hash receipts.",
        agent_name="rilo",
    )
    assert provider.store_session_summary(
        "sess-1",
        "Session summary: user asked to repair FI memory writes before QRMv2 I2.",
    )
    assert provider.store_agent_memory(
        "Rilo owns QRMv2 execution directly when Jeremiah authorizes training.",
        agent_name="rilo",
    )

    assert len(captured) == 4
    for item in captured:
        meta = item["metadatas"][0]
        assert meta["fi_schema_version"] == "atlas_fi_claim_event_v1"
        assert meta["fi_write_enriched"] is True
        assert meta["source_text"] == item["documents"][0]
        assert meta["claim_types_csv"]
        assert "facets_csv" in meta
        assert "keywords_csv" in meta
