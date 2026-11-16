"""Validation panel: run OpenUSD's ``UsdValidation`` validators, as usdchecker does.

:func:`validate_stage` returns plain :class:`Issue` rows; :class:`ValidationPanel`
lists them, selects the prim an issue is about, and applies a validator's
fixer to the current edit target when one exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pxr import Usd, UsdValidation
from PySide6.QtCore import Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from stage_editor.document import StageDocument

SEVERITY_COLORS = {
    "Error": QColor(232, 93, 93),
    "Warn": QColor(222, 158, 46),
    "Info": QColor(135, 206, 250),
}


@dataclass
class Issue:
    """One validation error, warning, or note.

    Attributes:
        severity: ``Error``, ``Warn``, ``Info``, or ``None``.
        name: The error's name, such as ``MissingMetersPerUnitMetadata``.
        validator: The validator that reported it.
        message: Human-readable explanation.
        paths: Prim or property paths the issue is about, as strings.
        fixable: A registered fixer can apply to the current edit target.
        error: The ``UsdValidation.ValidationError``, for fixing.
    """

    severity: str
    name: str
    validator: str
    message: str
    paths: list[str] = field(default_factory=list)
    fixable: bool = False
    error: Any = field(default=None, repr=False, compare=False)


def validator_keywords() -> list[str]:
    """Return the sorted keywords of every registered validator."""
    keywords: set[str] = set()
    for validator in UsdValidation.ValidationRegistry().GetOrLoadAllValidators():
        keywords.update(validator.GetMetadata().GetKeywords())
    return sorted(keywords)


def _fixers(error: Any) -> list[Any]:
    try:
        return list(error.GetFixers())
    except AttributeError:  # pragma: no cover - older OpenUSD without fixers
        return []


def validate_stage(
    stage: Usd.Stage, skip_keywords: frozenset[str] = frozenset()
) -> list[Issue]:
    """Run every registered validator on ``stage``.

    Args:
        stage: The stage to check.
        skip_keywords: Validators carrying only these keywords are skipped,
            for example ``UsdzValidators`` for a non-package scene.

    Returns:
        Issues sorted errors first, then by name.
    """
    validators = [
        v
        for v in UsdValidation.ValidationRegistry().GetOrLoadAllValidators()
        if not set(v.GetMetadata().GetKeywords()) <= skip_keywords
        or not v.GetMetadata().GetKeywords()
    ]
    errors = UsdValidation.ValidationContext(validators).Validate(stage)
    target = stage.GetEditTarget()
    issues = []
    for error in errors:
        if error.HasNoError():
            continue
        paths = []
        for site in error.GetSites():
            if site.IsProperty():
                paths.append(str(site.GetProperty().GetPath()))
            elif site.IsPrim():
                paths.append(str(site.GetPrim().GetPath()))
            elif site.GetLayer():
                paths.append(f"@{site.GetLayer().identifier}@")
        validator = error.GetValidator()
        fixable = any(
            fixer.CanApplyFix(error, target, Usd.TimeCode.Default())
            for fixer in _fixers(error)
        )
        issues.append(
            Issue(
                error.GetType().displayName,
                error.GetName(),
                validator.GetMetadata().name if validator else "",
                error.GetMessage(),
                paths,
                fixable,
                error,
            )
        )
    order = {"Error": 0, "Warn": 1, "Info": 2}
    issues.sort(key=lambda i: (order.get(i.severity, 3), i.name, i.paths))
    return issues


def apply_fix(stage: Usd.Stage, issue: Issue) -> bool:
    """Apply the first fixer that can repair ``issue`` to the edit target.

    Args:
        stage: The validated stage; its edit target receives the fix.
        issue: An issue from :func:`validate_stage`.

    Returns:
        Whether a fixer ran and reported success.
    """
    target = stage.GetEditTarget()
    time = Usd.TimeCode.Default()
    for fixer in _fixers(issue.error):
        if fixer.CanApplyFix(issue.error, target, time):
            return bool(fixer.ApplyFix(issue.error, target, time))
    return False


class ValidationPanel(QWidget):
    """Run the validators on demand and list what they report.

    Selecting an issue about a prim or property emits :attr:`pathsSelected`
    with the prim paths, so the window can select them.

    Signals:
        pathsSelected(list): Prim paths, as strings, of the clicked issue.

    Args:
        document: The document to validate.
        parent: Owning widget.
    """

    pathsSelected = Signal(list)

    def __init__(self, document: StageDocument, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._document = document
        self._issues: list[Issue] = []
        self._skip: set[str] = {"UsdzValidators", "UsdPhysicsValidators"}

        self._run = QPushButton("Validate", self)
        self._run.clicked.connect(self.run)
        self._fix = QPushButton("Apply Fix", self)
        self._fix.setEnabled(False)
        self._fix.clicked.connect(self._apply_fix)
        self._filter = QToolButton(self)
        self._filter.setText("Validators")
        self._filter.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self._filter)
        for keyword in validator_keywords():
            action = menu.addAction(keyword)
            action.setCheckable(True)
            action.setChecked(keyword not in self._skip)
            action.toggled.connect(lambda on, k=keyword: self._toggle_keyword(k, on))
        self._filter.setMenu(menu)
        self._summary = QLabel("Not run", self)

        self._tree = QTreeWidget(self)
        self._tree.setHeaderLabels(["Severity", "Issue", "Site", "Message"])
        self._tree.setRootIsDecorated(False)
        self._tree.setUniformRowHeights(True)
        self._tree.itemSelectionChanged.connect(self._on_selected)
        header = self._tree.header()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.resizeSection(0, 70)
        header.resizeSection(1, 220)
        header.resizeSection(2, 260)

        top = QHBoxLayout()
        top.setContentsMargins(4, 4, 4, 0)
        top.addWidget(self._run)
        top.addWidget(self._fix)
        top.addWidget(self._filter)
        top.addWidget(self._summary, 1)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self._tree)
        document.stageReplaced.connect(self._clear)

    @property
    def issues(self) -> list[Issue]:
        """The issues from the last run."""
        return list(self._issues)

    def run(self) -> None:
        """Validate the document's stage and list the issues."""
        self._issues = validate_stage(self._document.stage, frozenset(self._skip))
        self._tree.clear()
        for issue in self._issues:
            item = QTreeWidgetItem(
                [
                    issue.severity,
                    issue.name,
                    ", ".join(issue.paths),
                    issue.message,
                ]
            )
            color = SEVERITY_COLORS.get(issue.severity)
            if color is not None:
                item.setForeground(0, QBrush(color))
            item.setToolTip(3, issue.message)
            item.setToolTip(1, issue.validator)
            self._tree.addTopLevelItem(item)
        counts = {
            s: sum(i.severity == s for i in self._issues) for s in SEVERITY_COLORS
        }
        self._summary.setText(
            "No issues"
            if not self._issues
            else f"{counts['Error']} errors, {counts['Warn']} warnings, "
            f"{counts['Info']} notes"
        )

    def _clear(self) -> None:
        self._issues = []
        self._tree.clear()
        self._summary.setText("Not run")
        self._fix.setEnabled(False)

    def _toggle_keyword(self, keyword: str, on: bool) -> None:
        if on:
            self._skip.discard(keyword)
        else:
            self._skip.add(keyword)

    def _current(self) -> Issue | None:
        row = self._tree.indexOfTopLevelItem(self._tree.currentItem())
        return self._issues[row] if 0 <= row < len(self._issues) else None

    def _on_selected(self) -> None:
        issue = self._current()
        self._fix.setEnabled(issue is not None and issue.fixable)
        if issue is None:
            return
        prims = [
            p.split(".", 1)[0] for p in issue.paths if p.startswith("/") and p != "/"
        ]
        if prims:
            self.pathsSelected.emit(prims)

    def _apply_fix(self) -> None:
        issue = self._current()
        if issue is not None and apply_fix(self._document.stage, issue):
            self.run()
