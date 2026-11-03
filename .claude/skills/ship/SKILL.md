---
name: ship
description: >-
  Take finished work in mtqt from a branch to a merged pull request: run
  scripts/check, confirm the changelog and docs rules, commit, push, open the
  PR, wait for CI, and hand it to the pr-reviewer agent. Use when the user asks
  to commit, push, open a PR, or ship a change.
argument-hint: "[PR title]"
---

# Ship a change

Invoking this skill is the user's request to commit, push, and open a PR. It
is not approval for anything else, such as repo settings or other branches.

## 1. Branch

Never commit on `main`. If you are on it, create a branch first:
`git switch -c <type>/<short-slug>`, where the type is `fix`, `feat`, `chore`, `refactor`, or `docs`.

## 2. Check

```bash
scripts/check
```

It runs the same list as CI's `check` job. Fix every failure and rerun it; don't skip a step. If it fails on the lockfile, run `uv lock`, then `uv sync --all-packages`.

## 3. Changelog and docs

- `CHANGELOG.md` gets an entry under `## Unreleased`, written for a user or contributor.
- When the diff touches `src/`, `examples/`, `apps/`, or `pyproject.toml`, update `docs/` for anything someone would notice.

The `gate` CI job enforces both, except for Dependabot PRs.

## 4. Commit

Stage tracked changes with `git add -u` and new files by name. Don't use `git add -A`: `.vscode/` is tracked but gitignored, so `-A` fails on it.

Write an imperative subject of 72 characters or fewer. In the body, say why the change was made, then end with the co-author trailer the harness gives you. The pre-commit hook runs ruff and pyright; if it rewrites files, stage them and commit again.

## 5. Push and open the PR

```bash
git push -u origin HEAD
gh pr create --base main --head <branch> --title "<title>" --body-file <file>
```

The body has a `## Summary`, any behavior changes, and a `## Testing` section that names what you actually ran and what you didn't (for example, no GPU window). End it with the PR attribution line the harness gives you.

Shell pitfalls from past runs:
- zsh does not split `$VAR` into words, so never put several flags in one variable.
- `uv pip list` ignores `UV_PROJECT_ENVIRONMENT`; pass `--python <env>/bin/python`.

## 6. Wait for CI

```bash
gh pr checks <number> --watch --interval 20
```

On a failure, read `gh run view <run-id> --log-failed`, fix it, push, and wait again. Report the failure honestly; don't paper over it.

## 7. Hand off to pr-reviewer

Spawn the `pr-reviewer` agent with the PR number and the specific risks to look at. Never merge it yourself: a PreToolUse hook blocks `gh pr merge` outside `pr-reviewer`, and `AGENTS.md` forbids it.

## 8. After the merge

```bash
git switch main && git pull --ff-only && git branch -D <branch>
gh run list --branch main --workflow CI --limit 1
```

Confirm CI passed on `main`, then report the PR link, what changed, and anything left open.
