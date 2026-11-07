"""Animation clips: glTF reader, document model, world, import, and sampling.

No GPU window is opened. The glTF files are built by hand with embedded
buffers, so each interpolation mode and the skipped channel kinds are
covered without sample assets.
"""

from __future__ import annotations

import base64
import json
import math
import struct
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("trimesh")

from scene_viewport.animation import AnimationChannel, AnimationClip
from scene_viewport.animation_player import (
    ClipSampler,
    PlaybackState,
    SampledTrs,
    hermite,
    posed_transform,
    quat_slerp,
)
from scene_viewport.components import MeshComponent, TransformComponent
from scene_viewport.history import SceneObjects
from scene_viewport.mesh.gltf_extras import GltfDocument, read_animations
from scene_viewport.mesh.importer import load_model
from scene_viewport.model_import import import_objects
from scene_viewport.scene_io import SceneDocument, load_scene, save_scene
from scene_viewport.world import SceneWorld, prune_clip, unique_clip_name


def _pack(values: list[float], fmt: str) -> bytes:
    return struct.pack(f"<{len(values)}{fmt}", *values)


def _animated_gltf(path: Path) -> Path:
    """Write a glTF with two cubes and every channel kind on them.

    Node 0 ``Body`` has a LINEAR translation and a STEP scale; node 1
    ``Wheel`` (child of ``Body``) has a LINEAR rotation stored as normalized
    int16 and a CUBICSPLINE translation. A second animation animates
    morph ``weights`` and a ``KHR_animation_pointer`` target only, and a
    third names a node that does not exist.
    """
    times = [0.0, 1.0, 2.0]
    translation = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 2.0, 0.0]
    scale = [1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 3.0, 3.0, 3.0]
    half = math.sqrt(0.5)
    # Identity, 90 degrees about Y, 180 degrees about Y, as int16 normalized.
    rotation = [0.0, 0.0, 0.0, 1.0, 0.0, half, 0.0, half, 0.0, 1.0, 0.0, 0.0]
    rotation_int16 = [round(v * 32767) for v in rotation]
    # Cubic: in-tangent, value, out-tangent per key; a straight line in X.
    cubic = []
    for key, x in enumerate([0.0, 1.0, 2.0]):
        cubic += [1.0, 0.0, 0.0, x, 0.0, 0.0, 1.0, 0.0, 0.0]
        del key
    weights = [0.0, 1.0, 0.0]
    cube = [-0.5, -0.5, -0.5, 0.5, -0.5, -0.5, 0.5, 0.5, -0.5, -0.5, 0.5, -0.5]
    indices = [0, 1, 2, 0, 2, 3]
    blobs = [
        _pack(times, "f"),
        _pack(translation, "f"),
        _pack(scale, "f"),
        _pack(rotation_int16, "h"),
        _pack(cubic, "f"),
        _pack(weights, "f"),
        _pack(cube, "f"),
        _pack(indices, "H"),
    ]
    views = []
    offset = 0
    buffer = b""
    for blob in blobs:
        views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(blob)})
        buffer += blob + b"\0" * (-len(blob) % 4)
        offset = len(buffer)
    accessors = [
        {"bufferView": 0, "componentType": 5126, "count": 3, "type": "SCALAR"},
        {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3"},
        {"bufferView": 2, "componentType": 5126, "count": 3, "type": "VEC3"},
        {
            "bufferView": 3,
            "componentType": 5122,
            "count": 3,
            "type": "VEC4",
            "normalized": True,
        },
        {"bufferView": 4, "componentType": 5126, "count": 9, "type": "VEC3"},
        {"bufferView": 5, "componentType": 5126, "count": 3, "type": "SCALAR"},
        {
            "bufferView": 6,
            "componentType": 5126,
            "count": 4,
            "type": "VEC3",
            "min": [-0.5, -0.5, -0.5],
            "max": [0.5, 0.5, -0.5],
        },
        {"bufferView": 7, "componentType": 5123, "count": 6, "type": "SCALAR"},
    ]
    document = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [
            {"name": "Body", "mesh": 0, "children": [1]},
            {"name": "Wheel", "mesh": 0, "translation": [0.0, -1.0, 0.0]},
        ],
        "skins": [{"joints": [1]}],
        "meshes": [
            {
                "name": "Quad",
                "primitives": [{"attributes": {"POSITION": 6}, "indices": 7}],
            }
        ],
        "animations": [
            {
                "name": "Drive",
                "channels": [
                    {"sampler": 0, "target": {"node": 0, "path": "translation"}},
                    {"sampler": 1, "target": {"node": 0, "path": "scale"}},
                    {"sampler": 2, "target": {"node": 1, "path": "rotation"}},
                    {"sampler": 3, "target": {"node": 1, "path": "translation"}},
                ],
                "samplers": [
                    {"input": 0, "output": 1, "interpolation": "LINEAR"},
                    {"input": 0, "output": 2, "interpolation": "STEP"},
                    {"input": 0, "output": 3},
                    {"input": 0, "output": 4, "interpolation": "CUBICSPLINE"},
                ],
            },
            {
                "name": "Morph",
                "channels": [
                    {"sampler": 0, "target": {"node": 0, "path": "weights"}},
                    {"sampler": 0, "target": {"node": 0, "path": "weights"}},
                    {
                        "sampler": 0,
                        "target": {
                            "path": "pointer",
                            "extensions": {
                                "KHR_animation_pointer": {"pointer": "/nodes/0/..."}
                            },
                        },
                    },
                ],
                "samplers": [{"input": 0, "output": 5}],
            },
            {
                "channels": [{"sampler": 0, "target": {"node": 7, "path": "scale"}}],
                "samplers": [{"input": 0, "output": 2}],
            },
        ],
        "buffers": [
            {
                "byteLength": len(buffer),
                "uri": "data:application/octet-stream;base64,"
                + base64.b64encode(buffer).decode("ascii"),
            }
        ],
        "bufferViews": views,
        "accessors": accessors,
    }
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_read_animations_reads_trs_channels_and_skips_the_rest(tmp_path: Path) -> None:
    document = GltfDocument(_animated_gltf(tmp_path / "anim.gltf"))
    drive, morph, nameless = read_animations(document)
    assert drive.name == "Drive"
    assert drive.skipped == ()
    paths = [(c.node, c.path, c.interpolation) for c in drive.channels]
    assert paths == [
        ("Body", "translation", "linear"),
        ("Body", "scale", "step"),
        ("Wheel", "rotation", "linear"),
        ("Wheel", "translation", "cubicspline"),
    ]
    rotation = drive.channels[2]
    assert rotation.times.tolist() == [0.0, 1.0, 2.0]
    # Normalized int16 output is scaled back to unit quaternions.
    half = math.sqrt(0.5)
    assert rotation.values[1] == pytest.approx([0.0, half, 0.0, half], abs=1e-4)
    assert drive.channels[3].values.shape == (9, 3)
    assert morph.channels == ()
    assert morph.skipped == ("morph target weights", "KHR_animation_pointer targets")
    assert nameless.name == "animation_2"
    assert nameless.skipped == ("channels without a node",)


