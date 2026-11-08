"""Author and read scene viewport components as USD prims.

One mapping, used both by :class:`~scene_viewport.world.SceneWorld` for its
live in-memory stage and by :mod:`scene_viewport.usd_io` for files:

- ``TransformComponent`` → ``UsdGeom.Xform`` with explicit ops ``translate``,
  ``translate:pivot``, ``orient`` (quaternion), ``scale``, and the inverse
  pivot. Names that are not USD identifiers (``Wheel.001``) are made valid
  and the original kept in ``customData``.
- Primitives → ``UsdGeom.Cube`` / ``Sphere`` / ``Plane`` at the viewport's
  sizes. File meshes → ``UsdGeom.Mesh`` with the triangles embedded for
  other tools, plus the model file as an asset path.
- Mesh tab → ``UsdPreviewSurface`` under ``/World/Looks``; lights →
  ``UsdLux`` distant and sphere lights; cameras → ``UsdGeom.Camera``.
- Each animation clip → one layer of time samples on ``over`` prims. USD
  interpolates time samples linearly, so STEP and CUBICSPLINE channels are
  baked at 60 Hz for other tools and kept exactly in the layer's
  ``customLayerData`` for us, along with the channel order.

Fields USD has no schema for (edit space, light distance and decay, shadow
flags, unlit, the document index) ride in ``customData`` under ``mtqt``.
Schema attributes are float32 where the spec says so; the exact doubles
ride in ``customData`` too, so a round trip changes nothing.
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
from collections.abc import Callable, Iterable, Mapping
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
from scene_viewport.mesh.usd_reader import is_usd_model, linear_to_hex
from scene_viewport.xform_math import trs_from_mat

_log = logging.getLogger(__name__)

#: Time codes per second written to every layer.
TIME_CODES_PER_SECOND = 60.0
#: Rate at which STEP and CUBICSPLINE channels are baked to time samples.
BAKE_HZ = 60.0
#: The default prim every scene lives under.
ROOT = Sdf.Path("/World")
#: Where materials go.
LOOKS = Sdf.Path("/World/Looks")
#: Namespace of our ``customData`` keys and custom attributes.
NS = "mtqt"
#: USD's default film apertures in millimetres.
_VERTICAL_APERTURE = 15.2908
_HORIZONTAL_APERTURE = 20.955
_CHANNEL_PATHS: dict[str, ChannelPath] = {
    "xformOp:translate": "translation",
    "xformOp:orient": "rotation",
    "xformOp:scale": "scale",
}
_OP_NAMES = {path: op for op, path in _CHANNEL_PATHS.items()}


# -- paths and stage ----------------------------------------------------------


def identifier(name: str) -> str:
    """Return ``name`` as a valid USD identifier (``Wheel.001`` → ``Wheel_001``).

    Args:
        name: Component name.
    """
    return Tf.MakeValidIdentifier(name)


def unique_child_path(
    parent: Sdf.Path, name: str, is_taken: Callable[[Sdf.Path], bool]
) -> Sdf.Path:
    """Return a child path of ``parent`` for ``name`` that ``is_taken`` rejects.

    Sibling names that sanitize to the same identifier (``a.b`` and ``a_b``)
    get a ``_1``, ``_2`` suffix.

    Args:
        parent: Parent prim path.
        name: Component name.
        is_taken: Whether a candidate path is already in use.
    """
    stem = identifier(name)
    candidate = parent.AppendChild(stem)
    index = 1
    while is_taken(candidate):
        candidate = parent.AppendChild(f"{stem}_{index}")
        index += 1
    return candidate


def configure_stage(stage: Usd.Stage) -> Usd.Prim:
    """Set up axis, units, and time codes; define ``/World`` and ``/World/Looks``.

    Args:
        stage: A new stage.

    Returns:
        The ``/World`` prim, set as the default prim.
    """
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    stage.SetTimeCodesPerSecond(TIME_CODES_PER_SECOND)
    stage.SetFramesPerSecond(TIME_CODES_PER_SECOND)
    world = UsdGeom.Xform.Define(stage, ROOT).GetPrim()
    stage.SetDefaultPrim(world)
    # Reserve the materials scope so no transform can take its path.
    UsdGeom.Scope.Define(stage, LOOKS)
    return world


def material_path(prim_name: str) -> Sdf.Path:
    """Return the material prim path for the mesh prim named ``prim_name``."""
    return LOOKS.AppendChild(f"{prim_name}_Material")


# -- conversions --------------------------------------------------------------


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


def _custom(prim: Usd.Prim, values: Mapping[str, object]) -> None:
    for key, value in values.items():
        prim.SetCustomDataByKey(f"{NS}:{key}", value)


def _custom_get(prim: Usd.Prim, key: str, default: Any = None) -> Any:
    value = prim.GetCustomDataByKey(f"{NS}:{key}")
    return default if value is None else value


def _custom_name(prim: Usd.Prim, name: str) -> None:
    """Keep the component name when the prim had to be renamed for USD."""
    if prim.GetName() != name:
        _custom(prim, {"name": name})
    else:
        prim.ClearCustomDataByKey(f"{NS}:name")


# -- transforms ---------------------------------------------------------------


def define_transform(
    stage: Usd.Stage, path: Sdf.Path, transform: TransformComponent, index: int
) -> Usd.Prim:
    """Define an ``Xform`` prim at ``path`` holding ``transform``.

    Args:
        stage: Stage to author on.
        path: Prim path; its parent must exist.
        transform: Component to write.
        index: Position in the document's transform list.
    """
    prim = UsdGeom.Xform.Define(stage, path).GetPrim()
    author_transform(prim, transform, index)
    return prim


def author_transform(prim: Usd.Prim, transform: TransformComponent, index: int) -> None:
    """Write ``transform``'s TRS, pivot, visibility, and extras onto ``prim``.

    Args:
        prim: An ``Xform`` prim.
        transform: Component to write.
        index: Position in the document's transform list.
    """
    xform = UsdGeom.Xformable(prim)
    xform.ClearXformOpOrder()
    xform.AddTranslateOp().Set(Gf.Vec3d(*transform.position))
    xform.AddTranslateOp(opSuffix="pivot").Set(Gf.Vec3d(*transform.pivot))
    xform.AddOrientOp(UsdGeom.XformOp.PrecisionDouble).Set(
        _quat_from_euler(transform.rotation)
    )
    xform.AddScaleOp(UsdGeom.XformOp.PrecisionDouble).Set(Gf.Vec3d(*transform.scale))
    xform.AddTranslateOp(opSuffix="pivot", isInverseOp=True)
    visibility = UsdGeom.Imageable(prim).GetVisibilityAttr()
    if transform.visible:
        visibility.Clear()
    else:
        visibility.Set(UsdGeom.Tokens.invisible)
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
    _custom_name(prim, transform.name)


def _op_value(xform: UsdGeom.Xformable, name: str) -> object:
    for op in xform.GetOrderedXformOps():
        if op.GetOpName() == name:
            return op.Get()
    return None


def read_transform(prim: Usd.Prim, name: str, parent: str) -> TransformComponent:
    """Read an ``Xformable`` prim as a transform component.

    Prims without our op stack (another tool's file) are read through their
    local transformation, with no pivot.

    Args:
        prim: The prim.
        name: Component name to use.
        parent: Parent transform name, or ``""``.
    """
    xform = UsdGeom.Xformable(prim)
    translate = _op_value(xform, "xformOp:translate")
    orient = _op_value(xform, "xformOp:orient")
    scale = _op_value(xform, "xformOp:scale")
    pivot = _op_value(xform, "xformOp:translate:pivot")
    if translate is None or orient is None or scale is None:
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


# -- meshes -------------------------------------------------------------------


def define_mesh(
    stage: Usd.Stage,
    path: Sdf.Path,
    mesh: MeshComponent,
    layer_dir: Path | None,
    index: int,
) -> Usd.Prim:
    """Define the gprim for ``mesh`` at ``path`` and bind its material.

    Args:
        stage: Stage to author on.
        path: Prim path under the mesh's transform prim.
        mesh: Component to write.
        layer_dir: Folder the model file path is written relative to, or
            ``None`` for an absolute path (an in-memory stage).
        index: Position in the document's mesh list.
    """
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
        source = mesh.source
        if layer_dir is not None:
            with contextlib.suppress(ValueError):
                source = source.resolve().relative_to(layer_dir.resolve(), walk_up=True)
        if is_usd_model(mesh.source):
            # A USD source prim is referenced, not copied: composition brings
            # its geometry, subsets, and materials in at no cost. Our own
            # opinions (type stays the source's, placement comes from the
            # parent transform) sit above it.
            prim = stage.DefinePrim(path)
            prim.GetReferences().SetReferences(
                [Sdf.Reference(source.as_posix(), Sdf.Path(mesh.source_mesh))]
            )
            UsdGeom.Xformable(prim).SetXformOpOrder([])
            gprim = UsdGeom.Gprim(prim)
        else:
            gprim = UsdGeom.Mesh.Define(stage, path)
            _write_mesh_data(UsdGeom.Mesh(gprim), mesh)
        prim = gprim.GetPrim()
        prim.CreateAttribute(f"{NS}:source", Sdf.ValueTypeNames.Asset).Set(
            Sdf.AssetPath(source.as_posix())
        )
        prim.CreateAttribute(f"{NS}:sourceMesh", Sdf.ValueTypeNames.String).Set(
            mesh.source_mesh
        )
    prim = gprim.GetPrim()
    author_mesh_surface(stage, prim, mesh, index)
    return prim


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


def author_mesh_surface(
    stage: Usd.Stage, prim: Usd.Prim, mesh: MeshComponent, index: int
) -> None:
    """Write the Mesh tab values onto ``prim``: material, sides, and extras.

    The geometry is left alone, so this is what a Mesh tab edit calls.

    Args:
        stage: Stage holding ``prim`` (the material lives under ``/World/Looks``).
        prim: The mesh's gprim.
        mesh: Component to write.
        index: Position in the document's mesh list.
    """
    UsdGeom.Gprim(prim).GetDoubleSidedAttr().Set(mesh.double_sided)
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
            "alphaMode": mesh.alpha_mode,
            "alphaCutoff": mesh.alpha_cutoff,
            # Shader inputs are float32; keep the exact values for ourselves.
            "roughness": mesh.roughness,
            "metalness": mesh.metalness,
            "opacity": mesh.opacity,
        },
    )
    _custom_name(prim, mesh.name)
    looks_path = material_path(prim.GetName())
    material = UsdShade.Material.Define(stage, looks_path)
    shader = UsdShade.Shader.Define(stage, looks_path.AppendChild("Surface"))
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
    else:
        shader.GetPrim().RemoveProperty("inputs:opacityThreshold")
    material.CreateSurfaceOutput().ConnectToSource(shader.ConnectableAPI(), "surface")
    UsdShade.MaterialBindingAPI.Apply(prim).Bind(material)


def read_mesh(
    prim: Usd.Prim, parent: str, layer_dir: Path | None
) -> MeshComponent | None:
    """Read a gprim as a mesh component, or ``None`` when it cannot be shown.

    ``Cube`` / ``Sphere`` / ``Plane`` become primitives. A ``Mesh`` needs a
    model file (our ``mtqt:source`` attribute); without one it is skipped,
    since the viewport draws meshes from model files.

    Args:
        prim: The gprim.
        parent: Transform name the shape sits on; ``""`` skips the prim.
        layer_dir: Folder relative asset paths resolve against, or ``None``.
    """
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
        asset = prim.GetAttribute(f"{NS}:source")
        value: Any = asset.Get() if asset else None
        asset_path = str(getattr(value, "path", "") or "")
        if not asset_path:
            _log.info("%s: mesh without a model file, skipped", prim.GetPath())
            return None
        resolved = str(getattr(value, "resolvedPath", "") or "")
        base = layer_dir if layer_dir is not None else Path()
        source = Path(resolved or (base / asset_path)).resolve()
        mesh_attr = prim.GetAttribute(f"{NS}:sourceMesh")
        source_mesh = str(mesh_attr.Get() or "") if mesh_attr else ""
        kind = "file"
    if kind not in ("box", "sphere", "plane", "file"):
        return None
    inputs = _surface_inputs(prim)
    color = _custom_get(prim, "color")
    if color is None:
        diffuse = inputs.get("diffuseColor")
        color = linear_to_hex(diffuse) if diffuse is not None else "#8899aa"
    alpha_mode = str(_custom_get(prim, "alphaMode", ""))
    if alpha_mode not in ("opaque", "mask", "blend"):
        alpha_mode = "mask" if "opacityThreshold" in inputs else "opaque"
    double_sided = UsdGeom.Gprim(prim).GetDoubleSidedAttr().Get()
    albedo = str(_custom_get(prim, "albedoMap", ""))
    return MeshComponent(
        name=str(_custom_get(prim, "name", prim.GetName())),
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


# -- lights -------------------------------------------------------------------


def define_light(
    stage: Usd.Stage, path: Sdf.Path, light: LightComponent, index: int
) -> Usd.Prim:
    """Define a ``UsdLux`` light prim for ``light`` at ``path``.

    Args:
        stage: Stage to author on.
        path: Prim path under the light's transform prim.
        light: Component to write.
        index: Position in the document's light list.
    """
    if light.kind == "directional":
        prim = UsdLux.DistantLight.Define(stage, path).GetPrim()
    else:
        sphere = UsdLux.SphereLight.Define(stage, path)
        sphere.GetRadiusAttr().Set(0.05)
        sphere.GetTreatAsPointAttr().Set(True)
        if light.kind == "spot":
            shaping = UsdLux.ShapingAPI.Apply(sphere.GetPrim())
            shaping.CreateShapingConeAngleAttr().Set(45.0)
        prim = sphere.GetPrim()
    author_light(prim, light, index)
    return prim


def author_light(prim: Usd.Prim, light: LightComponent, index: int) -> None:
    """Write ``light``'s color, intensity, shadow flag, and extras onto ``prim``.

    Args:
        prim: A ``UsdLux`` light prim of the right kind.
        light: Component to write.
        index: Position in the document's light list.
    """
    lux = UsdLux.LightAPI(prim)
    lux.GetIntensityAttr().Set(light.intensity)
    lux.GetColorAttr().Set(_srgb_to_linear(light.color))
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
    _custom_name(prim, light.name)


def read_light(prim: Usd.Prim, name: str, parent: str) -> LightComponent:
    """Read a ``UsdLux`` light prim as a light component.

    Args:
        prim: The light prim.
        name: Component name to use.
        parent: Transform name the light sits on.
    """
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
        color = linear_to_hex(lux.GetColorAttr().Get() or Gf.Vec3f(1.0, 1.0, 1.0))
    shadow = UsdLux.ShadowAPI(prim).GetShadowEnableAttr()
    cast_shadow = shadow.Get() if shadow else True
    authored = lux.GetIntensityAttr().Get()
    intensity = float(
        _custom_get(prim, "intensity", 1.0 if authored is None else authored)
    )
    return LightComponent(
        name=name,
        transform=parent,
        kind=cast(LightKind, kind),
        color=str(color),
        intensity=min(max(intensity, 0.0), 20.0),
        distance=float(_custom_get(prim, "distance", 0.0)),
        decay=float(_custom_get(prim, "decay", 2.0)),
        cast_shadow=bool(cast_shadow) if cast_shadow is not None else True,
    )


# -- cameras ------------------------------------------------------------------


def define_camera(
    stage: Usd.Stage, path: Sdf.Path, camera: CameraComponent, index: int
) -> Usd.Prim:
    """Define a ``UsdGeom.Camera`` prim for ``camera`` at ``path``.

    Args:
        stage: Stage to author on.
        path: Prim path under the camera's transform prim.
        camera: Component to write.
        index: Position in the document's camera list.
    """
    prim = UsdGeom.Camera.Define(stage, path).GetPrim()
    author_camera(prim, camera, index)
    return prim


def author_camera(prim: Usd.Prim, camera: CameraComponent, index: int) -> None:
    """Write ``camera``'s lens and clipping onto ``prim``.

    Args:
        prim: A ``Camera`` prim.
        camera: Component to write.
        index: Position in the document's camera list.
    """
    geom = UsdGeom.Camera(prim)
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
    _custom_name(prim, camera.name)


def read_camera(prim: Usd.Prim, name: str, parent: str) -> CameraComponent:
    """Read a ``Camera`` prim as a camera component.

    Args:
        prim: The camera prim.
        name: Component name to use.
        parent: Transform name the camera sits on.
    """
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


def read_name(prim: Usd.Prim) -> str:
    """Return the component name a prim was written with, or its prim name."""
    return str(_custom_get(prim, "name", prim.GetName()))


def read_index(prim: Usd.Prim) -> int | None:
    """Return the document index a prim was written with, if any."""
    value = _custom_get(prim, "index")
    return int(value) if value is not None else None


# -- clips --------------------------------------------------------------------


def author_clip(
    layer: Sdf.Layer, clip: AnimationClip, paths: Mapping[str, Sdf.Path]
) -> None:
    """Replace ``layer``'s contents with ``clip`` as time-sampled xformOps.

    Linear channels keep their key times. STEP and CUBICSPLINE channels are
    baked at :data:`BAKE_HZ` for other tools, and the exact channels plus the
    channel order are kept in ``customLayerData`` for us.

    Args:
        layer: A layer to clear and fill (a file layer or an anonymous one).
        clip: The clip to write.
        paths: Transform name → prim path, for every channel target.
    """
    layer.Clear()
    layer.timeCodesPerSecond = TIME_CODES_PER_SECOND
    layer.framesPerSecond = TIME_CODES_PER_SECOND
    layer.startTimeCode = 0.0
    layer.endTimeCode = clip.duration() * TIME_CODES_PER_SECOND
    exact: list[dict[str, object]] = []
    for channel in clip.channels:
        spec = Sdf.CreatePrimInLayer(layer, paths[channel.transform])
        type_name = (
            Sdf.ValueTypeNames.Quatd
            if channel.path == "rotation"
            else Sdf.ValueTypeNames.Double3
        )
        op_name = _OP_NAMES[channel.path]
        attr = spec.attributes.get(op_name) or Sdf.AttributeSpec(
            spec, op_name, type_name
        )
        if channel.interpolation == "linear":
            times = channel.times
            values: list[list[float]] = channel.values
        else:
            times, values = _bake(channel)
            exact.append(channel.model_dump())
        for t, value in zip(times, values, strict=True):
            code = t * TIME_CODES_PER_SECOND
            if channel.path == "rotation":
                x, y, z, w = value
                layer.SetTimeSample(attr.path, code, Gf.Quatd(w, Gf.Vec3d(x, y, z)))
            else:
                layer.SetTimeSample(attr.path, code, Gf.Vec3d(*value))
    layer.customLayerData = {
        f"{NS}:clip": clip.name,
        f"{NS}:duration": clip.duration(),
        f"{NS}:channels": json.dumps(exact),
        # Prims are read in traversal order; this restores the clip's order.
        f"{NS}:order": json.dumps([[c.transform, c.path] for c in clip.channels]),
    }


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


def read_clip(layer: Sdf.Layer, names: Mapping[Sdf.Path, str]) -> AnimationClip | None:
    """Read a clip layer written by :func:`author_clip`.

    Args:
        layer: The clip layer.
        names: Prim path → transform name; channels on other prims are skipped.

    Returns:
        The clip, or ``None`` when no channel targets a known transform.
    """
    data = layer.customLayerData
    name = str(data.get(f"{NS}:clip") or Path(layer.identifier).stem)
    exact = _exact_channels(str(data.get(f"{NS}:channels") or "[]"))
    channels: list[AnimationChannel] = []
    stack = list(layer.rootPrims)
    while stack:
        spec = stack.pop()
        stack.extend(spec.nameChildren)
        target = names.get(spec.path)
        if target is None:
            continue
        for op_name, channel_path in _CHANNEL_PATHS.items():
            kept = exact.get((target, channel_path))
            if kept is not None:
                channels.append(kept)
                continue
            attr = spec.attributes.get(op_name)
            if attr is None:
                continue
            codes = layer.ListTimeSamplesForPath(attr.path)
            if not codes:
                continue
            channels.append(
                _sampled_channel(layer, attr.path, codes, target, channel_path)
            )
    if not channels:
        return None
    order = _channel_order(str(data.get(f"{NS}:order") or "[]"))
    channels.sort(key=lambda c: order.get((c.transform, c.path), len(order)))
    return AnimationClip(name=name, channels=channels)


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
    layer: Sdf.Layer,
    attr_path: Sdf.Path,
    codes: Iterable[float],
    target: str,
    channel_path: ChannelPath,
) -> AnimationChannel:
    times: list[float] = []
    values: list[list[float]] = []
    for code in codes:
        value = layer.QueryTimeSample(attr_path, code)
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
