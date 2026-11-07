"""Recent files store, startup arguments, and the open / discard logic.

The GPU window is never opened. Window methods run against a
``SimpleNamespace`` stand-in for ``self``.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from scene_viewport.components import TransformComponent
from scene_viewport.recent_files import RecentFiles
from scene_viewport.scene_io import SceneDocument
from scene_viewport.window import SceneViewportWindow, startup_paths
from scene_viewport.world import SceneWorld


def test_recent_files_order_dedupe_and_limit(tmp_path: Path) -> None:
    recent = RecentFiles(limit=3)
    recent.add(tmp_path / "a.json")
    recent.add(tmp_path / "b.glb")
    recent.add(tmp_path / "a.json")  # moves to the front, no duplicate
    assert [p.name for p in recent.paths] == ["a.json", "b.glb"]
    recent.add(tmp_path / "c.obj")
    recent.add(tmp_path / "d.stl")
    assert [p.name for p in recent.paths] == ["d.stl", "c.obj", "a.json"]
    recent.remove(tmp_path / "c.obj")
    assert [p.name for p in recent.paths] == ["d.stl", "a.json"]
    assert recent.paths[0].is_absolute()
    recent.clear()
    assert recent.paths == []


def test_recent_files_roundtrip_and_bad_file(tmp_path: Path) -> None:
    store = tmp_path / "state" / "recent.json"
    recent = RecentFiles([tmp_path / "x.json", tmp_path / "y.gltf"])
    assert recent.save(store) == store
    loaded = RecentFiles.load(store)
    assert loaded.paths == recent.paths
    assert RecentFiles.load(tmp_path / "missing.json").paths == []
    store.write_text("not json", encoding="utf-8")
    assert RecentFiles.load(store).paths == []
    store.write_text('{"a": 1}', encoding="utf-8")
    assert RecentFiles.load(store).paths == []
    store.write_text('["ok.json", 3, ""]', encoding="utf-8")
    assert [p.name for p in RecentFiles.load(store).paths] == ["ok.json"]


def test_startup_paths_keeps_scenes_and_models(tmp_path: Path) -> None:
    args = ["scene.json", "Duck.glb", "notes.txt", "mesh.OBJ", "--flag"]
    assert [p.name for p in startup_paths(args)] == [
        "scene.json",
        "Duck.glb",
        "mesh.OBJ",
    ]
    assert startup_paths([]) == []


def _window(world: SceneWorld, saved: SceneDocument | None) -> SimpleNamespace:
    ns = SimpleNamespace(_world=world, _saved_document=saved)
    ns._has_unsaved_changes = lambda: SceneViewportWindow._has_unsaved_changes(ns)
    return ns


def test_unsaved_changes_compare_against_the_saved_snapshot() -> None:
    world = SceneWorld(transforms=[TransformComponent(name="a")])
    # No snapshot yet (startup): never dirty.
    assert not _window(world, None)._has_unsaved_changes()
    snapshot = world.to_document()
    ns = _window(world, snapshot)
    assert not ns._has_unsaved_changes()
    world.set_transform(
        world.transforms["a"].model_copy(update={"position": (1.0, 0, 0)})
    )
    assert ns._has_unsaved_changes()
    ns._saved_document = world.to_document()
    assert not ns._has_unsaved_changes()


def test_confirm_discard_skips_the_prompt_when_clean() -> None:
    world = SceneWorld(transforms=[TransformComponent(name="a")])
    ns = _window(world, world.to_document())
    assert SceneViewportWindow._confirm_discard(ns, "Open")  # type: ignore[arg-type]
