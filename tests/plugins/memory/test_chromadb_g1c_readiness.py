from __future__ import annotations

import json


class FakeChromaCollection:
    def __init__(self, count: int):
        self._count = count

    def count(self) -> int:
        return self._count


class FakeChromaClient:
    def __init__(self, collections: dict[str, int]):
        self.collections = collections
        self.calls: list[str] = []

    def heartbeat(self):
        self.calls.append("heartbeat")
        return {"nanosecond heartbeat": 123}

    def get_collection(self, name: str):
        self.calls.append(f"get_collection:{name}")
        if name not in self.collections:
            raise ValueError("missing collection")
        return FakeChromaCollection(self.collections[name])

    def get_or_create_collection(self, name: str):  # pragma: no cover - must never be called
        raise AssertionError("readiness probe must be read-only")


class FakeResponse:
    def __init__(self, payload, status: int = 200):
        self.payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class FakeConfig:
    chromadb_host = "127.0.0.1"
    chromadb_port = 8000
    embedding_service_url = "http://forge.local:8006"
    embedding_model = "Qwen/Qwen3-Embedding-0.6B"
    collections = {"memories": "agent_memories", "sessions": "session_history"}


def test_evaluate_config_readiness_uses_effective_defaults_for_g3():
    from plugins.memory.chromadb.g1c_readiness import evaluate_config_readiness

    report = evaluate_config_readiness({"memory": {"provider": "chromadb"}})

    checks = {item["name"]: item for item in report["checks"]}
    assert checks["memory.provider"]["ok"] is True
    assert checks["memory.boot_synthesis_enabled"]["ok"] is True
    assert checks["memory.first_turn_recall_enabled"]["ok"] is True
    assert checks["memory.retrieval_routing_enabled"]["ok"] is True
    assert report["ok"] is True


def test_probe_chroma_is_read_only_and_counts_configured_collections():
    from plugins.memory.chromadb.g1c_readiness import probe_chroma

    client = FakeChromaClient({"agent_memories": 3, "session_history": 2})
    report = probe_chroma(FakeConfig(), client_factory=lambda host, port, timeout: client, timeout=0.1)

    assert report["ok"] is True
    assert report["heartbeat_ok"] is True
    assert report["collections"] == {
        "memories": {"name": "agent_memories", "ok": True, "count": 3},
        "sessions": {"name": "session_history", "ok": True, "count": 2},
    }
    assert all(not call.startswith("get_or_create") for call in client.calls)


def test_probe_forge_uses_health_and_embed_not_embed_single():
    from plugins.memory.chromadb.g1c_readiness import probe_forge

    seen: list[str] = []

    def opener(request, timeout=0):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        seen.append(url)
        if url.endswith("/health"):
            return FakeResponse({"status": "ok", "model": "Qwen/Qwen3-Embedding-0.6B", "dimensions": 1024})
        if url.endswith("/embed"):
            body = json.loads(request.data.decode("utf-8"))
            assert body == {"texts": ["Hermes Chroma readiness probe"]}
            return FakeResponse({"embeddings": [[0.0] * 1024]})
        raise AssertionError(f"unexpected URL: {url}")

    report = probe_forge(FakeConfig(), opener=opener, timeout=0.1, embed_check=True)

    assert report["ok"] is True
    assert report["health"]["dimensions"] == 1024
    assert report["embed"]["dimensions"] == 1024
    assert not any(url.endswith("/embed-single") for url in seen)


def test_probe_chroma_http_fallback_when_python_package_missing(monkeypatch):
    from plugins.memory.chromadb import g1c_readiness

    opened: list[str] = []

    def opener(url, timeout=0):
        opened.append(str(url))
        if str(url).endswith("/api/v2/heartbeat"):
            return FakeResponse({"nanosecond heartbeat": 123})
        if str(url).endswith("/collections"):
            return FakeResponse([
                {"id": "mem-id", "name": "agent_memories"},
                {"id": "sess-id", "name": "session_history"},
            ])
        if str(url).endswith("/collections/mem-id/count"):
            return FakeResponse(3)
        if str(url).endswith("/collections/sess-id/count"):
            return FakeResponse(2)
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(
        g1c_readiness,
        "_default_chroma_client_factory",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("chromadb import failed")),
    )

    report = g1c_readiness.probe_chroma(FakeConfig(), http_opener=opener, timeout=0.1)

    assert report["ok"] is True
    assert report["collections"]["memories"]["count"] == 3
    assert report["collections"]["sessions"]["count"] == 2
    assert any(url.endswith("/collections/mem-id/count") for url in opened)


def test_probe_chroma_reports_http_fallback_error_when_both_transports_fail(monkeypatch):
    from plugins.memory.chromadb import g1c_readiness

    def opener(url, timeout=0):
        raise TimeoutError("fallback timed out")

    monkeypatch.setattr(
        g1c_readiness,
        "_default_chroma_client_factory",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("chromadb import failed")),
    )

    report = g1c_readiness.probe_chroma(FakeConfig(), http_opener=opener, timeout=0.1)

    assert report["ok"] is False
    assert report["transport"] == "http-v2"
    assert "fallback timed out" in report["error"]
    assert "chromadb import failed" in report["client_error"]


def test_build_readiness_report_default_has_no_network_probes(tmp_path):
    from plugins.memory.chromadb.g1c_readiness import build_readiness_report

    home = tmp_path / ".hermes"
    logs = home / "logs"
    logs.mkdir(parents=True)
    (logs / "memory_feedback.jsonl").write_text(
        json.dumps({"event_type": "recall_used", "fact_id": "f1", "labels": ["job_search"]}) + "\n",
        encoding="utf-8",
    )
    (home / "chromadb.json").write_text(
        json.dumps({"chromadb_host": "127.0.0.1", "embedding_service_url": "http://forge.local:8006"}),
        encoding="utf-8",
    )

    def fail_chroma(*args, **kwargs):
        raise AssertionError("default report must not touch Chroma")

    def fail_forge(*args, **kwargs):
        raise AssertionError("default report must not touch Forge")

    report = build_readiness_report(
        hermes_home=home,
        config={"memory": {"provider": "chromadb"}},
        limit=10,
        probe=False,
        chroma_client_factory=fail_chroma,
        forge_opener=fail_forge,
    )

    assert report["ok"] is True
    assert report["probe_enabled"] is False
    assert report["feedback"]["event_types"] == {"recall_used": 1}
    assert "chroma_probe" not in report
    assert "forge_probe" not in report


def test_load_chromadb_json_status_reports_non_object_json(tmp_path):
    from plugins.memory.chromadb.g1c_readiness import load_chromadb_json_status

    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "chromadb.json").write_text("[]", encoding="utf-8")

    report = load_chromadb_json_status(home)

    assert report["ok"] is False
    assert report["exists"] is True
    assert "error" in report
