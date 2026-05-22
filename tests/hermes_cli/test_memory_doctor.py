from __future__ import annotations

import json
from argparse import Namespace


def test_memory_doctor_default_summarizes_boot_and_feedback_without_network(tmp_path, monkeypatch, capsys):
    from hermes_cli.memory_setup import memory_command

    home = tmp_path / ".hermes"
    logs = home / "logs"
    logs.mkdir(parents=True)
    (home / "chromadb.json").write_text(
        json.dumps({"chromadb_host": "127.0.0.1", "embedding_service_url": "http://forge.local:8006"}),
        encoding="utf-8",
    )
    boot = logs / "boot_synthesis.jsonl"
    feedback = logs / "memory_feedback.jsonl"
    boot.write_text(
        json.dumps({
            "session_id": "s1",
            "model": "qwen2.5:7b",
            "fallback_path_taken": False,
            "latency_ms": 12,
            "selected_ids": ["fact1"],
            "dropped_ids": [],
            "candidates": [{"id": "fact1", "metadata": {"source": "builtin_mirror"}, "durability_label": "durable"}],
        }) + "\n",
        encoding="utf-8",
    )
    feedback.write_text(
        json.dumps({"event_type": "recall_needed", "fact_id": "", "labels": ["job_search"]}) + "\n" +
        json.dumps({"event_type": "recall_used", "fact_id": "fact1", "labels": ["job_search"]}) + "\n",
        encoding="utf-8",
    )
    before_boot = boot.read_bytes()
    before_feedback = feedback.read_bytes()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(
        "hermes_cli.memory_setup.load_config",
        lambda: {"memory": {"provider": "chromadb"}},
    )
    monkeypatch.setattr(
        "plugins.memory.chromadb.g1c_readiness.probe_chroma",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("default doctor must not probe Chroma")),
    )
    monkeypatch.setattr(
        "plugins.memory.chromadb.g1c_readiness.probe_forge",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("default doctor must not probe Forge")),
    )

    memory_command(Namespace(memory_command="doctor", json=False, limit=100, probe=False, strict=False))

    out = capsys.readouterr().out
    assert "Memory doctor" in out
    assert "provider: chromadb" in out
    assert "recall_used" in out
    assert boot.read_bytes() == before_boot
    assert feedback.read_bytes() == before_feedback


def test_memory_doctor_json_reports_feedback_recall_events(tmp_path, monkeypatch, capsys):
    from hermes_cli.memory_setup import memory_command

    home = tmp_path / ".hermes"
    logs = home / "logs"
    logs.mkdir(parents=True)
    (home / "chromadb.json").write_text("{}", encoding="utf-8")
    (logs / "memory_feedback.jsonl").write_text(
        "not-json\n" +
        json.dumps({"event_type": "recall_skipped", "labels": ["not_triggered"]}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))

    memory_command(Namespace(memory_command="doctor", json=True, limit=10, probe=False, strict=False))

    payload = json.loads(capsys.readouterr().out)
    assert payload["feedback"]["event_types"] == {"recall_skipped": 1}
    assert payload["feedback"]["malformed_count"] == 1
    assert payload["probe_enabled"] is False


def test_memory_doctor_strict_raises_nonzero_for_bad_local_config(tmp_path, monkeypatch):
    from hermes_cli.memory_setup import memory_command

    home = tmp_path / ".hermes"
    home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(
        "hermes_cli.memory_setup.load_config",
        lambda: {"memory": {"provider": ""}},
        raising=False,
    )

    try:
        memory_command(Namespace(memory_command="doctor", json=True, limit=10, probe=False, strict=True))
    except SystemExit as exc:
        assert exc.code == 1
    else:  # pragma: no cover
        raise AssertionError("strict doctor must exit nonzero on local readiness failure")


def test_memory_doctor_probe_reports_injected_chroma_and_forge(tmp_path, monkeypatch, capsys):
    from hermes_cli.memory_setup import memory_command

    home = tmp_path / ".hermes"
    home.mkdir(parents=True)
    (home / "chromadb.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(
        "hermes_cli.memory_setup.load_config",
        lambda: {"memory": {"provider": "chromadb"}},
    )
    monkeypatch.setattr(
        "plugins.memory.chromadb.g1c_readiness.probe_chroma",
        lambda *a, **k: {"ok": True, "heartbeat_ok": True, "collections": {}},
    )
    monkeypatch.setattr(
        "plugins.memory.chromadb.g1c_readiness.probe_forge",
        lambda *a, **k: {"ok": True, "health": {"dimensions": 1024}, "embed": {"dimensions": 1024}},
    )

    memory_command(Namespace(memory_command="doctor", json=True, limit=10, probe=True, strict=True))

    payload = json.loads(capsys.readouterr().out)
    assert payload["probe_enabled"] is True
    assert payload["chroma_probe"]["ok"] is True
    assert payload["forge_probe"]["ok"] is True
