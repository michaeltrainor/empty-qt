"""Tests for live properties-panel previews in the scene viewport window.

The GPU window is not opened: the handlers run against a stand-in ``self``.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

pytest.importorskip("pygfx")

from scene_viewport.components import (
    LightComponent,
    MeshComponent,
    TransformComponent,
)
from scene_viewport.window import SceneViewportWindow


class _Viewport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def apply_transform_local(self, component: TransformComponent) -> None:
        self.calls.append(("local", component))

    def apply_transform_world(self, name, position, rotation, scale):
        self.calls.append(("world", (name, position, rotation, scale)))
        return TransformComponent(name=name, position=(9.0, 9.0, 9.0))

    def set_pivot(self, name: str, pivot) -> None:
        self.calls.append(("pivot", (name, pivot)))

    def apply_mesh(self, component: MeshComponent) -> None:
        self.calls.append(("mesh", component))

    def apply_light(self, component: LightComponent) -> None:
        self.calls.append(("light", component))


def _owner(stored: TransformComponent) -> SimpleNamespace:
    owner = SimpleNamespace(
        _viewport=_Viewport(),
        _world=SimpleNamespace(transforms={stored.name: stored}, meshes={}, lights={}),
    )
    owner._apply_panel_trs = lambda model, base: SceneViewportWindow._apply_panel_trs(
        owner, model, base
    )
    return owner


def test_transform_preview_updates_viewport_not_store() -> None:
    stored = TransformComponent(name="pCube1")
    owner = _owner(stored)
    moved = stored.model_copy(update={"space": "local", "position": (1.0, 2.0, 3.0)})
    SceneViewportWindow._on_prop_preview(owner, "position", moved.position, moved)
    kind, pushed = owner._viewport.calls[-1]
    assert kind == "local"
    assert isinstance(pushed, TransformComponent)
    assert pushed.position == (1.0, 2.0, 3.0)
    # Store untouched: the release commit records undo against it.
    assert owner._world.transforms["pCube1"] is stored


def test_world_space_preview_uses_world_apply() -> None:
    stored = TransformComponent(name="pCube1")
    owner = _owner(stored)
    moved = stored.model_copy(update={"space": "world", "rotation": (0.5, 0.0, 0.0)})
    SceneViewportWindow._on_prop_preview(owner, "rotation", moved.rotation, moved)
    assert owner._viewport.calls[-1][0] == "world"
    assert owner._world.transforms["pCube1"] is stored


def test_pivot_mesh_and_light_previews() -> None:
    stored = TransformComponent(name="pCube1")
    owner = _owner(stored)
    pivoted = stored.model_copy(update={"pivot": (0.0, 1.0, 0.0)})
    SceneViewportWindow._on_prop_preview(owner, "pivot", pivoted.pivot, pivoted)
    assert owner._viewport.calls[-1] == ("pivot", ("pCube1", (0.0, 1.0, 0.0)))

    mesh = MeshComponent(name="pCubeShape1", transform="pCube1", kind="box")
    SceneViewportWindow._on_prop_preview(owner, "roughness", 0.2, mesh)
    assert owner._viewport.calls[-1] == ("mesh", mesh)
    assert owner._world.meshes == {}

    light = LightComponent(name="light1", transform="pCube1")
    SceneViewportWindow._on_prop_preview(owner, "intensity", 2.0, light)
    assert owner._viewport.calls[-1] == ("light", light)
    assert owner._world.lights == {}


def test_non_trs_transform_fields_do_not_preview() -> None:
    stored = TransformComponent(name="pCube1")
    owner = _owner(stored)
    SceneViewportWindow._on_prop_preview(owner, "visible", False, stored)
    assert owner._viewport.calls == []


def test_rotation_displays_in_degrees(qtbot) -> None:
    from mtqt import PropertiesPanel
    from mtqt.widgets.properties_panel import _VecEditor

    panel = PropertiesPanel()
    qtbot.addWidget(panel)
    panel.set_model(TransformComponent(name="pCube1", rotation=(math.pi / 2, 0.0, 0.0)))
    vec = panel._editors["rotation"]
    assert isinstance(vec, _VecEditor)
    assert vec._spins[0].value() == pytest.approx(90.0)
    assert vec._spins[0].suffix() == ""
    label = panel._form.labelForField(vec)
    assert label is not None
    assert label.text().endswith("(°)")
