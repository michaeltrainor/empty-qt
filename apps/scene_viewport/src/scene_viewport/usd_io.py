"""Save and open scene viewport documents as OpenUSD layers.

The mapping keeps every component field, so a scene saved as USD opens
again unchanged, while other USD tools see standard schemas:

- ``TransformComponent`` → ``UsdGeom.Xform`` with explicit ops ``translate``,
  ``translate:pivot``, ``orient`` (quaternion), ``scale``, and the inverse
  pivot. Names that are not USD identifiers (``Wheel.001``) are made valid
  and the original kept in ``customData``.
- Primitives → ``UsdGeom.Cube`` / ``Sphere`` / ``Plane`` at the viewport's
  sizes. File meshes → ``UsdGeom.Mesh`` with the triangles embedded for
  other tools, plus the model file as an asset path; the viewport keeps
  drawing from the model file.
- Mesh tab → ``UsdPreviewSurface`` under ``/World/Looks``; lights →
  ``UsdLux`` distant and sphere lights; cameras → ``UsdGeom.Camera``.
- Each animation clip → one sublayer of time samples next to the file in
  ``<stem>_clips/``. USD interpolates time samples linearly, so STEP and
  CUBICSPLINE channels are baked at 60 Hz for other tools and kept exactly
  in the layer's ``customLayerData`` for us.

Fields USD has no schema for (edit space, light distance and decay, shadow
flags, unlit) ride in ``customData`` under the ``mtqt`` key.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, cast

import numpy as np
import pylinalg as la
from pxr import Gf, Sdf, Tf, Usd, UsdGeom, UsdLux, UsdShade, Vt

from scene_viewport.animation import (
    AnimationChannel,
    AnimationClip,
    ChannelPath,
    Interpolation,
)
from scene_viewport.animation_player import ClipSampler
from scene_viewport.components import (
    AlphaMode,
    CameraComponent,
    LightComponent,
    LightKind,
    MeshComponent,
    MeshKind,
    TransformComponent,
)
from scene_viewport.mesh.importer import ModelImportError, load_model
from scene_viewport.scene_io import SceneDocument
from scene_viewport.xform_math import trs_from_mat

_log = logging.getLogger(__name__)

#: File suffixes :func:`save_usd_scene` writes and :func:`load_usd_scene` reads.
USD_SUFFIXES = (".usda", ".usdc", ".usd")
#: Time codes per second written to every layer.
TIME_CODES_PER_SECOND = 60.0
#: Rate at which STEP and CUBICSPLINE channels are baked to time samples.
BAKE_HZ = 60.0
#: USD's default vertical film aperture in millimetres.
_VERTICAL_APERTURE = 15.2908
_HORIZONTAL_APERTURE = 20.955
_ROOT = Sdf.Path("/World")
_LOOKS = Sdf.Path("/World/Looks")
_NS = "mtqt"
_CHANNEL_PATHS: dict[str, ChannelPath] = {
    "xformOp:translate": "translation",
    "xformOp:orient": "rotation",
    "xformOp:scale": "scale",
}
_OP_NAMES = {path: op for op, path in _CHANNEL_PATHS.items()}


def is_usd_file(path: Path) -> bool:
    """Return whether ``path`` has a USD layer suffix.

    Args:
        path: Candidate file path.
    """
    return path.suffix.lower() in USD_SUFFIXES


# -- save ---------------------------------------------------------------------


def save_usd_scene(document: SceneDocument, path: Path) -> Path:
    """Write ``document`` as a USD stage at ``path``.

    Args:
        document: Scene to persist. Mesh ``source`` paths are written relative
            to the layer when they share a drive with it.
        path: Destination ``.usda``, ``.usdc``, or ``.usd`` file. Clips go to
            ``<stem>_clips/`` beside it, one layer each.

    Returns:
        The path written.

    Raises:
        OSError: When a layer cannot be created or saved.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        stage = Usd.Stage.CreateNew(str(path))
    except Tf.ErrorException as exc:
        raise OSError(f"cannot create {path}: {exc}") from exc
    if stage is None:
        raise OSError(f"cannot create {path}")
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    stage.SetTimeCodesPerSecond(TIME_CODES_PER_SECOND)
    stage.SetFramesPerSecond(TIME_CODES_PER_SECOND)
    world = UsdGeom.Xform.Define(stage, _ROOT)
    stage.SetDefaultPrim(world.GetPrim())

    paths = _prim_paths(document.transforms)
    for index, transform in enumerate(document.transforms):
        _write_transform(stage, paths[transform.name], transform, index)
    for index, mesh in enumerate(document.meshes):
        _write_mesh(stage, paths[mesh.transform], mesh, path.parent, index)
    for index, light in enumerate(document.lights):
        _write_light(stage, paths[light.transform], light, index)
    for index, camera in enumerate(document.cameras):
        _write_camera(stage, paths[camera.transform], camera, index)

    layer = stage.GetRootLayer()
    if document.animations:
        clips_dir = path.parent / f"{path.stem}_clips"
        clips_dir.mkdir(exist_ok=True)
        for clip in document.animations:
            clip_path = clips_dir / f"{_identifier(clip.name)}{path.suffix}"
            _write_clip(clip_path, clip, paths)
            layer.subLayerPaths.append(f"./{clips_dir.name}/{clip_path.name}")
        stage.SetStartTimeCode(0.0)
        stage.SetEndTimeCode(
            max(clip.duration() for clip in document.animations) * TIME_CODES_PER_SECOND
        )
    try:
        if not layer.Save():
            raise OSError(f"cannot save {path}")
    except Tf.ErrorException as exc:
        raise OSError(f"cannot save {path}: {exc}") from exc
    return path


