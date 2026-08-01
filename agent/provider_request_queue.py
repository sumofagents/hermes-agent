"""Cross-process provider request queue / single-flight lock.

Cheapest Inference (and similar flat-rate plans) enforce **one in-flight
request per API key**. Hermes Desktop/gateway can run many sessions at once;
without serialization, the second session gets HTTP 429 concurrency and the
error classifier eagerly fails over to ``fallback_providers`` (e.g. Codex Sol).

This module provides a file-lock slot so all Hermes processes on the same
``HERMES_HOME`` queue behind one in-flight CI call:

* Hold the lock for the **entire** provider round-trip (including streaming
  consumption), not just the HTTP open.
* Opt-in via ``providers.<id>.max_concurrent_requests`` (default 0 = off),
  with a built-in default of 1 for Cheapest Inference endpoints.
* Wait up to ``providers.<id>.request_queue_timeout_seconds`` (default:
  provider request timeout or 7200s) before raising ``TimeoutError``.

Never logs secrets.
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

# Providers / base URLs that default to single-flight when config is silent.
_DEFAULT_SINGLE_FLIGHT_PROVIDER_MARKERS = (
    "cheapest-inference",
    "cheapestinference",
    "custom:cheapest-inference",
)
_DEFAULT_SINGLE_FLIGHT_HOST_MARKERS = (
    "api.cheapestinference.com",
    "cheapestinference.com",
)


def _hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()


def _load_provider_cfg(provider: Optional[str]) -> dict[str, Any]:
    if not provider:
        return {}
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
    except Exception:
        return {}
    providers = cfg.get("providers") if isinstance(cfg, dict) else None
    if not isinstance(providers, dict):
        return {}

    # Accept custom:cheapest-inference → cheapest-inference
    candidates = [provider]
    if provider.startswith("custom:"):
        candidates.append(provider.split(":", 1)[1])
    # reverse: bare name when agent.provider is "custom" with base_url match
    for key in candidates:
        entry = providers.get(key)
        if isinstance(entry, dict):
            return entry
    # fuzzy: any provider entry whose name/api matches
    plen = provider.lower()
    for key, entry in providers.items():
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or key).lower()
        api = str(entry.get("api") or entry.get("base_url") or "").lower()
        if plen in name or plen in str(key).lower() or plen in api:
            return entry
    return {}


def _is_default_single_flight(provider: Optional[str], base_url: Optional[str]) -> bool:
    p = (provider or "").strip().lower()
    b = (base_url or "").strip().lower()
    if any(m in p for m in _DEFAULT_SINGLE_FLIGHT_PROVIDER_MARKERS):
        return True
    if any(m in b for m in _DEFAULT_SINGLE_FLIGHT_HOST_MARKERS):
        return True
    return False


def resolve_max_concurrent(
    provider: Optional[str],
    base_url: Optional[str] = None,
) -> int:
    """Return max parallel in-flight requests allowed for this provider.

    0 = unlimited (no queue). 1 = single-flight queue (CI default).
    """
    pcfg = _load_provider_cfg(provider)
    raw = pcfg.get("max_concurrent_requests")
    if raw is None and provider and provider.startswith("custom:"):
        # also try bare slug
        pcfg = _load_provider_cfg(provider.split(":", 1)[1]) or pcfg
        raw = pcfg.get("max_concurrent_requests")

    if raw is not None:
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            return 0

    if _is_default_single_flight(provider, base_url):
        return 1
    return 0


def resolve_queue_timeout_seconds(
    provider: Optional[str],
    *,
    default: float = 7200.0,
) -> float:
    pcfg = _load_provider_cfg(provider)
    for key in (
        "request_queue_timeout_seconds",
        "request_timeout_seconds",
        "stale_timeout_seconds",
    ):
        raw = pcfg.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
            if val > 0:
                return val
        except (TypeError, ValueError):
            continue
    return float(default)


def _lock_path(provider: Optional[str], base_url: Optional[str]) -> Path:
    # Stable key shared by custom:cheapest-inference and base_url form
    if _is_default_single_flight(provider, base_url):
        slug = "cheapest-inference"
    else:
        raw = (provider or "unknown").strip().lower().replace("/", "-")
        raw = raw.replace(":", "-").replace(" ", "-")
        slug = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in raw)[:80]
    lock_dir = _hermes_home() / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"provider-slot-{slug}.lock"


class _FileSlot:
    """Exclusive flock held for the duration of one provider round-trip."""

    def __init__(self, path: Path, timeout: float, label: str):
        self.path = path
        self.timeout = max(1.0, float(timeout))
        self.label = label
        self._fh = None
        self.waited = 0.0

    def acquire(self) -> None:
        if fcntl is None:
            # Best-effort no-op on platforms without flock. Classifier retry
            # still covers concurrency 429s.
            logger.warning(
                "provider_request_queue: fcntl unavailable — cannot serialize %s",
                self.label,
            )
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a+", encoding="utf-8")
        deadline = time.monotonic() + self.timeout
        start = time.monotonic()
        notified = False
        while True:
            try:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.waited = time.monotonic() - start
                if self.waited > 0.25:
                    logger.info(
                        "provider_request_queue: acquired %s after %.1fs wait",
                        self.label,
                        self.waited,
                    )
                try:
                    self._fh.seek(0)
                    self._fh.truncate()
                    self._fh.write(
                        f"pid={os.getpid()} label={self.label} "
                        f"held_at={time.time():.3f} waited={self.waited:.3f}\n"
                    )
                    self._fh.flush()
                except Exception:
                    pass
                return
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.release()
                    raise TimeoutError(
                        f"Timed out after {self.timeout:.0f}s waiting for "
                        f"provider request slot ({self.label}). Another Hermes "
                        f"session is still using the single-concurrency key."
                    )
                if not notified:
                    logger.info(
                        "provider_request_queue: waiting for %s slot "
                        "(single-concurrency key; timeout=%.0fs)",
                        self.label,
                        self.timeout,
                    )
                    notified = True
                time.sleep(min(0.5, max(0.05, remaining)))

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            self._fh.close()
        except Exception:
            pass
        self._fh = None


@contextmanager
def provider_request_slot(
    provider: Optional[str],
    *,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
    timeout: Optional[float] = None,
    enabled: Optional[bool] = None,
) -> Iterator[Optional[_FileSlot]]:
    """Serialize provider calls when max_concurrent_requests resolves to 1.

    Yields the slot object (or None when unlimited). Caller must keep the
    context open for the full stream/response lifetime.
    """
    max_c = resolve_max_concurrent(provider, base_url)
    if enabled is False or max_c != 1:
        # max_c > 1 not implemented yet — treat as unlimited for safety
        yield None
        return

    label = f"{provider or 'unknown'}:{model or ''}".rstrip(":")
    to = timeout if timeout is not None else resolve_queue_timeout_seconds(provider)
    slot = _FileSlot(_lock_path(provider, base_url), timeout=to, label=label)
    slot.acquire()
    try:
        yield slot
    finally:
        slot.release()


@contextmanager
def agent_provider_request_slot(agent: Any) -> Iterator[Optional[_FileSlot]]:
    """Convenience wrapper around a live AIAgent instance."""
    provider = getattr(agent, "provider", None)
    base_url = getattr(agent, "base_url", None)
    model = getattr(agent, "model", None)
    with provider_request_slot(provider, base_url=base_url, model=model) as slot:
        if slot is not None and slot.waited > 0.5:
            try:
                status = getattr(agent, "_buffer_status", None)
                if callable(status):
                    status(
                        f"⏳ Queued for {provider} "
                        f"(single-key concurrency; waited {slot.waited:.0f}s)..."
                    )
            except Exception:
                pass
        yield slot
