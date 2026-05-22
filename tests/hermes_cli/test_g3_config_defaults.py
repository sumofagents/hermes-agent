from hermes_cli.config import DEFAULT_CONFIG


def test_g3_retrieval_routing_defaults_are_safe_and_rollbackable():
    memory_cfg = DEFAULT_CONFIG["memory"]

    assert memory_cfg["retrieval_routing_enabled"] is True
    routing = memory_cfg["retrieval_routing"]
    assert routing["llm_planner_enabled"] is False
    assert routing["char_budget"] == 3500
    assert routing["latency_budget_ms"] == 5000
    assert "memory_semantic" in routing["allowed_routes"]
    assert "web_search" in routing["allowed_routes"]
    assert "tool_recall" in routing["allowed_routes"]
