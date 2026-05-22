"""Goal 1C local readiness and observability helpers for ChromaDB memory.

G1C is a read-only diagnostic layer. It summarizes local G1A/G1B/G2
observability files and optionally probes the configured ChromaDB and Forge
embedding endpoints without creating collections, upserting facts, restarting
services, or touching MEMORY.md / USER.md.
"""
from __future__ import annotations

import json
import socket
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from hermes_cli.config import DEFAULT_CONFIG
from plugins.memory.chromadb.config import ChromaDBConfig
from plugins.memory.chromadb.g1b_observability import (
    boot_receipt_path_for_home,
    feedback_path_for_home,
    read_boot_receipts,
    read_feedback_events,
    summarize_boot_receipts,
    summarize_feedback_events,
)

EXPECTED_EMBEDDING_DIMENSIONS = 1024
FORGE_PROBE_TEXT = "Hermes Chroma readiness probe"


def _nested_default(path: tuple[str, ...], fallback: Any = None) -> Any:
    node: Any = DEFAULT_CONFIG
    for key in path:
        if not isinstance(node, dict) or key not in node:
            return fallback
        node = node[key]
    return node


def _memory_value(config: dict[str, Any], key: str, default: Any) -> Any:
    memory = config.get("memory") if isinstance(config, dict) else {}
    if not isinstance(memory, dict):
        memory = {}
    return memory.get(key, default)


def _check(name: str, ok: bool, actual: Any, expected: Any = None, detail: str = "") -> dict[str, Any]:
    row = {"name": name, "ok": bool(ok), "actual": actual}
    if expected is not None:
        row["expected"] = expected
    if detail:
        row["detail"] = detail
    return row


def evaluate_config_readiness(config: dict[str, Any]) -> dict[str, Any]:
    """Evaluate effective memory config using Hermes defaults for absent G1/G2/G3 keys."""
    provider = _memory_value(config, "provider", _nested_default(("memory", "provider"), ""))
    boot = bool(_memory_value(config, "boot_synthesis_enabled", _nested_default(("memory", "boot_synthesis_enabled"), True)))
    recall = bool(_memory_value(config, "first_turn_recall_enabled", _nested_default(("memory", "first_turn_recall_enabled"), True)))
    routing = bool(_memory_value(config, "retrieval_routing_enabled", _nested_default(("memory", "retrieval_routing_enabled"), True)))
    checks = [
        _check("memory.provider", provider == "chromadb", provider, "chromadb"),
        _check("memory.boot_synthesis_enabled", boot is True, boot, True),
        _check("memory.first_turn_recall_enabled", recall is True, recall, True),
        _check("memory.retrieval_routing_enabled", routing is True, routing, True),
    ]
    return {"ok": all(item["ok"] for item in checks), "checks": checks}


def load_chromadb_json_status(hermes_home: str | Path) -> dict[str, Any]:
    path = Path(hermes_home).expanduser() / "chromadb.json"
    if not path.exists():
        return {"ok": False, "path": str(path), "exists": False, "error": "missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "path": str(path), "exists": True, "error": str(exc)}
    if not isinstance(payload, dict):
        return {
            "ok": False,
            "path": str(path),
            "exists": True,
            "error": f"expected object, got {type(payload).__name__}",
        }
    return {
        "ok": True,
        "path": str(path),
        "exists": True,
        "host": payload.get("chromadb_host"),
        "port": payload.get("chromadb_port"),
        "embedding_service_url": payload.get("embedding_service_url"),
        "embedding_model": payload.get("embedding_model"),
        "collections": payload.get("collections", {}),
    }


def _default_chroma_client_factory(host: str, port: int, timeout: float):
    try:
        import chromadb  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on local optional package
        raise RuntimeError(f"chromadb import failed: {exc}") from exc
    return chromadb.HttpClient(host=host, port=port)


def probe_chroma(
    cfg: ChromaDBConfig,
    *,
    client_factory: Optional[Callable[[str, int, float], Any]] = None,
    http_opener: Optional[Callable[..., Any]] = None,
    timeout: float = 3.0,
) -> dict[str, Any]:
    """Read-only Chroma probe: heartbeat + collection counts only."""
    factory = client_factory or _default_chroma_client_factory
    old_timeout = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(timeout)
        client = factory(cfg.chromadb_host, int(cfg.chromadb_port), timeout)
        heartbeat = client.heartbeat()
        collections: dict[str, dict[str, Any]] = {}
        for logical, name in sorted((cfg.collections or {}).items()):
            try:
                col = client.get_collection(name=str(name))
                collections[str(logical)] = {"name": str(name), "ok": True, "count": int(col.count())}
            except Exception as exc:
                collections[str(logical)] = {"name": str(name), "ok": False, "error": str(exc)}
        return {
            "ok": bool(heartbeat) and all(row.get("ok") for row in collections.values()),
            "host": cfg.chromadb_host,
            "port": int(cfg.chromadb_port),
            "heartbeat_ok": bool(heartbeat),
            "heartbeat": heartbeat,
            "collections": collections,
            "transport": "chromadb-client",
        }
    except Exception as exc:
        if client_factory is None:
            fallback = _probe_chroma_http(cfg, opener=http_opener, timeout=timeout)
            fallback["client_error"] = str(exc)
            return fallback
        return {
            "ok": False,
            "host": getattr(cfg, "chromadb_host", ""),
            "port": int(getattr(cfg, "chromadb_port", 0) or 0),
            "heartbeat_ok": False,
            "error": str(exc),
        }
    finally:
        socket.setdefaulttimeout(old_timeout)


def _read_json_value(response: Any) -> Any:
    raw = response.read()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw or "{}")