def test_channel_model_validates_layout() -> None:
    def channel(path: str, times: list[float], values: list[list[float]]) -> None:
        AnimationChannel(transform="a", path=path, times=times, values=values)  # type: ignore[arg-type]

    channel("rotation", [0.0], [[0, 0, 0, 1]])
    with pytest.raises(ValueError, match="at least one key"):
        channel("scale", [], [])
    with pytest.raises(ValueError, match="strictly increasing"):
        channel("scale", [1.0, 1.0], [[1] * 3] * 2)
    with pytest.raises(ValueError, match="values"):
        channel("scale", [0.0, 1.0], [[1] * 3])
    with pytest.raises(ValueError, match="4 floats"):
        channel("rotation", [0.0], [[0, 0, 1]])
    cubic = AnimationChannel(
        transform="a",
        path="translation",
        interpolation="cubicspline",
        times=[0.0, 1.0],
        values=[[0.0] * 3] * 6,
    )
    assert cubic.components == 3
    clip = AnimationClip(name="c", channels=[cubic])
    assert clip.duration() == 1.0
    assert clip.targets() == {"a"}
    assert AnimationClip(name="empty").duration() == 0.0


def _clip(*targets: str, name: str = "spin") -> AnimationClip:
    return AnimationClip(
        name=name,
        channels=[
            AnimationChannel(
                transform=target,
                path="rotation",
                times=[0.0, 2.0],
                values=[[0.0, 0.0, 0.0, 1.0], [0.0, 1.0, 0.0, 0.0]],
            )
            for target in targets
        ],
    )


