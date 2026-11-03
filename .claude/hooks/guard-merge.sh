#!/usr/bin/env bash
# PreToolUse hook (Bash): only the pr-reviewer agent may merge a pull request,
# as AGENTS.md requires. Exit 2 blocks the call and shows stderr to Claude.
# A settings deny rule can't do this: deny rules also bind subagents.
set -euo pipefail

input=$(cat)
agent=$(jq -r '.agent_type // ""' <<<"$input")
[[ "$agent" == "pr-reviewer" ]] && exit 0

cmd=$(jq -r '.tool_input.command // ""' <<<"$input")
# `gh [global flags] pr merge`, or the REST merge endpoint via `gh api`.
merge_cli='(^|[[:space:];&|(])gh[[:space:]]([^;&|]*[[:space:]])?pr[[:space:]]+merge([[:space:]]|$)'
merge_api='pulls/[0-9]+/merge'
if grep -Eq "$merge_cli|$merge_api" <<<"$cmd"; then
  echo "Blocked: only the pr-reviewer agent merges PRs (AGENTS.md). Hand the PR to pr-reviewer." >&2
  exit 2
fi
exit 0
