# mtqt

Reusable PySide6 widgets, item models, and QSS. Library code lives in `src/mtqt`.

## PySide6

Ownership, signal re-entrancy, deferred rebuilds, threading, and SIGSEGV
triage: project skill `.cursor/skills/pyside6/` (read `SKILL.md` when
writing or debugging Qt/PySide6 code).

## Docstrings

Google-style docstrings on public classes, functions, and methods that are mtqt's own API. Start with a summary line, then `Args`, `Returns`, and `Raises` when they apply. Match `src/mtqt/widgets/labeled_line_edit.py`.

Qt virtual overrides (`paintEvent`, `rowCount`, `data`, and similar) do not need a docstring when they only implement the Qt contract. `__init__` is documented on the class, not on the method.

Ruff enforces this with pydocstyle convention `google` (see `pyproject.toml`). `D102` is ignored because Qt overrides are public methods. The review agent still checks new public Python methods.

## Tests

Every public widget gets pytest-qt tests under `tests/`. Use the `qtbot` fixture. `tests/conftest.py` sets `QT_QPA_PLATFORM=offscreen`. Do not construct `QApplication` in tests.

```bash
uv run pytest
```

## Lint

```bash
uv run ruff check
uv run ruff format
```

Ruff select is `E`, `F`, `I`, `UP`, and `D`. `uv run pyright` (mode `standard`) is a local check. It is not a required CI job.

## Docs

When a PR is marked ready for review, update the MkDocs pages under `docs/` for anything a user or contributor would notice (new widgets, examples, commands, or the PR process). `mkdocs.yml` sets `docstring_style: google`.

```bash
uv sync --extra dev --extra docs
uv run mkdocs build --strict
```

## Changelog

Every PR that is merged updates `CHANGELOG.md` under `## Unreleased` in the same PR, before merge. Do not add a follow-up commit after merge.

## Review and merge

When a pull request is ready for review (not a draft), the `pr-reviewer` agent reviews it and squash-merges it if the review passes. The parent agent must not merge a ready PR itself in place of `pr-reviewer`. Spawn or follow the project agent named `pr-reviewer`.

## Node graph

Do not add a node-type plugin loader, evaluator, or palette inside mtqt. Node catalogs belong in the host tool.