def _identifier(name: str) -> str:
    return Tf.MakeValidIdentifier(name)


def _prim_paths(transforms: Iterable[TransformComponent]) -> dict[str, Sdf.Path]:
    """Map transform names to prim paths that mirror the hierarchy."""
    by_name = {t.name: t for t in transforms}
    paths: dict[str, Sdf.Path] = {}

    def path_of(name: str, seen: frozenset[str]) -> Sdf.Path:
        if name in paths:
            return paths[name]
        parent = by_name[name].parent
        if parent in by_name and parent not in seen:
            base = path_of(parent, seen | {name})
        else:
            base = _ROOT
        paths[name] = base.AppendChild(_identifier(name))
        return paths[name]

    for name in by_name:
        path_of(name, frozenset())
    return paths


def _quat_from_euler(euler: tuple[float, float, float]) -> Gf.Quatd:
    x, y, z, w = (float(v) for v in la.quat_from_euler(euler, order="xyz"))
    return Gf.Quatd(w, Gf.Vec3d(x, y, z))


def _euler_from_quat(quat: Gf.Quatd) -> tuple[float, float, float]:
    imaginary = quat.GetImaginary()
    euler = la.quat_to_euler(
        (imaginary[0], imaginary[1], imaginary[2], quat.GetReal()), order="xyz"
    )
    return (float(euler[0]), float(euler[1]), float(euler[2]))


def _srgb_to_linear(hex_color: str) -> Gf.Vec3f:
    value = hex_color.lstrip("#")
    channels = [int(value[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
    return Gf.Vec3f(
        *(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    )


def _linear_to_hex(color: object) -> str:
    out = []
    for c in cast(Iterable[float], color):
        c = max(0.0, min(1.0, float(c)))
        s = c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1.0 / 2.4) - 0.055
        out.append(f"{round(s * 255):02x}")
    return "#" + "".join(out)


def _custom(prim: Usd.Prim, values: Mapping[str, object]) -> None:
    for key, value in values.items():
        prim.SetCustomDataByKey(f"{_NS}:{key}", value)


def _custom_get(prim: Usd.Prim, key: str, default: Any = None) -> Any:
    value = prim.GetCustomDataByKey(f"{_NS}:{key}")
    return default if value is None else value


def _write_transform(
    stage: Usd.Stage, path: Sdf.Path, transform: TransformComponent, index: int
) -> None:
    xform = UsdGeom.Xform.Define(stage, path)
    xform.AddTranslateOp().Set(Gf.Vec3d(*transform.position))
    xform.AddTranslateOp(opSuffix="pivot").Set(Gf.Vec3d(*transform.pivot))
    xform.AddOrientOp(UsdGeom.XformOp.PrecisionDouble).Set(
        _quat_from_euler(transform.rotation)
    )
    xform.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*transform.scale))
    xform.AddTranslateOp(opSuffix="pivot", isInverseOp=True)
    if not transform.visible:
        UsdGeom.Imageable(xform).GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)
    prim = xform.GetPrim()
    # The quaternion reads back as an equivalent Euler triple that may be
    # written differently (180, -30, -180 for 0, 150, 0); keep ours as is.
    _custom(
        prim,
        {
            "space": transform.space,
            "rotation": Vt.DoubleArray(transform.rotation),
            # Position in the saved list: composition reorders prims.
            "index": index,
        },
    )
    if prim.GetName() != transform.name:
        _custom(prim, {"name": transform.name})


