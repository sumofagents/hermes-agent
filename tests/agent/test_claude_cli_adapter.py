from unittest.mock import patch

from agent.claude_cli_adapter import _build_claude_cli_command


def test_build_claude_cli_command_includes_effort_and_fallback_model():
    cmd, cwd = _build_claude_cli_command(
        prompt="Return exactly OK",
        model="claude-fable-5",
        max_turns=1,
        cwd="/tmp",
        effort="max",
        fallback_model="claude-opus-4-8",
    )

    assert cwd == "/tmp"
    assert "--model=claude-fable-5" in cmd
    assert "--effort=max" in cmd
    assert "--fallback-model=claude-opus-4-8" in cmd


def test_build_claude_cli_command_uses_configured_cli_path(monkeypatch):
    monkeypatch.delenv("HERMES_CLAUDE_CLI_PATH", raising=False)
    from hermes_cli.config import DEFAULT_CONFIG

    assert DEFAULT_CONFIG["delegation"]["claude_cli"]["path"] == ""
    with patch(
        "hermes_cli.config.load_config",
        return_value={"delegation": {"claude_cli": {"path": "/opt/custom/claude"}}},
    ):
        cmd, _cwd = _build_claude_cli_command(
            prompt="Return exactly OK",
            model="claude-fable-5",
        )

    assert cmd[0] == "/opt/custom/claude"
