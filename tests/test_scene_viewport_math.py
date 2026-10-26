"""Unit tests for scene viewport pivot math and history (no Qt/wgpu)."""

from __future__ import annotations

import pytest
from examples.scene_viewport.components import TransformComponent
from examples.scene_viewport.history import HistoryStep, SceneHistory, SceneObjects


def test_history_undo_redo() -> None:
    """History stacks invert transform edits."""
    history = SceneHistory()
    before = TransformComponent(name="a", position=(0.0, 0.0, 0.0))
    after = TransformComponent(name="a", position=(1.0, 0.0, 0.0))
    history.push(
        HistoryStep(
            label="move",
            transforms_before={"a": before},
            transforms_after={"a": after},
        )
    )
    step = history.undo()
    assert step is not None
    assert step.transforms_before["a"].position == (0.0, 0.0, 0.0)
    step = history.redo()
    assert step is not None
    assert step.transforms_after["a"].position == (1.0, 0.0, 0.0)


def test_history_create_payload() -> None:
    """Create steps retain added objects for undo."""
    history = SceneHistory()
    transform = TransformComponent(name="null1")
    history.push(
        HistoryStep(
            label="create",
            added=SceneObjects(transforms=[transform]),
        )
    )
    step = history.undo()
    assert step is not None
    assert step.added is not None
    assert step.added.transforms[0].name == "null1"


def test_trs_roundtrip() -> None:
    """Compose then decompose recovers position and scale."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("pylinalg")
    from examples.scene_viewport.xform_math import mat_from_trs, trs_from_mat

    matrix = mat_from_trs((1.0, 2.0, 3.0), (0.1, -0.2, 0.3), (2.0, 2.0, 2.0))
    position, _rotation, scale = trs_from_mat(matrix)
    assert np.allclose(position, (1.0, 2.0, 3.0))
    assert np.allclose(scale, (2.0, 2.0, 2.0))


def test_pivot_proxy_roundtrip() -> None:
    """Proxy at pivot maps back to the original transform matrix."""
    np = pytest.importorskip("numpy")
    pytest.importorskip("pylinalg")
    from examples.scene_viewport.xform_math import (
        mat_from_trs,
        pivot_world_matrix,
        transform_world_from_proxy,
    )

    transform = mat_from_trs((1.0, 0.0, 0.0), (0.0, 0.5, 0.0), (1.0, 1.0, 1.0))
    pivot = (0.5, 1.0, -0.25)
    proxy = pivot_world_matrix(transform, pivot)
    recovered = transform_world_from_proxy(proxy, pivot)
    assert np.allclose(transform, recovered, atol=1e-6)


def test_local_pivot_from_world_point() -> None:
    """World point under a translated transform is the local pivot."""
    pytest.importorskip("numpy")
    pytest.importorskip("pylinalg")
    from examples.scene_viewport.xform_math import (
        local_pivot_from_world_point,
        mat_from_trs,
    )

    transform = mat_from_trs((2.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    pivot = local_pivot_from_world_point(transform, (3.0, 1.0, 0.0))
    assert pivot == (1.0, 1.0, 0.0)