def _write_mesh(
    stage: Usd.Stage,
    parent: Sdf.Path,
    mesh: MeshComponent,
    layer_dir: Path,
    index: int,
) -> None:
    path = parent.AppendChild(_identifier(mesh.name))
    if mesh.kind == "box":
        gprim: UsdGeom.Gprim = UsdGeom.Cube.Define(stage, path)
        UsdGeom.Cube(gprim).GetSizeAttr().Set(1.0)
    elif mesh.kind == "sphere":
        gprim = UsdGeom.Sphere.Define(stage, path)
        UsdGeom.Sphere(gprim).GetRadiusAttr().Set(0.5)
    elif mesh.kind == "plane":
        gprim = UsdGeom.Plane.Define(stage, path)
        plane = UsdGeom.Plane(gprim)
        plane.GetAxisAttr().Set(UsdGeom.Tokens.y)
        plane.GetWidthAttr().Set(1.0)
        plane.GetLengthAttr().Set(1.0)
    else:
        gprim = UsdGeom.Mesh.Define(stage, path)
        _write_mesh_data(UsdGeom.Mesh(gprim), mesh)
        source = mesh.source
        with contextlib.suppress(ValueError):
            source = source.resolve().relative_to(layer_dir.resolve(), walk_up=True)
        prim = gprim.GetPrim()
        prim.CreateAttribute(f"{_NS}:source", Sdf.ValueTypeNames.Asset).Set(
            Sdf.AssetPath(source.as_posix())
        )
        prim.CreateAttribute(f"{_NS}:sourceMesh", Sdf.ValueTypeNames.String).Set(
            mesh.source_mesh
        )
    gprim.GetDoubleSidedAttr().Set(mesh.double_sided)
    prim = gprim.GetPrim()
    _custom(
        prim,
        {
            "index": index,
            "kind": mesh.kind,
            "color": mesh.color,
            "unlit": mesh.unlit,
            "castShadow": mesh.cast_shadow,
            "receiveShadow": mesh.receive_shadow,
            "albedoMap": mesh.albedo_map.as_posix() if str(mesh.albedo_map) else "",
        },
    )
    if prim.GetName() != mesh.name:
        _custom(prim, {"name": mesh.name})
    _bind_material(stage, prim, mesh)


