"""Outliner drag-and-drop wiring: filter, store order, and undo.

The GPU window is never opened. Window methods run against a
``SimpleNamespace`` stand-in for ``self`` with a fake viewport.
"""

from __future__ import annotations

from types import SimpleNamespace

from mtqt import SceneGraphTree
from scene_viewport.components import (
    LightComponent,
    MeshComponent,
    TransformComponent,
)
from scene_viewport.history import HistoryStep, SceneHistory
from scene_viewport.scene import build_forest
from scene_viewport.window import SceneViewportWindow
from scene_viewport.world import SceneWorld

_LOCAL = ((1.0, 2.0, 3.0), (0.0, 90.0, 0.0), (1.0, 1.0, 1.0))


def _world() -> SceneWorld:
    return SceneWorld(
        transforms=[
            TransformComponent(name="key", position=(4.0, 8.0, 3.0)),
            TransformComponent(name="cube"),
            TransformComponent(name="sphere"),
            TransformComponent(name="child", parent="cube"),
        ],
        meshes=[
            MeshComponent(name="cubeShape", transform="cube", kind="box"),
            MeshComponent(name="sphereShape", transform="sphere", kind="sphere"),
        ],
        lights=[
            LightComponent(name="keyShape", transform="key", kind="directional"),
        ],
    )


def _window(qtbot, world: SceneWorld) -> SimpleNamespace:
    """Build a stand-in window around a real tree and a fake viewport."""
    tree = SceneGraphTree(
        build_forest(world.iter_transforms(), world.iter_meshes(), world.iter_lights()),
        drag_drop=True,
    )
    qtbot.addWidget(tree)
    calls: list[tuple[str, str]] = []

    def reparent_transform(name: str, parent: str):
        calls.append((name, parent))
        return _LOCAL

    ns = SimpleNamespace(
        _world=world,
        _tree=tree,
        _history=SceneHistory(),
        _viewport=SimpleNamespace(reparent_transform=reparent_transform),
        reparent_calls=calls,
        rebuilds=[],
        _stop_playback_for_edit=lambda names: False,
    )
    ns._snapshot_transform = lambda name: SceneViewportWindow._snapshot_transform(
        ns, name
    )
    ns._reparent_keep_world = lambda name, parent, before, after: (
        SceneViewportWindow._reparent_keep_world(ns, name, parent, before, after)
    )
    ns._tree_transform_order = lambda: SceneViewportWindow._tree_transform_order(ns)
    ns._rebuild_tree = lambda select=None: ns.rebuilds.append(list(select or []))
    tree.set_move_filter(
        lambda names, parent, row: SceneViewportWindow._accept_tree_move(
            ns, names, parent, row
        )
    )
    tree.nodesMoved.connect(
        lambda names, parent, row: SceneViewportWindow._on_tree_nodes_moved(
            ns, names, parent, row
        )
    )
    return ns


def test_world_reorder_transforms() -> None:
    world = _world()
    world.reorder_transforms(["sphere", "missing", "cube", "sphere"])
    assert list(world.transforms) == ["sphere", "cube", "key", "child"]
    assert world.to_document().transforms[0].name == "sphere"


def test_accept_tree_move_rules(qtbot) -> None:
    ns = _window(qtbot, _world())
    accept = ns._tree.model.can_move_nodes
    assert accept(["sphere"], "cube")
    assert accept(["sphere"], "geo", 0)
    assert accept(["key"], "lgt", 0)
    assert accept(["key"], "cube")
    # Folders, shapes, roots, and lights in geo (or meshes in lgt) are refused.
    assert not accept(["geo"], "lgt")
    assert not accept(["cubeShape"], "sphere")
    assert not accept(["sphere"], "world")
    assert not accept(["sphere"], "")
    assert not accept(["key"], "geo")
    assert not accept(["sphere"], "lgt")
    assert not accept(["sphere", "key"], "geo")


def test_tree_drop_reparents_keeps_world_and_records_undo(qtbot) -> None:
    world = _world()
    ns = _window(qtbot, world)
    assert ns._tree.model.move_nodes(["sphere"], "child")

    stored = world.transforms["sphere"]
    assert stored.parent == "child"
    assert stored.position == _LOCAL[0]
    assert stored.rotation == _LOCAL[1]
    assert ns.reparent_calls == [("sphere", "child")]
    # The store follows the outliner's top-down order: geo first, then lgt.
    assert list(world.transforms) == ["cube", "child", "sphere", "key"]

    step = ns._history.undo()
    assert isinstance(step, HistoryStep)
    assert step.label == "Move"
    assert step.transforms_before["sphere"].parent == ""
    assert step.transforms_after["sphere"].parent == "child"
    assert step.order_before == ["key", "cube", "sphere", "child"]
    assert step.order_after == ["cube", "child", "sphere", "key"]

    # The tree rebuild is deferred past the drop event.
    assert ns.rebuilds == []
    qtbot.waitUntil(lambda: ns.rebuilds == [["sphere"]])


def test_tree_drop_reorder_only_records_order(qtbot) -> None:
    world = _world()
    ns = _window(qtbot, world)
    assert ns._tree.model.move_nodes(["sphere"], "geo", 0)

    assert ns.reparent_calls == []
    assert world.transforms["sphere"].parent == ""
    assert list(world.transforms) == ["sphere", "cube", "child", "key"]
    step = ns._history.undo()
    assert step is not None
    assert step.transforms_before == step.transforms_after
    assert list(step.transforms_after) == ["sphere"]
    assert step.order_before == ["key", "cube", "sphere", "child"]
    assert step.order_after == ["sphere", "cube", "child", "key"]

    # Dropping a node where it already sits changes nothing.
    assert not ns._tree.model.move_nodes(["sphere"], "geo", 0)
    assert not ns._history.can_undo
    qtbot.waitUntil(lambda: ns.rebuilds == [["sphere"]])


def test_history_step_restores_order(qtbot) -> None:
    world = _world()
    applied: list[str] = []
    ns = SimpleNamespace(
        _world=world,
        _rebuild_tree=lambda select=None: None,
        _apply_stored_transform=lambda component: applied.append(component.name),
        _rename_transform=lambda old, new: None,
        _remove_objects=lambda objects: None,
        _restore_objects=lambda objects: None,
    )
    step = HistoryStep(
        label="Move",
        transforms_before={"sphere": world.transforms["sphere"]},
        transforms_after={"sphere": world.transforms["sphere"]},
        order_before=["key", "cube", "sphere", "child"],
        order_after=["sphere", "key", "cube", "child"],
    )
    SceneViewportWindow._apply_history_step(ns, step, undo=False)
    assert list(world.transforms) == ["sphere", "key", "cube", "child"]
    SceneViewportWindow._apply_history_step(ns, step, undo=True)
    assert list(world.transforms) == ["key", "cube", "sphere", "child"]
    assert applied == ["sphere", "sphere"]
