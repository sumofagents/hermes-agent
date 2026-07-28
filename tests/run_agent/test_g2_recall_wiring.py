from pathlib import Path

from hermes_cli.config import DEFAULT_CONFIG
from agent.recall_gate import append_ephemeral_context_to_user_message

REPO = Path(__file__).resolve().parents[2]
CONVERSATION_LOOP = (REPO / "agent" / "conversation_loop.py").read_text(encoding="utf-8")
TURN_CONTEXT = (REPO / "agent" / "turn_context.py").read_text(encoding="utf-8")
AGENT_INIT = (REPO / "agent" / "agent_init.py").read_text(encoding="utf-8")
MEMORY_MANAGER = (REPO / "agent" / "memory_manager.py").read_text(encoding="utf-8")
MEMORY_PROVIDER = (REPO / "agent" / "memory_provider.py").read_text(encoding="utf-8")


def test_default_config_enables_g2_kill_switch_by_default():
    assert DEFAULT_CONFIG["memory"]["first_turn_recall_enabled"] is True


def test_run_agent_initializes_g3_config_without_changing_g2_path():
    assert "_retrieval_routing_enabled" in AGENT_INIT
    assert "_retrieval_routing_cfg" in AGENT_INIT
    assert AGENT_INIT.index("_retrieval_routing_enabled") < AGENT_INIT.index("# Memory provider plugin")
    assert TURN_CONTEXT.count(".enforced_recall(") >= 1


def test_run_agent_wires_first_turn_recall_before_prefetch_and_injects_ephemerally():
    assert "_first_turn_recall_enabled" in AGENT_INIT
    assert ".enforced_recall(" in TURN_CONTEXT
    assert "g2_recall_context" in TURN_CONTEXT
    assert "_g2_recall_context" in CONVERSATION_LOOP
    assert "append_ephemeral_context_to_user_message" in CONVERSATION_LOOP

    idx_on_turn = TURN_CONTEXT.index(".on_turn_start(")
    idx_g2 = TURN_CONTEXT.index(".enforced_recall(")
    idx_prefetch = TURN_CONTEXT.index(".prefetch_all(")
    idx_unpack = CONVERSATION_LOOP.index("_g2_recall_context = _ctx.g2_recall_context")
    idx_inject = CONVERSATION_LOOP.index("if _g2_recall_context:")
    # HEAD (post v2026.7.20 upgrade) injects the single recall context directly
    # via [_g2_recall_context] rather than the older _injections list. Both forms
    # preserve the ephemeral-injection invariant; the call signature changed when
    # the byte-stable compose_user_api_content path landed upstream.
    idx_helper = CONVERSATION_LOOP.index(
        "append_ephemeral_context_to_user_message(api_msg, [_g2_recall_context])"
    )

    assert idx_on_turn < idx_g2 < idx_prefetch
    assert idx_unpack < idx_inject < idx_helper


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
    flag_idx = TURN_CONTEXT.index('if getattr(agent, "_first_turn_recall_enabled", False):')
    call_idx = TURN_CONTEXT.index(".enforced_recall(")
    classify_idx = TURN_CONTEXT.index("classify_risk")
    assert flag_idx < call_idx
    assert flag_idx < classify_idx


def test_no_provider_mandatory_fallback_logs_needed_and_skipped_events():
    fallback_region = TURN_CONTEXT[
        TURN_CONTEXT.index("if not g2_recall_context:"):
        TURN_CONTEXT.index("# External memory provider: prefetch once")
    ]
    assert "append_feedback_event" in fallback_region
    assert 'event_type="recall_needed"' in fallback_region
    assert 'event_type="recall_skipped"' in fallback_region
    assert 'skip_reason="no_provider_or_provider_empty"' in fallback_region
    assert "context_sha256=_ctx_sha" in fallback_region


def test_memory_provider_and_manager_expose_enforced_recall_hook():
    assert "def enforced_recall(" in MEMORY_PROVIDER
    assert "def enforced_recall(" in MEMORY_MANAGER
    assert "provider.enforced_recall(" in MEMORY_MANAGER