def _write_mesh_data(geom: UsdGeom.Mesh, mesh: MeshComponent) -> None:
    """Embed the triangles of a file mesh, when its model file loads."""
    try:
        buffers = load_model(mesh.source).meshes[mesh.source_mesh]
    except (ModelImportError, KeyError):
        _log.info("%s: source not loaded, writing an empty mesh prim", mesh.name)
        return
    faces = np.ascontiguousarray(buffers.indices, dtype=np.int32).reshape(-1)
    geom.GetPointsAttr().Set(Vt.Vec3fArray.FromNumpy(buffers.positions))
    geom.GetFaceVertexIndicesAttr().Set(Vt.IntArray.FromNumpy(faces))
    geom.GetFaceVertexCountsAttr().Set(
        Vt.IntArray.FromNumpy(np.full(len(faces) // 3, 3, dtype=np.int32))
    )
    geom.GetNormalsAttr().Set(Vt.Vec3fArray.FromNumpy(buffers.normals))
    geom.SetNormalsInterpolation(UsdGeom.Tokens.vertex)
    geom.GetSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
    st = UsdGeom.PrimvarsAPI(geom).CreatePrimvar(
        "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex
    )
    st.Set(Vt.Vec2fArray.FromNumpy(buffers.texcoords))


def _bind_material(stage: Usd.Stage, prim: Usd.Prim, mesh: MeshComponent) -> None:
    material_path = _LOOKS.AppendChild(f"{prim.GetName()}_Material")
    material = UsdShade.Material.Define(stage, material_path)
    shader = UsdShade.Shader.Define(stage, material_path.AppendChild("Surface"))
    shader.CreateIdAttr("UsdPreviewSurface")
    shader.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).Set(
        _srgb_to_linear(mesh.color)
    )
    shader.CreateInput("roughness", Sdf.ValueTypeNames.Float).Set(mesh.roughness)
    shader.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(mesh.metalness)
    shader.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(mesh.opacity)
    if mesh.alpha_mode == "mask":
        shader.CreateInput("opacityThreshold", Sdf.ValueTypeNames.Float).Set(
            mesh.alpha_cutoff
        )
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    UsdShade.MaterialBindingAPI.Apply(prim).Bind(material)
    _custom(
        prim,
        {
            "alphaMode": mesh.alpha_mode,
            "alphaCutoff": mesh.alpha_cutoff,
            # Shader inputs are float32; keep the exact values for ourselves.
            "roughness": mesh.roughness,
            "metalness": mesh.metalness,
            "opacity": mesh.opacity,
        },
    )


def _write_light(
    stage: Usd.Stage, parent: Sdf.Path, light: LightComponent, index: int
) -> None:
    path = parent.AppendChild(_identifier(light.name))
    if light.kind == "directional":
        lux: UsdLux.LightAPI = UsdLux.DistantLight.Define(stage, path).LightAPI()
    else:
        sphere = UsdLux.SphereLight.Define(stage, path)
        sphere.GetRadiusAttr().Set(0.05)
        sphere.GetTreatAsPointAttr().Set(True)
        if light.kind == "spot":
            shaping = UsdLux.ShapingAPI.Apply(sphere.GetPrim())
            shaping.CreateShapingConeAngleAttr().Set(45.0)
        lux = sphere.LightAPI()
    lux.GetIntensityAttr().Set(light.intensity)
    lux.GetColorAttr().Set(_srgb_to_linear(light.color))
    prim = lux.GetPrim()
    UsdLux.ShadowAPI.Apply(prim).CreateShadowEnableAttr().Set(light.cast_shadow)
    _custom(
        prim,
        {
            "index": index,
            "kind": light.kind,
            "color": light.color,
            "intensity": light.intensity,
            "distance": light.distance,
            "decay": light.decay,
        },
    )
    if prim.GetName() != light.name:
        _custom(prim, {"name": light.name})


def _write_camera(
    stage: Usd.Stage, parent: Sdf.Path, camera: CameraComponent, index: int
) -> None:
    path = parent.AppendChild(_identifier(camera.name))
    geom = UsdGeom.Camera.Define(stage, path)
    geom.GetClippingRangeAttr().Set(Gf.Vec2f(camera.near, camera.far))
    if camera.kind == "orthographic":
        geom.GetProjectionAttr().Set(UsdGeom.Tokens.orthographic)
        # Orthographic apertures are in tenths of scene units.
        geom.GetVerticalApertureAttr().Set(camera.ortho_size * 10.0)
        geom.GetHorizontalApertureAttr().Set(camera.ortho_size * 10.0)
    else:
        geom.GetProjectionAttr().Set(UsdGeom.Tokens.perspective)
        geom.GetVerticalApertureAttr().Set(_VERTICAL_APERTURE)
        geom.GetHorizontalApertureAttr().Set(_HORIZONTAL_APERTURE)
        geom.GetFocalLengthAttr().Set(
            _VERTICAL_APERTURE / (2.0 * math.tan(math.radians(camera.fov) / 2.0))
        )
    prim = geom.GetPrim()
    _custom(
        prim,
        {
            "index": index,
            "fov": camera.fov,
            "orthoSize": camera.ortho_size,
            "near": camera.near,
            "far": camera.far,
        },
    )
    if prim.GetName() != camera.name:
        _custom(prim, {"name": camera.name})


def _write_clip(
    layer_path: Path, clip: AnimationClip, paths: Mapping[str, Sdf.Path]
) -> None:
    """Write one clip as a layer of time-sampled xformOps on ``over`` prims."""
    layer = Sdf.Layer.CreateNew(str(layer_path))
    if layer is None:
        raise OSError(f"cannot create {layer_path}")
    stage = Usd.Stage.Open(layer)
    stage.SetTimeCodesPerSecond(TIME_CODES_PER_SECOND)
    stage.SetFramesPerSecond(TIME_CODES_PER_SECOND)
    stage.SetStartTimeCode(0.0)
    stage.SetEndTimeCode(clip.duration() * TIME_CODES_PER_SECOND)
    exact: list[dict[str, object]] = []
    for channel in clip.channels:
        prim = stage.OverridePrim(paths[channel.transform])
        type_name = (
            Sdf.ValueTypeNames.Quatd
            if channel.path == "rotation"
            else Sdf.ValueTypeNames.Double3
        )
        attr = prim.CreateAttribute(_OP_NAMES[channel.path], type_name)
        if channel.interpolation == "linear":
            times = channel.times
            values: list[list[float]] = channel.values
        else:
            # Time samples interpolate linearly stage-wide, so bake these for
            # other tools and keep the exact keys for ourselves below.
            times, values = _bake(channel)
            exact.append(channel.model_dump())
        for t, value in zip(times, values, strict=True):
            code = t * TIME_CODES_PER_SECOND
            if channel.path == "rotation":
                x, y, z, w = value
                attr.Set(Gf.Quatd(w, Gf.Vec3d(x, y, z)), code)
            else:
                attr.Set(Gf.Vec3d(*value), code)
    layer.customLayerData = {
        f"{_NS}:clip": clip.name,
        f"{_NS}:duration": clip.duration(),
        f"{_NS}:channels": json.dumps(exact),
        # Prims are read in traversal order; this restores the clip's order.
        f"{_NS}:order": json.dumps([[c.transform, c.path] for c in clip.channels]),
    }
    if not layer.Save():
        raise OSError(f"cannot save {layer_path}")


def _bake(channel: AnimationChannel) -> tuple[list[float], list[list[float]]]:
    sampler = ClipSampler(AnimationClip(name="bake", channels=[channel]))
    end = channel.times[-1]
    count = max(2, round(end * BAKE_HZ) + 1)
    times = [end * i / (count - 1) for i in range(count)]
    values: list[list[float]] = []
    for t in times:
        sample = sampler.sample(t)[channel.transform]
        value = sample.position or sample.rotation or sample.scale
        values.append([float(v) for v in value or ()])
    return times, values


# -- load ---------------------------------------------------------------------


def load_usd_scene(path: Path) -> SceneDocument:
    """Read a USD stage at ``path`` into a scene document.

    Stages this app wrote come back unchanged. For stages from other tools,
    ``Xform`` hierarchies, ``Cube`` / ``Sphere`` / ``Plane`` prims, lights,
    and cameras are read; ``Mesh`` prims without a model file this app can
    load are skipped with a log message, since the viewport draws meshes
    from model files.

    Args:
        path: ``.usda``, ``.usdc``, or ``.usd`` file.

    Returns:
        The document, with mesh ``source`` paths made absolute.

    Raises:
        OSError: When the file cannot be read.
        ValueError: When it is not a USD layer this app can open.
    """
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {path}")
    try:
        stage = Usd.Stage.Open(str(path))
    except Tf.ErrorException as exc:
        raise ValueError(f"not a USD file: {path.name} ({exc})") from exc
    if stage is None:
        raise ValueError(f"not a USD file: {path.name}")
    root = stage.GetPrimAtPath(_ROOT)
    if not root:
        root = stage.GetDefaultPrim() or stage.GetPseudoRoot()

    transforms: list[_Keyed[TransformComponent]] = []
    meshes: list[_Keyed[MeshComponent]] = []
    lights: list[_Keyed[LightComponent]] = []
    cameras: list[_Keyed[CameraComponent]] = []
    names: dict[Sdf.Path, str] = {}
    used: set[str] = set()

    def unique(prim: Usd.Prim) -> str:
        name = str(_custom_get(prim, "name", prim.GetName()))
        candidate, index = name, 1
        while candidate in used:
            candidate = f"{name}{index}"
            index += 1
        used.add(candidate)
        return candidate

    for prim in Usd.PrimRange(root):
        if prim == root or prim.GetPath().HasPrefix(_LOOKS):
            continue
        prim_path = prim.GetPath()
        parent = names.get(prim_path.GetParentPath(), "")
        saved = _custom_get(prim, "index")
        key: _SortKey = (0, int(saved), prim_path) if saved is not None else None
        if prim.IsA(UsdGeom.Camera):
            if parent:
                camera = _read_camera(prim, unique(prim), parent)
                cameras.append((key, prim_path, camera))
        elif prim.HasAPI(UsdLux.LightAPI):
            if parent:
                lights.append((key, prim_path, _read_light(prim, unique(prim), parent)))
        elif prim.IsA(UsdGeom.Gprim):
            mesh = _read_mesh(prim, parent, path.parent)
            if mesh is not None:
                mesh = mesh.model_copy(update={"name": unique(prim)})
                meshes.append((key, prim_path, mesh))
        elif prim.IsA(UsdGeom.Xformable):
            name = unique(prim)
            names[prim_path] = name
            transform = _read_transform(prim, name, parent)
            transforms.append((key, prim_path, transform))
    # Composition lists sublayer prims (the clips' overs) ahead of the root
    # layer's children, so put everything back in the saved order: the index
    # we wrote, or for other tools' files the root layer's authored order.
    order = _authored_order(stage.GetRootLayer(), root.GetPath())
    return SceneDocument(
        transforms=_ordered(transforms, order),
        meshes=_ordered(meshes, order),
        lights=_ordered(lights, order),
        cameras=_ordered(cameras, order),
        animations=_read_clips(stage, names),
    )


def _authored_order(layer: Sdf.Layer, root: Sdf.Path) -> dict[Sdf.Path, int]:
    """Depth-first position of each prim spec in ``layer`` under ``root``."""
    order: dict[Sdf.Path, int] = {}
    spec = layer.GetPrimAtPath(root)
    if spec is None:
        return order
    stack = list(reversed(list(spec.nameChildren)))
    while stack:
        child = stack.pop()
        order[child.path] = len(order)
        stack.extend(reversed(list(child.nameChildren)))
    return order


type _SortKey = tuple[int, int, Sdf.Path] | None
type _Keyed[T] = tuple[_SortKey, Sdf.Path, T]


def _ordered[T](items: list[_Keyed[T]], order: Mapping[Sdf.Path, int]) -> list[T]:
    """Sort by saved index, then by authored order for prims without one."""
    end = len(order)

    def key(item: _Keyed[T]) -> tuple[int, int, Sdf.Path]:
        saved, prim_path, _ = item
        if saved is not None:
            return saved
        return (1, order.get(prim_path, end), prim_path)

    return [item for _, _, item in sorted(items, key=key)]


def _op_value(xform: UsdGeom.Xformable, name: str) -> object:
    for op in xform.GetOrderedXformOps():
        if op.GetOpName() == name:
            return op.Get()
    return None


def _read_transform(prim: Usd.Prim, name: str, parent: str) -> TransformComponent:
    xform = UsdGeom.Xformable(prim)
    translate = _op_value(xform, "xformOp:translate")
    orient = _op_value(xform, "xformOp:orient")
    scale = _op_value(xform, "xformOp:scale")
    pivot = _op_value(xform, "xformOp:translate:pivot")
    if translate is None or orient is None or scale is None:
        # Not our op stack (another tool's file): take the matrix, no pivot.
        matrix = np.asarray(xform.GetLocalTransformation(), dtype=np.float64).T
        position, rotation, scale_vec = trs_from_mat(matrix)
        pivot_vec = (0.0, 0.0, 0.0)
    else:
        position = tuple(float(v) for v in cast(Iterable[float], translate))
        authored = _custom_get(prim, "rotation")
        if authored is not None and len(authored) == 3:
            rotation = (float(authored[0]), float(authored[1]), float(authored[2]))
        else:
            rotation = _euler_from_quat(cast(Gf.Quatd, orient))
        scale_vec = tuple(float(v) for v in cast(Iterable[float], scale))
        pivot_vec = (
            tuple(float(v) for v in cast(Iterable[float], pivot))
            if pivot is not None
            else (0.0, 0.0, 0.0)
        )
    visibility = UsdGeom.Imageable(prim).GetVisibilityAttr().Get()
    space = str(_custom_get(prim, "space", "world"))
    return TransformComponent(
        name=name,
        parent=parent,
        space="local" if space == "local" else "world",
        position=cast(tuple[float, float, float], position),
        rotation=rotation,
        scale=cast(tuple[float, float, float], scale_vec),
        pivot=cast(tuple[float, float, float], pivot_vec),
        visible=visibility != UsdGeom.Tokens.invisible,
    )


def _read_mesh(prim: Usd.Prim, parent: str, layer_dir: Path) -> MeshComponent | None:
    if not parent:
        return None
    kind = str(_custom_get(prim, "kind", ""))
    source = Path("")
    source_mesh = ""
    if not kind:
        if prim.IsA(UsdGeom.Cube):
            kind = "box"
        elif prim.IsA(UsdGeom.Sphere):
            kind = "sphere"
        elif prim.IsA(UsdGeom.Plane):
            kind = "plane"
    if kind == "file" or (not kind and prim.IsA(UsdGeom.Mesh)):
        asset = prim.GetAttribute(f"{_NS}:source")
        value: Any = asset.Get() if asset else None
        asset_path = str(getattr(value, "path", "") or "")
        if not asset_path:
            _log.info("%s: mesh without a model file, skipped", prim.GetPath())
            return None
        resolved = str(getattr(value, "resolvedPath", "") or "")
        source = Path(resolved or (layer_dir / asset_path)).resolve()
        mesh_attr = prim.GetAttribute(f"{_NS}:sourceMesh")
        source_mesh = str(mesh_attr.Get() or "") if mesh_attr else ""
        kind = "file"
    if kind not in ("box", "sphere", "plane", "file"):
        return None
    inputs = _surface_inputs(prim)
    color = _custom_get(prim, "color")
    if color is None:
        diffuse = inputs.get("diffuseColor")
        color = _linear_to_hex(diffuse) if diffuse is not None else "#8899aa"
    alpha_mode = str(_custom_get(prim, "alphaMode", ""))
    if alpha_mode not in ("opaque", "mask", "blend"):
        alpha_mode = "mask" if "opacityThreshold" in inputs else "opaque"
    double_sided = UsdGeom.Gprim(prim).GetDoubleSidedAttr().Get()
    albedo = str(_custom_get(prim, "albedoMap", ""))
    return MeshComponent(
        name=prim.GetName(),
        transform=parent,
        kind=cast(MeshKind, kind),
        source=source,
        source_mesh=source_mesh,
        color=str(color),
        roughness=float(_custom_get(prim, "roughness", inputs.get("roughness", 0.45))),
        metalness=float(_custom_get(prim, "metalness", inputs.get("metallic", 0.05))),
        albedo_map=Path(albedo) if albedo else Path(""),
        opacity=float(_custom_get(prim, "opacity", inputs.get("opacity", 1.0))),
        alpha_mode=cast(AlphaMode, alpha_mode),
        alpha_cutoff=float(
            _custom_get(prim, "alphaCutoff", inputs.get("opacityThreshold", 0.5))
        ),
        double_sided=bool(double_sided) if double_sided is not None else True,
        unlit=bool(_custom_get(prim, "unlit", False)),
        cast_shadow=bool(_custom_get(prim, "castShadow", True)),
        receive_shadow=bool(_custom_get(prim, "receiveShadow", True)),
    )


def _surface_inputs(prim: Usd.Prim) -> dict[str, Any]:
    """Return the bound UsdPreviewSurface inputs by name, when there are any."""
    material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    if not material:
        return {}
    surface = material.ComputeSurfaceSource()[0]
    if not surface:
        return {}
    values: dict[str, Any] = {}
    for shader_input in surface.GetInputs():
        value = shader_input.Get()
        if value is not None:
            values[shader_input.GetBaseName()] = value
    return values


def _read_light(prim: Usd.Prim, name: str, parent: str) -> LightComponent:
    lux = UsdLux.LightAPI(prim)
    kind = str(_custom_get(prim, "kind", ""))
    if kind not in ("directional", "point", "spot"):
        if prim.IsA(UsdLux.DistantLight):
            kind = "directional"
        elif prim.HasAPI(UsdLux.ShapingAPI):
            kind = "spot"
        else:
            kind = "point"
    color = _custom_get(prim, "color")
    if color is None:
        color = _linear_to_hex(lux.GetColorAttr().Get() or Gf.Vec3f(1.0, 1.0, 1.0))
    shadow = UsdLux.ShadowAPI(prim).GetShadowEnableAttr()
    cast_shadow = shadow.Get() if shadow else True
    return LightComponent(
        name=name,
        transform=parent,
        kind=cast(LightKind, kind),
        color=str(color),
        intensity=min(
            max(
                float(
                    _custom_get(prim, "intensity", lux.GetIntensityAttr().Get() or 1.0)
                ),
                0.0,
            ),
            20.0,
        ),
        distance=float(_custom_get(prim, "distance", 0.0)),
        decay=float(_custom_get(prim, "decay", 2.0)),
        cast_shadow=bool(cast_shadow) if cast_shadow is not None else True,
    )


def _read_camera(prim: Usd.Prim, name: str, parent: str) -> CameraComponent:
    geom = UsdGeom.Camera(prim)
    clipping = geom.GetClippingRangeAttr().Get()
    near, far = (float(clipping[0]), float(clipping[1])) if clipping else (0.1, 500.0)
    near = float(_custom_get(prim, "near", near))
    far = float(_custom_get(prim, "far", far))
    ortho = geom.GetProjectionAttr().Get() == UsdGeom.Tokens.orthographic
    aperture = float(geom.GetVerticalApertureAttr().Get() or _VERTICAL_APERTURE)
    lens: dict[str, float] = {}
    if ortho:
        lens["ortho_size"] = float(_custom_get(prim, "orthoSize", aperture / 10.0))
    else:
        focal = float(geom.GetFocalLengthAttr().Get() or 50.0)
        fov = math.degrees(2.0 * math.atan(aperture / (2.0 * focal)))
        lens["fov"] = float(_custom_get(prim, "fov", fov))
    near = min(max(near, 1e-4), 1000.0)
    return CameraComponent(
        name=name,
        transform=parent,
        kind="orthographic" if ortho else "perspective",
        near=near,
        far=min(max(far, near * 1.001), 100000.0),
        **{
            k: min(max(v, 1e-3), 10000.0 if k == "ortho_size" else 170.0)
            for k, v in lens.items()
        },
    )


def _read_clips(stage: Usd.Stage, names: Mapping[Sdf.Path, str]) -> list[AnimationClip]:
    root_layer = stage.GetRootLayer()
    clips: list[AnimationClip] = []
    for sub_path in root_layer.subLayerPaths:
        layer = Sdf.Layer.FindOrOpen(root_layer.ComputeAbsolutePath(sub_path))
        if layer is None:
            _log.warning("clip layer %s is missing, skipped", sub_path)
            continue
        data = layer.customLayerData
        name = str(data.get(f"{_NS}:clip") or Path(layer.identifier).stem)
        exact = _exact_channels(str(data.get(f"{_NS}:channels") or "[]"))
        clip_stage = Usd.Stage.Open(layer)
        channels: list[AnimationChannel] = []
        for prim in clip_stage.TraverseAll():
            target = names.get(prim.GetPath())
            if target is None:
                continue
            for op_name, channel_path in _CHANNEL_PATHS.items():
                kept = exact.get((target, channel_path))
                if kept is not None:
                    channels.append(kept)
                    continue
                attr = prim.GetAttribute(op_name)
                if not attr or not attr.GetNumTimeSamples():
                    continue
                channels.append(_sampled_channel(attr, target, channel_path))
        if channels:
            order = _channel_order(str(data.get(f"{_NS}:order") or "[]"))
            channels.sort(key=lambda c: order.get((c.transform, c.path), len(order)))
            clips.append(AnimationClip(name=name, channels=channels))
    return clips


def _channel_order(payload: str) -> dict[tuple[str, str], int]:
    try:
        entries = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    order: dict[tuple[str, str], int] = {}
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, list) and len(entry) == 2:
            order[(str(entry[0]), str(entry[1]))] = len(order)
    return order


