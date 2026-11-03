"""Tests for the Claude Code merge guard hook in .claude/hooks/guard_merge.py."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "guard_merge.py"


def _run(raw: str) -> int:
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=raw,
        capture_output=True,
        text=True,
        check=False,
    ).returncode


def _call(command: str, agent: str | None = None) -> int:
    payload: dict[str, object] = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    if agent is not None:
        payload["agent_type"] = agent
    return _run(json.dumps(payload))


@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 12 --squash",
        "gh --repo a/b pr merge 12",
        "gh -R a/b pr merge 12 --squash --delete-branch",
        "cd x && gh pr merge 3",
        "git status; gh pr merge 3",
        "echo ok\ngh pr merge 3",
        "GH_TOKEN=x gh pr merge 5",
        "/opt/homebrew/bin/gh pr merge 5",
        "gh api -X PUT repos/a/b/pulls/5/merge",
        'bash -c "gh pr merge 5"',
        "sh -c 'cd x && gh pr merge 5'",
        "eval gh pr merge 5",
    ],
)
def test_blocks_merges_outside_the_reviewer(command: str) -> None:
    assert _call(command) == 2


@pytest.mark.parametrize(
    "command",
    [
        "gh pr view 12",
        "gh pr checks 12 --watch",
        "gh pr list | grep merged",
        "git merge main",
        'echo "gh pr merge is blocked"',
        'git commit -m "docs: gh pr merge is blocked"',
        'gh pr review 5 --comment --body "the hook blocks gh pr merge outside"',
        "git commit -q -F - <<'EOF'\nBlock merges\n\ngh pr merge is reviewer-only\nEOF",
        "gh api repos/a/b/pulls/5",
        "uv run pytest -k merge",
    ],
)
def test_allows_commands_that_only_mention_merging(command: str) -> None:
    assert _call(command) == 0


def test_allows_the_reviewer_to_merge() -> None:
    assert _call("gh pr merge 12 --squash", agent="pr-reviewer") == 0


def test_other_agents_are_blocked() -> None:
    assert _call("gh pr merge 12", agent="general-purpose") == 2


def test_fails_closed_on_unparseable_merge_command() -> None:
    assert _call('gh pr merge 5 --body "unbalanced') == 2


def test_unparseable_command_without_merge_is_allowed() -> None:
    assert _call('echo "unbalanced') == 0


@pytest.mark.parametrize("raw", ["not json, gh pr merge 5", '{"tool_input": "merge"'])
def test_fails_closed_on_malformed_input_mentioning_merge(raw: str) -> None:
    assert _run(raw) == 2


def test_malformed_input_without_merge_is_allowed() -> None:
    assert _run("not json") == 0
