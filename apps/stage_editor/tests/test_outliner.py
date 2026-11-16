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


def test_filters_and_session_overrides(qtbot) -> None:
    document = StageDocument()
    outliner = Outliner(document)
    qtbot.addWidget(outliner)
    model = outliner.model
    stage = document.stage
    UsdGeom.Cube.Define(stage, "/World/A")
    UsdGeom.Cube.Define(stage, "/World/B")
    stage.OverridePrim("/World/Over")
    stage.CreateClassPrim("/_Class")
    document._set_edited(False)

    # Session overrides change what is drawn but never mark the scene unsaved.
    document.show_only(["/World/A"])
    assert model.is_visible("/World/A")
    assert not model.is_visible("/World/B")
    document.set_active(["/World/B"], False)
    document.set_dome_light(True)
    assert not document.has_unsaved_changes()
    assert stage.GetRootLayer().GetPrimAtPath("/World/B") is not None
    assert not stage.GetRootLayer().GetPrimAtPath("/World/B").HasInfo("active")
    assert not model.index_for_path("/_StageEditor").isValid()

    # Inactive prims list by default; overs and classes only when asked.
    assert model.index_for_path("/World/B").isValid()
    assert not model.index_for_path("/World/Over").isValid()
    assert not model.index_for_path("/_Class").isValid()
    prim_filter = model.filter
    prim_filter.inactive = False
    prim_filter.undefined = prim_filter.abstract = True
    model.set_filter(prim_filter)
    assert not model.index_for_path("/World/B").isValid()
    assert model.index_for_path("/World/Over").isValid()
    assert model.index_for_path("/_Class").isValid()

    document.clear_session_overrides(["/World"])
    assert model.is_visible("/World/B")
    assert stage.GetPrimAtPath("/World/B").IsActive()

    # Search keeps matches and their ancestors; selection maps through it.
    outliner.set_search("b")
    outliner.set_selected_paths(["/World/B"])
    assert outliner.selected_paths() == ["/World/B"]
    UsdGeom.Xform.Define(stage, "/World/C")  # a rebuild keeps the selection
    assert outliner.selected_paths() == ["/World/B"]
