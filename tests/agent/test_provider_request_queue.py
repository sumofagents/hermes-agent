"""Tests for single-key provider request queue + CI concurrency classification."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from agent.error_classifier import FailoverReason, classify_api_error
from agent.provider_request_queue import (
    provider_request_slot,
    resolve_max_concurrent,
)


class _FakeErr(Exception):
    def __init__(self, status_code, message, code=None):
        super().__init__(message)
        self.status_code = status_code
        self.body = {
            "error": {
                "message": message,
                "type": "throttling_error",
                "code": code or "429",
            }
        }


def test_ci_concurrency_429_is_overloaded_not_immediate_fallback():
    err = _FakeErr(
        429,
        "Concurrency limit reached for this key. Finish an in-flight request and retry.",
    )
    classified = classify_api_error(err, provider="custom:cheapest-inference", model="kimi-k3")
    assert classified.reason == FailoverReason.overloaded
    assert classified.retryable is True
    # overloaded must NOT set should_fallback True on first sight
    assert classified.should_fallback is False
    assert classified.should_rotate_credential is False


def test_ci_invalid_key_403_transient_not_pool_poison():
    """CI intermittently 403s a VALID single-concurrency key under load.

    Must classify as transient overload (grace/queue retry, no pool
    exhaustion) instead of auth/non-retryable that marks the key exhausted
    and jumps to fallback.
    """
    err = _FakeErr(
        403,
        "Invalid or expired API key.",
        code="authentication_error",
    )
    # With base_url present -> transient overload, no rotation, no fallback
    classified = classify_api_error(
        err,
        provider="custom",
        model="kimi-k3",
        base_url="https://api.cheapestinference.com/v1",
    )
    assert classified.reason == FailoverReason.overloaded
    assert classified.retryable is True
    assert classified.should_rotate_credential is False
    assert classified.should_fallback is False
    # Without base_url (e.g. auxiliary path that didn't pass it) -> keep old
    # auth classification so a truly dead key still fails fast.
    classified2 = classify_api_error(err, provider="custom", model="kimi-k3")
    assert classified2.reason == FailoverReason.auth
    assert classified2.retryable is False


def test_ci_daily_window_429_still_rate_limit_fallback():
    err = _FakeErr(
        429,
        "This subscription's daily active window is 16:00–24:00 UTC. The next window opens in 4h.",
    )
    classified = classify_api_error(err, provider="cheapest-inference", model="kimi-k3")
    assert classified.reason == FailoverReason.rate_limit
    assert classified.should_fallback is True


def test_default_single_flight_for_ci_markers():
    assert resolve_max_concurrent("custom:cheapest-inference", "https://api.cheapestinference.com/v1") == 1
    assert resolve_max_concurrent("openai-codex", "https://chatgpt.com/backend-api/codex") == 0


def test_failover_grace_seconds_resolution(tmp_path, monkeypatch):
    import yaml
    from pathlib import Path as _P

    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "providers": {
                    "cheapest-inference": {
                        "failover_grace_seconds": 60,
                        "max_concurrent_requests": 1,
                    }
                }
            }
        )
    )
    from hermes_cli import config as hcfg

    # Point _load_provider_cfg's config source at the tmp config.
    monkeypatch.setattr(hcfg, "load_config", lambda *a, **k: yaml.safe_load(cfg_path.read_text()))

    from agent.provider_request_queue import resolve_failover_grace_seconds

    assert resolve_failover_grace_seconds("custom:cheapest-inference") == 60.0
    assert resolve_failover_grace_seconds("cheapest-inference") == 60.0
    # No config for openai-codex -> default 0 (behavior unchanged)
    assert resolve_failover_grace_seconds("openai-codex") == 0.0


def test_cross_thread_slot_serializes(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # force lock dir under tmp
    order: list[str] = []
    barrier = threading.Barrier(2)

    def worker(name: str, hold: float):
        barrier.wait()
        with provider_request_slot(
            "custom:cheapest-inference",
            base_url="https://api.cheapestinference.com/v1",
            model="kimi-k3",
            timeout=10.0,
            enabled=True,
        ):
            order.append(f"{name}:start")
            time.sleep(hold)
            order.append(f"{name}:end")

    t1 = threading.Thread(target=worker, args=("a", 0.4))
    t2 = threading.Thread(target=worker, args=("b", 0.05))
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)
    assert not t1.is_alive() and not t2.is_alive()
    # Fully serialized: one start/end pair completes before the other starts
    assert order in (
        ["a:start", "a:end", "b:start", "b:end"],
        ["b:start", "b:end", "a:start", "a:end"],
    )
    lock = tmp_path / "locks" / "provider-slot-cheapest-inference.lock"
    assert lock.exists()
