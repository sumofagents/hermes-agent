from pathlib import Path

from hermes_cli.config import DEFAULT_CONFIG
from agent.recall_gate import append_ephemeral_context_to_user_message

REPO = Path(__file__).resolve().parents[2]
RUN_AGENT = (REPO / "run_agent.py").read_text(encoding="utf-8")
MEMORY_MANAGER = (REPO / "agent" / "memory_manager.py").read_text(encoding="utf-8")
MEMORY_PROVIDER = (REPO / "agent" / "memory_provider.py").read_text(encoding="utf-8")


def test_default_config_enables_g2_kill_switch_by_default():
    assert DEFAULT_CONFIG["memory"]["first_turn_recall_enabled"] is True


def test_run_agent_initializes_g3_config_without_changing_g2_path():
    assert "_retrieval_routing_enabled" in RUN_AGENT
    assert "_retrieval_routing_cfg" in RUN_AGENT
    assert RUN_AGENT.index("_retrieval_routing_enabled") < RUN_AGENT.index("# Memory provider plugin")
    assert RUN_AGENT.count(".enforced_recall(") >= 1


def test_run_agent_wires_first_turn_recall_before_prefetch_and_injects_ephemerally():
    assert "_first_turn_recall_enabled" in RUN_AGENT
    assert ".enforced_recall(" in RUN_AGENT
    assert "_g2_recall_context" in RUN_AGENT
    assert "append_ephemeral_context_to_user_message" in RUN_AGENT

    idx_on_turn = RUN_AGENT.index(".on_turn_start(")
    idx_g2 = RUN_AGENT.index(".enforced_recall(")
    idx_prefetch = RUN_AGENT.index(".prefetch_all(")
    idx_inject = RUN_AGENT.index("if _g2_recall_context:")
    idx_helper = RUN_AGENT.index("append_ephemeral_context_to_user_message(api_msg, _injections)")

    assert idx_on_turn < idx_g2 < idx_prefetch
    assert idx_inject < idx_helper


def test_ephemeral_injection_helper_does_not_mutate_stored_user_message():
    stored = {"role": "user", "content": "same as before"}
    api_msg = stored.copy()
    out = append_ephemeral_context_to_user_message(api_msg, ["## Enforced Memory Recall\nSources:"])
    assert out is api_msg
    assert "## Enforced Memory Recall" in api_msg["content"]
    assert stored["content"] == "same as before"


def test_ephemeral_injection_helper_is_bit_identical_when_no_g2_context():
    stored = {"role": "user", "content": "what is 2+2?"}
    api_msg = stored.copy()
    before = dict(api_msg)
    out = append_ephemeral_context_to_user_message(api_msg, [""])
    assert out == before
    assert stored == before


def test_disabled_path_short_circuits_before_classification_or_manager_call():
    flag_idx = RUN_AGENT.index("if self._first_turn_recall_enabled:")
    call_idx = RUN_AGENT.index(".enforced_recall(")
    classify_idx = RUN_AGENT.index("classify_risk")
    assert flag_idx < call_idx
    assert flag_idx < classify_idx


def test_no_provider_mandatory_fallback_logs_needed_and_skipped_events():
    fallback_region = RUN_AGENT[RUN_AGENT.index("if not _g2_recall_context:"):RUN_AGENT.index("# External memory provider: prefetch once")]
    assert "append_feedback_event" in fallback_region
    assert 'event_type="recall_needed"' in fallback_region
    assert 'event_type="recall_skipped"' in fallback_region
    assert 'skip_reason="no_provider_or_provider_empty"' in fallback_region
    assert "context_sha256=_ctx_sha" in fallback_region


def test_memory_provider_and_manager_expose_enforced_recall_hook():
    assert "def enforced_recall(" in MEMORY_PROVIDER
    assert "def enforced_recall(" in MEMORY_MANAGER
    assert "provider.enforced_recall(" in MEMORY_MANAGER