def test_world_keeps_clips_consistent_with_transforms() -> None:
    world = SceneWorld(
        transforms=[TransformComponent(name="a"), TransformComponent(name="b")],
        animations=[_clip("a", "b")],
    )
    assert world.animations_targeting("a") == ["spin"]
    assert world.unique_clip_name("spin") == "spin_1"
    assert world.unique_clip_name("other") == "other"
    with pytest.raises(KeyError, match="already exists"):
        world.add_animation(_clip("a"))
    with pytest.raises(KeyError, match="missing transforms"):
        world.add_animation(_clip("zzz", name="bad"))
    with pytest.raises(KeyError, match="not found"):
        world.set_animation(_clip("a", name="unknown"))
    # Rename retargets the channel.
    world.rename_transform("a", "hub")
    assert {c.transform for c in world.animations["spin"].channels} == {"hub", "b"}
    assert world.animations_targeting("a") == []
    # Delete drops the channel but keeps the clip while channels remain.
    world.remove_transform("b")
    assert [c.transform for c in world.animations["spin"].channels] == ["hub"]
    world.remove_transform("hub")
    assert world.animations == {}
    assert world.remove_animation("spin") is None
    # A clip name may equal a transform name: separate namespaces.
    world.add_transform(TransformComponent(name="spin"))
    world.add_animation(_clip("spin"))
    document = world.to_document()
    assert [clip.name for clip in document.animations] == ["spin"]
    document.animations[0].channels[0].times[0] = 5.0
    assert world.animations["spin"].channels[0].times[0] == 0.0
    world.clear()
    assert world.animations == {}


def test_replace_all_prunes_channels_on_missing_transforms() -> None:
    world = SceneWorld()
    clips = [_clip("a", "gone"), _clip("gone", name="x")]
    world.replace_all([TransformComponent(name="a")], [], [], [], clips)
    assert list(world.animations) == ["spin"]
    assert [c.transform for c in world.animations["spin"].channels] == ["a"]
    whole = _clip("a")
    assert prune_clip(whole, ["a"]) is whole
    assert prune_clip(_clip("q"), ["a"]) is None
    assert unique_clip_name("run", ["run", "run_1"]) == "run_2"


