"""PreToolUse hook (Bash): only the pr-reviewer agent may merge a pull request.

AGENTS.md requires this. A settings deny rule can't enforce it, because deny
rules also bind subagents, so this hook checks the caller's ``agent_type``.

Exit 2 blocks the call and shows stderr to Claude. Exit 0 allows it.

The command is split with real shell tokenizing, so quoted text, commit
messages, and heredoc bodies that mention merging are not blocked. It also
looks inside ``bash -c``, ``sh -c``, ``zsh -c``, and ``eval``. When a command
mentions "merge" but can't be parsed, the hook blocks it (fails closed).

This is a guardrail, not a security boundary. A merge run from a script file,
an alias, or a pipeline built at runtime is not detected. Merges you run
yourself in a terminal are never affected.
"""

from __future__ import annotations

import json
import re
import shlex
import sys

REVIEWER = "pr-reviewer"
OPERATORS = {";", "&", "&&", "|", "||", "(", ")", "\n", ";;", "|&"}
SHELLS = {"bash", "sh", "zsh", "dash"}
GH_FLAGS_WITH_VALUE = {"-R", "--repo", "--hostname"}
HEREDOC = re.compile(
    r"<<-?[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n.*?^[ \t]*\2[ \t]*$",
    re.DOTALL | re.MULTILINE,
)
API_MERGE = re.compile(r"pulls/\d+/merge\b")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


class Unparseable(Exception):
    """The command could not be tokenized."""


def _tokens(command: str) -> list[str]:
    """Split a command into words and operator tokens, keeping quotes intact.

    Raises:
        Unparseable: If the command has unbalanced quotes.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError as error:
        raise Unparseable(str(error)) from error


def _segments(tokens: list[str]) -> list[list[str]]:
    """Group tokens into simple commands, split on shell operators."""
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in OPERATORS:
            segments.append([])
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def _is_gh_merge(words: list[str]) -> bool:
    """Return True for ``gh [flags] pr merge`` or a ``gh api`` merge call."""
    if not words or words[0].rsplit("/", 1)[-1] != "gh":
        return False
    i = 1
    while i < len(words) and words[i].startswith("-"):
        i += 2 if words[i] in GH_FLAGS_WITH_VALUE else 1
    rest = words[i:]
    if rest[:2] == ["pr", "merge"]:
        return True
    return rest[:1] == ["api"] and any(API_MERGE.search(word) for word in rest)


def is_merge(command: str, depth: int = 0) -> bool:
    """Return True when ``command`` would merge a pull request.

    Raises:
        Unparseable: If the command mentions merging and can't be tokenized.
    """
    if "merge" not in command or depth > 3:
        return False
    stripped = HEREDOC.sub("", command)
    for words in _segments(_tokens(stripped)):
        while words and ASSIGNMENT.match(words[0]):
            words = words[1:]
        if not words:
            continue
        program = words[0].rsplit("/", 1)[-1]
        if program in SHELLS and "-c" in words[1:]:
            index = words.index("-c")
            if index + 1 < len(words) and is_merge(words[index + 1], depth + 1):
                return True
        elif program == "eval":
            if is_merge(" ".join(words[1:]), depth + 1):
                return True
        elif _is_gh_merge(words):
            return True
    return False


def main() -> int:
    """Read the hook payload from stdin and return the hook exit code."""
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
        command = str(payload.get("tool_input", {}).get("command", ""))
        agent = str(payload.get("agent_type") or "")
    except (json.JSONDecodeError, AttributeError):
        if "merge" in raw:
            print(
                "Blocked: couldn't read the hook input to check a merge.",
                file=sys.stderr,
            )
            return 2
        return 0
    if agent == REVIEWER:
        return 0
    try:
        merging = is_merge(command)
    except Unparseable:
        print(
            "Blocked: this command mentions merging and couldn't be parsed. "
            "Only the pr-reviewer agent merges PRs (AGENTS.md).",
            file=sys.stderr,
        )
        return 2
    if merging:
        print(
            "Blocked: only the pr-reviewer agent merges PRs (AGENTS.md). "
            "Hand the PR to pr-reviewer.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
