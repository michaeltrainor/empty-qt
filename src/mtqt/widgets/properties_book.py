"""Tabbed stack of :class:`~mtqt.PropertiesPanel` pages for multi-component objects."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QTabWidget, QVBoxLayout, QWidget

from mtqt.widgets.properties_panel import EditableCheck, FieldHandler, PropertiesPanel


class PropertiesBook(QWidget):
    """Tabs of :class:`PropertiesPanel` pages sharing handlers and editability.

    Use this when one selected object exposes several pydantic components
    (transform, mesh, light, …). Each page is an independent panel; commits
    emit the same signals as a single panel, with the page's model as the
    third ``fieldChanged`` argument.

    Args:
        parent: Qt parent widget.
        empty_message: Placeholder when no pages are set.
    """

    modelChanged = Signal(object)
    fieldChanged = Signal(str, object, object)
    fieldPreviewed = Signal(str, object, object)
    validationFailed = Signal(str, str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        empty_message: str = "No properties",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("PropertiesBook")
        self.setAccessibleName("Properties")
        self._empty_message = empty_message
        self._editable_check: EditableCheck | None = None
        self._editable_overrides: dict[str, bool] = {}
        self._handlers: list[FieldHandler] = []
        self._field_handlers: dict[str, list[FieldHandler]] = {}
        self._panels: list[PropertiesPanel] = []
        self._titles: list[str] = []

        self._empty = QLabel(empty_message, self)
        self._empty.setWordWrap(True)
        self._empty.setObjectName("PropertiesPanelEmpty")

        self._tabs = QTabWidget(self)
        self._tabs.setObjectName("PropertiesBookTabs")
        self._tabs.setDocumentMode(True)
        self._tabs.setMovable(False)
        self._tabs.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._empty)
        layout.addWidget(self._tabs, stretch=1)
        self._show_empty()

    @property
    def model(self) -> BaseModel | None:
        """Model on the current tab, or ``None`` when empty."""
        panel = self._current_panel()
        return None if panel is None else panel.model

    @property
    def models(self) -> list[BaseModel]:
        """Models on every page, in tab order."""
        return [panel.model for panel in self._panels if panel.model is not None]

    def set_editable_check(self, check: EditableCheck | None) -> None:
        """Apply an editability hook to every page.

        Args:
            check: Callback ``(field_name, model) -> bool``, or ``None``.
        """
        self._editable_check = check
        for panel in self._panels:
            panel.set_editable_check(check)

    def set_field_editable(self, field_name: str, editable: bool) -> None:
        """Force a field editable or read-only on every page.

        Args:
            field_name: Model field name.
            editable: ``True`` to allow edits, ``False`` to lock the field.
        """
        self._editable_overrides[field_name] = editable
        for panel in self._panels:
            panel.set_field_editable(field_name, editable)

    def clear_field_editable(self, field_name: str | None = None) -> None:
        """Clear one field override, or all overrides when omitted.

        Args:
            field_name: Field to clear, or ``None`` for every override.
        """
        if field_name is None:
            self._editable_overrides.clear()
        else:
            self._editable_overrides.pop(field_name, None)
        for panel in self._panels:
            panel.clear_field_editable(field_name)

    def register_handler(self, handler: FieldHandler) -> None:
        """Register a commit handler on every current and future page.

        Args:
            handler: ``handler(field_name, value, model)`` after a successful commit.
        """
        self._handlers.append(handler)
        for panel in self._panels:
            panel.register_handler(handler)

    def unregister_handler(self, handler: FieldHandler) -> None:
        """Remove a global handler from every page.

        Args:
            handler: Callback passed to :meth:`register_handler`.
        """
        self._handlers = [item for item in self._handlers if item is not handler]
        for panel in self._panels:
            panel.unregister_handler(handler)

    def register_field_handler(self, field_name: str, handler: FieldHandler) -> None:
        """Register a per-field commit handler on every page.

        Args:
            field_name: Model field name.
            handler: Callback after a successful commit of that field.
        """
        self._field_handlers.setdefault(field_name, []).append(handler)
        for panel in self._panels:
            panel.register_field_handler(field_name, handler)

    def unregister_field_handler(
        self,
        field_name: str,
        handler: FieldHandler | None = None,
    ) -> None:
        """Remove a field handler from every page.

        Args:
            field_name: Model field name.
            handler: Specific callback, or ``None`` to clear all for the field.
        """
        if handler is None:
            self._field_handlers.pop(field_name, None)
        else:
            current = self._field_handlers.get(field_name, [])
            self._field_handlers[field_name] = [
                item for item in current if item is not handler
            ]
            if not self._field_handlers[field_name]:
                self._field_handlers.pop(field_name, None)
        for panel in self._panels:
            panel.unregister_field_handler(field_name, handler)

    def set_model(self, model: BaseModel | None) -> None:
        """Show a single page (no tab bar) or clear when ``model`` is ``None``.

        Args:
            model: Pydantic model instance, or ``None`` to show the empty state.
        """
        if model is None:
            self.set_pages(None)
            return
        self.set_pages([("Properties", model)])

    def set_pages(self, pages: Sequence[tuple[str, BaseModel]] | None) -> None:
        """Replace all tabs with ``(title, model)`` pages.

        When ``pages`` is empty or ``None``, shows the empty placeholder.
        A single page hides the tab bar; two or more pages show tabs.

        Args:
            pages: Ordered ``(tab_title, model)`` pairs, or ``None`` to clear.
        """
        self._clear_pages()
        if not pages:
            self._show_empty()
            return

        self._empty.hide()
        self._tabs.show()
        for title, model in pages:
            panel = PropertiesPanel(self._tabs, empty_message=self._empty_message)
            panel.set_editable_check(self._editable_check)
            for field_name, editable in self._editable_overrides.items():
                panel.set_field_editable(field_name, editable)
            for handler in self._handlers:
                panel.register_handler(handler)
            for field_name, handlers in self._field_handlers.items():
                for handler in handlers:
                    panel.register_field_handler(field_name, handler)
            panel.modelChanged.connect(self.modelChanged.emit)
            panel.fieldChanged.connect(self.fieldChanged.emit)
            panel.fieldPreviewed.connect(self.fieldPreviewed.emit)
            panel.validationFailed.connect(self.validationFailed.emit)
            panel.set_model(model)
            self._panels.append(panel)
            self._titles.append(title)
            self._tabs.addTab(panel, title)

        hide_bar = len(self._panels) == 1
        self._tabs.tabBar().setVisible(not hide_bar)
        self._tabs.setCurrentIndex(0)

    def clear(self) -> None:
        """Remove every page and show the empty placeholder."""
        self.set_pages(None)

    def _current_panel(self) -> PropertiesPanel | None:
        if not self._panels:
            return None
        index = self._tabs.currentIndex()
        if index < 0 or index >= len(self._panels):
            return self._panels[0]
        return self._panels[index]

    def _clear_pages(self) -> None:
        while self._tabs.count():
            self._tabs.removeTab(0)
        for panel in self._panels:
            panel.deleteLater()
        self._panels.clear()
        self._titles.clear()

    def _show_empty(self) -> None:
        self._empty.setText(self._empty_message)
        self._empty.show()
        self._tabs.hide()