def test_scene_document_round_trips_clips(tmp_path: Path) -> None:
    document = SceneDocument(
        transforms=[TransformComponent(name="a")],
        meshes=[MeshComponent(name="aShape", transform="a", kind="box")],
        animations=[_clip("a")],
    )
    path = tmp_path / "scene.json"
    save_scene(document, path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["animations"][0]["channels"][0]["path"] == "rotation"
    loaded = load_scene(path)
    assert loaded.animations == document.animations
    # Older scenes have no animations key.
    del payload["animations"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_scene(path).animations == []


def test_import_maps_channels_to_renamed_transforms_and_warns(tmp_path: Path) -> None:
    model = load_model(_animated_gltf(tmp_path / "anim.gltf"))
    assert len(model.animations) == 3
    assert any("skins are not supported" in w for w in model.warnings)
    used = {"Body", "anim"}
    clip_names = {"Drive"}
    result = import_objects(model, used, clip_names=clip_names)
    transforms = {t.name: t for t in result.objects.transforms}
    # The file's Body clashed with the live scene, so it became Body1.
    assert result.root == "anim1"
    assert "Body1" in transforms
    assert transforms["Wheel"].parent == "Body1"
    (clip,) = result.objects.animations
    assert clip.name == "Drive_1"
    assert clip_names == {"Drive", "Drive_1"}
    assert [(c.transform, c.path) for c in clip.channels] == [
        ("Body1", "translation"),
        ("Body1", "scale"),
        ("Wheel", "rotation"),
        ("Wheel", "translation"),
    ]
    assert clip.channels[3].interpolation == "cubicspline"
    assert len(clip.channels[3].values) == 9
    assert clip.duration() == 2.0
    warnings = [w for w in result.warnings if "animation" in w]
    assert len(warnings) == 2
    assert "'Morph' skips morph target weights; KHR_animation_pointer" in warnings[0]
    assert "'animation_2' skips channels without a node" in warnings[1]
    # The single-mesh shortcut must not swallow an animated node.
    assert len(result.objects.transforms) == 3


def test_sampler_clamps_steps_lerps_and_slerps() -> None:
    half = math.sqrt(0.5)
    clip = AnimationClip(
        name="c",
        channels=[
            AnimationChannel(
                transform="a",
                path="translation",
                times=[1.0, 3.0],
                values=[[0.0, 0.0, 0.0], [2.0, 4.0, 0.0]],
            ),
            AnimationChannel(
                transform="a",
                path="scale",
                interpolation="step",
                times=[1.0, 3.0],
                values=[[1.0, 1.0, 1.0], [2.0, 2.0, 2.0]],
            ),
            AnimationChannel(
                transform="b",
                path="rotation",
                times=[0.0, 2.0],
                values=[[0.0, 0.0, 0.0, 1.0], [0.0, 1.0, 0.0, 0.0]],
            ),
        ],
    )
    sampler = ClipSampler(clip)
    assert sampler.duration == 3.0
    assert sampler.targets == {"a", "b"}
    early = sampler.sample(0.0)
    assert early["a"].position == (0.0, 0.0, 0.0)
    assert early["a"].scale == (1.0, 1.0, 1.0)
    assert early["a"].rotation is None
    mid = sampler.sample(2.0)
    assert mid["a"].position == pytest.approx((1.0, 2.0, 0.0))
    assert mid["a"].scale == (1.0, 1.0, 1.0)  # step holds the left key
    # Halfway through a 180 degree turn about Y is 90 degrees about Y.
    assert sampler.sample(1.0)["b"].rotation == pytest.approx((0.0, half, 0.0, half))
    assert mid["b"].rotation == pytest.approx((0.0, 1.0, 0.0, 0.0))
    late = sampler.sample(9.0)
    assert late["a"].position == (2.0, 4.0, 0.0)
    assert late["a"].scale == (2.0, 2.0, 2.0)
    assert sampler.sample(2.999)["a"].scale == (1.0, 1.0, 1.0)


def test_slerp_takes_the_short_arc() -> None:
    a = np.array([0.0, 0.0, 0.0, 1.0])
    b = np.array([0.0, math.sin(math.radians(80)), 0.0, math.cos(math.radians(80))])
    # -b is the same rotation; the result must still be the 80 degree turn.
    mid = quat_slerp(a, -b, 0.5)
    assert mid == pytest.approx(
        [0.0, math.sin(math.radians(40)), 0.0, math.cos(math.radians(40))]
    )
    assert np.linalg.norm(quat_slerp(a, b, 0.25)) == pytest.approx(1.0)
    # Nearly equal quaternions fall back to a normalized lerp.
    close = np.array([0.0, 1e-4, 0.0, 1.0])
    close /= np.linalg.norm(close)
    assert np.linalg.norm(quat_slerp(a, close, 0.5)) == pytest.approx(1.0)


def test_cubic_spline_follows_the_gltf_hermite_formula() -> None:
    # A key at 0 with value 0 and out-tangent 2/s, a key at 2 with value 4
    # and in-tangent 2/s: a straight line, so the midpoint is exactly 2.
    clip = AnimationClip(
        name="c",
        channels=[
            AnimationChannel(
                transform="a",
                path="translation",
                interpolation="cubicspline",
                times=[0.0, 2.0],
                values=[
                    [0.0, 0.0, 0.0],
                    [0.0, 0.0, 0.0],
                    [2.0, 0.0, 0.0],
                    [2.0, 0.0, 0.0],
                    [4.0, 0.0, 0.0],
                    [0.0, 0.0, 0.0],
                ],
            ),
            AnimationChannel(
                transform="a",
                path="rotation",
                interpolation="cubicspline",
                times=[0.0, 1.0],
                values=[
                    [0.0, 0.0, 0.0, 0.0],
                    [0.0, 0.0, 0.0, 1.0],
                    [0.0, 0.0, 0.0, 0.0],
                    [0.0, 0.0, 0.0, 0.0],
                    [0.0, 1.0, 0.0, 0.0],
                    [0.0, 0.0, 0.0, 0.0],
                ],
            ),
        ],
    )
    sampler = ClipSampler(clip)
    assert sampler.sample(1.0)["a"].position == pytest.approx((2.0, 0.0, 0.0))
    assert sampler.sample(0.5)["a"].position == pytest.approx((1.0, 0.0, 0.0))
    # Outside the keys the middle (value) row is used, not a tangent.
    assert sampler.sample(-1.0)["a"].position == (0.0, 0.0, 0.0)
    assert sampler.sample(5.0)["a"].position == (4.0, 0.0, 0.0)
    # Zero tangents blend the quaternions, and the result is renormalized.
    rotation = sampler.sample(0.5)["a"].rotation
    assert rotation is not None
    assert np.linalg.norm(rotation) == pytest.approx(1.0)
    assert rotation[1] == pytest.approx(rotation[3])
    # The formula with unit tangents scaled by the key interval.
    p0, p1 = np.array([0.0]), np.array([1.0])
    one, zero = np.array([1.0]), np.array([0.0])
    assert hermite(p0, one, p1, one, 0.5, 1.0) == pytest.approx([0.5])
    assert hermite(p0, zero, p1, zero, 0.5, 3.0) == pytest.approx([0.5])


def test_posed_transform_writes_euler_from_quaternion_and_keeps_rest() -> None:
    stored = TransformComponent(
        name="a",
        parent="p",
        position=(1.0, 2.0, 3.0),
        pivot=(0.5, 0.0, 0.0),
        visible=False,
    )
    half = math.sqrt(0.5)
    posed = posed_transform(stored, SampledTrs(rotation=(0.0, half, 0.0, half)))
    assert posed.position == (1.0, 2.0, 3.0)
    assert posed.rotation == pytest.approx((0.0, math.pi / 2, 0.0))
    assert posed.scale == (1.0, 1.0, 1.0)
    assert posed.pivot == (0.5, 0.0, 0.0)
    assert posed.visible is False
    assert posed.parent == "p"
    sample = SampledTrs(position=(0.0, 0.0, 9.0), scale=(2.0, 2.0, 2.0))
    moved = posed_transform(stored, sample)
    assert moved.position == (0.0, 0.0, 9.0)
    assert moved.rotation == (0.0, 0.0, 0.0)
    assert moved.scale == (2.0, 2.0, 2.0)
    assert stored.position == (1.0, 2.0, 3.0)


def test_playback_state_loops_clamps_and_seeks() -> None:
    state = PlaybackState(clip="c", playing=True, loop=True, speed=2.0)
    assert state.advance(0.5, 4.0) is False
    assert state.time == pytest.approx(1.0)
    assert state.advance(1.6, 4.0) is False
    assert state.time == pytest.approx(0.2)  # wrapped past 4.0
    state.loop = False
    state.time = 3.9
    assert state.advance(0.1, 4.0) is True
    assert state.time == 4.0
    assert state.playing is False
    assert state.advance(1.0, 4.0) is False  # paused: nothing moves
    state.playing = True
    assert state.advance(0.1, 0.0) is True  # an empty clip stops at once
    state.seek(-1.0, 4.0)
    assert state.time == 0.0
    state.seek(7.0, 4.0)
    assert state.time == 4.0


def test_scene_objects_carry_clips() -> None:
    objects = SceneObjects(animations=[_clip("a")])
    assert objects.animations[0].name == "spin"
    assert SceneObjects().animations == []
