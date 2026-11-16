"""PrimModel and Outliner follow the stage and round-trip path selection."""

from __future__ import annotations

from pxr import UsdGeom
from PySide6.QtCore import QModelIndex, Qt

from stage_editor.document import StageDocument
from stage_editor.outliner import Outliner


def _names(model, parent=QModelIndex()) -> list[str]:
    return [
        model.index(row, 0, parent).data(Qt.ItemDataRole.DisplayRole)
        for row in range(model.rowCount(parent))
    ]


def test_model_mirrors_prims_and_follows_edits(qtbot) -> None:
    document = StageDocument()
    outliner = Outliner(document)
    qtbot.addWidget(outliner)
    model = outliner.model
    assert _names(model) == ["World"]

    UsdGeom.Xform.Define(document.stage, "/World/Group")
    UsdGeom.Cube.Define(document.stage, "/World/Group/Box")
    world = model.index_for_path("/World")
    assert _names(model, world) == ["Group"]
    box = model.index_for_path("/World/Group/Box")
    assert box.isValid()
    assert model.path_at(box) == "/World/Group/Box"
    assert box.siblingAtColumn(1).data(Qt.ItemDataRole.DisplayRole) == "Cube"
    assert model.parent(box) == model.index_for_path("/World/Group")

    document.stage.RemovePrim("/World/Group/Box")
    assert not model.index_for_path("/World/Group/Box").isValid()
    assert model.rowCount(model.index_for_path("/World/Group")) == 0

    outliner.set_selected_paths(["/World/Group", "/missing"])
    assert outliner.selected_paths() == ["/World/Group"]

    document.new()
    assert _names(model) == ["World"]
    assert outliner.selected_paths() == []