def _read_json_response(response: Any) -> dict[str, Any]:
    payload = _read_json_value(response)
    return payload if isinstance(payload, dict) else {"value": payload}


def _probe_chroma_http(
    cfg: ChromaDBConfig,
    *,
    opener: Optional[Callable[..., Any]] = None,
    timeout: float = 3.0,
) -> dict[str, Any]:
    """Dependency-free Chroma v2 read-only probe when chromadb is absent."""
    open_fn = opener or urllib.request.urlopen
    base = f"http://{cfg.chromadb_host}:{int(cfg.chromadb_port)}"
    api = f"{base}/api/v2/tenants/default_tenant/databases/default_database"
    try:
        with open_fn(f"{base}/api/v2/heartbeat", timeout=timeout) as resp:
            heartbeat = _read_json_value(resp)
        with open_fn(f"{api}/collections", timeout=timeout) as resp:
            raw_collections = _read_json_value(resp)
        if not isinstance(raw_collections, list):
            raw_collections = []
        by_name = {
            str(item.get("name")): str(item.get("id"))
            for item in raw_collections
            if isinstance(item, dict) and item.get("name") and item.get("id")
        }
        collections: dict[str, dict[str, Any]] = {}
        for logical, name in sorted((cfg.collections or {}).items()):
            collection_id = by_name.get(str(name))
            if not collection_id:
                collections[str(logical)] = {"name": str(name), "ok": False, "error": "missing"}
                continue
            with open_fn(f"{api}/collections/{collection_id}/count", timeout=timeout) as resp:
                count_payload = _read_json_value(resp)
            count = int(count_payload.get("count") if isinstance(count_payload, dict) else count_payload)
            collections[str(logical)] = {"name": str(name), "ok": True, "count": count}
        return {
            "ok": bool(heartbeat) and all(row.get("ok") for row in collections.values()),
            "host": cfg.chromadb_host,
            "port": int(cfg.chromadb_port),
            "heartbeat_ok": bool(heartbeat),
            "heartbeat": heartbeat,
            "collections": collections,
            "transport": "http-v2",
        }
    except Exception as exc:
        return {
            "ok": False,
            "host": cfg.chromadb_host,
            "port": int(cfg.chromadb_port),
            "heartbeat_ok": False,
            "transport": "http-v2",
            "error": str(exc),
        }


def probe_forge(
    cfg: ChromaDBConfig,
    *,
    opener: Optional[Callable[..., Any]] = None,
    timeout: float = 3.0,
    embed_check: bool = True,
) -> dict[str, Any]:
    """Read-only Forge probe using /health and POST /embed, never /embed-single."""
    open_fn = opener or urllib.request.urlopen
    base = str(cfg.embedding_service_url or "").rstrip("/")
    try:
        with open_fn(f"{base}/health", timeout=timeout) as resp:
            health = _read_json_response(resp)
        health_dimensions = int(health.get("dimensions", 0) or 0)
        health_ok = health_dimensions == EXPECTED_EMBEDDING_DIMENSIONS
        embed: dict[str, Any] = {"skipped": not embed_check}
        embed_ok = True
        if embed_check:
            body = json.dumps({"texts": [FORGE_PROBE_TEXT]}).encode("utf-8")
            req = urllib.request.Request(
                f"{base}/embed",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with open_fn(req, timeout=timeout) as resp:
                payload = _read_json_response(resp)
            embeddings = payload.get("embeddings") or []
            first = embeddings[0] if embeddings and isinstance(embeddings[0], list) else []
            embed = {"dimensions": len(first)}
            embed_ok = len(first) == EXPECTED_EMBEDDING_DIMENSIONS
        return {
            "ok": bool(health_ok and embed_ok),
            "url": base,
            "health": health,
            "embed": embed,
        }
    except Exception as exc:
        return {"ok": False, "url": base, "error": str(exc)}


def build_readiness_report(
    *,
    hermes_home: str | Path,
    config: dict[str, Any],
    limit: int = 100,
    probe: bool = False,
    strict: bool = False,
    chroma_client_factory: Optional[Callable[[str, int, float], Any]] = None,
    forge_opener: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    home = Path(hermes_home).expanduser()
    bounded_limit = max(0, int(limit if limit is not None else 100))
    boot_records = read_boot_receipts(boot_receipt_path_for_home(home)).tail(bounded_limit)
    feedback_records = read_feedback_events(feedback_path_for_home(home)).tail(bounded_limit)
    config_report = evaluate_config_readiness(config)
    chromadb_json = load_chromadb_json_status(home)
    report: dict[str, Any] = {
        "schema_version": 1,
        "ok": bool(config_report["ok"] and chromadb_json["ok"]),
        "strict": bool(strict),
        "probe_enabled": bool(probe),
        "config": config_report,
        "chromadb_json": chromadb_json,
        "boot": summarize_boot_receipts(boot_records),
        "feedback": summarize_feedback_events(feedback_records),
    }
    if probe:
        cfg = ChromaDBConfig.from_json_file(str(home))
        chroma = probe_chroma(cfg, client_factory=chroma_client_factory)
        forge = probe_forge(cfg, opener=forge_opener)
        report["chroma_probe"] = chroma
        report["forge_probe"] = forge
        report["ok"] = bool(report["ok"] and chroma.get("ok") and forge.get("ok"))
    return report
