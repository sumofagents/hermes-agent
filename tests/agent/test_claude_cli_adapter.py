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