def _exact_channels(payload: str) -> dict[tuple[str, ChannelPath], AnimationChannel]:
    try:
        entries = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    exact: dict[tuple[str, ChannelPath], AnimationChannel] = {}
    for entry in entries if isinstance(entries, list) else []:
        try:
            channel = AnimationChannel.model_validate(entry)
        except ValueError:
            continue
        exact[(channel.transform, channel.path)] = channel
    return exact


def _seconds(code: float) -> float:
    """Convert a time code back to seconds.

    ``t * 60 / 60`` is not always ``t`` in floating point. Key times come
    from float32 glTF data, so a result within rounding noise of a float32
    value is snapped to it, which restores the authored time exactly.
    """
    seconds = code / TIME_CODES_PER_SECOND
    snapped = float(np.float32(seconds))
    return (
        snapped if abs(snapped - seconds) <= 1e-9 * max(1.0, abs(seconds)) else seconds
    )


def _sampled_channel(
    attr: Usd.Attribute, target: str, channel_path: ChannelPath
) -> AnimationChannel:
    times: list[float] = []
    values: list[list[float]] = []
    for code in attr.GetTimeSamples():
        value = attr.Get(code)
        times.append(_seconds(code))
        if channel_path == "rotation":
            quat = cast(Gf.Quatd, value)
            im = quat.GetImaginary()
            values.append([im[0], im[1], im[2], quat.GetReal()])
        else:
            values.append([float(v) for v in cast(Iterable[float], value)])
    interpolation: Interpolation = "linear"
    return AnimationChannel(
        transform=target,
        path=channel_path,
        interpolation=interpolation,
        times=times,
        values=values,
    )
