"""Write the scene, or part of it, as a glTF 2.0 file.

trimesh writes the geometry, hierarchy, PBR materials, and textures. The
glTF JSON is then patched for what trimesh doesn't write: punctual lights
(``KHR_lights_punctual``) and unlit materials (``KHR_materials_unlit``).
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh
from numpy.typing import NDArray
from PIL import Image
from trimesh.visual import ColorVisuals, TextureVisuals
from trimesh.visual.material import PBRMaterial

from scene_viewport.components import (
    CameraComponent,
    LightComponent,
    MapKind,
    MeshComponent,
)
from scene_viewport.mesh.gltf_extras import join_glb, split_glb
from scene_viewport.mesh.importer import ImportedMaps, MaterialSurface, ModelImportError
from scene_viewport.mesh.primitives import box, plane, uv_sphere
from scene_viewport.mesh.types import MeshBuffers
from scene_viewport.textures import file_material
from scene_viewport.world import SceneWorld
from scene_viewport.xform_math import look_at_matrix, mat_from_trs

#: glTF spot cone half-angle when the light has none of its own (pygfx default).
_SPOT_OUTER_ANGLE = math.pi / 3
#: Suffix for a light's node when its own name is taken by a transform.
_LIGHT_SUFFIX = "Light"
#: Core glTF texture slots trimesh writes, by map kind.
_CORE_MAP_SLOTS: dict[MapKind, str] = {
    "metallic_roughness": "metallicRoughnessTexture",
    "normal": "normalTexture",
    "emissive": "emissiveTexture",
    "occlusion": "occlusionTexture",
}
#: Map kinds exported through core slots or the base color.
_EXPORTED_MAPS = frozenset({"base_color", *_CORE_MAP_SLOTS})


@dataclass
class ExportResult:
    """What :func:`export_scene` wrote.

    Args:
        path: The file written.
        transforms: Transform nodes written.
        meshes: Meshes written.
        lights: Lights written.
        cameras: Cameras written.
        warnings: Things left out or approximated.
    """

    path: Path
    transforms: int = 0
    meshes: int = 0
    lights: int = 0
    cameras: int = 0
    warnings: list[str] = field(default_factory=list)


def export_scene(
    world: SceneWorld,
    path: Path,
    *,
    roots: Sequence[str] | None = None,
    include_hidden: bool = False,
) -> ExportResult:
    """Write ``world`` (or the subtrees under ``roots``) as ``.glb`` or ``.gltf``.

    Transforms become nodes with their local TRS. Exported roots whose
    parent is not exported keep their world pose. Primitives are
    tessellated, ``file`` meshes are re-embedded from their loaded
    buffers, so the result is self-contained. Materials carry the Mesh tab
    values and the file's maps; lights become ``KHR_lights_punctual``,
    aimed at the scene origin as the viewport aims them.

    Args:
        world: The scene.
        path: Destination; the suffix picks binary (``.glb``) or JSON
            (``.gltf``, with ``.bin`` buffers beside it).
        roots: Transform UIDs to export with their descendants. ``None``
            exports everything.
        include_hidden: Export transforms whose ``visible`` is off.

    Returns:
        Counts and warnings.

    Raises:
        ValueError: When nothing is left to export or the suffix is unknown.
        OSError: When the file can't be written.
    """
    suffix = path.suffix.lower()
    if suffix not in (".glb", ".gltf"):
        msg = f"export needs a .glb or .gltf path, not {path.name}"
        raise ValueError(msg)
    result = ExportResult(path=path)
    names = _export_set(world, roots, include_hidden, result)
    if not names:
        msg = "nothing to export"
        raise ValueError(msg)

    scene = trimesh.Scene()
    base = str(scene.graph.base_frame)
    exported = set(names)
    world_matrices = _world_matrices(world)
    light_nodes: dict[str, int] = {}
    lights_json: list[dict[str, object]] = []
    camera_nodes: dict[str, int] = {}
    cameras_json: list[dict[str, object]] = []
    unlit_materials: set[str] = set()
    for name in names:
        transform = world.transforms[name]
        parent = transform.parent if transform.parent in exported else base
        matrix = (
            mat_from_trs(transform.position, transform.rotation, transform.scale)
            if parent != base
            else world_matrices[name]
        )
        mesh_names = world.meshes_on(name)
        geometries = [
            _geometry(world.meshes[mesh_name], result)
            for mesh_name in mesh_names
            if mesh_name in world.meshes
        ]
        geometries = [item for item in geometries if item is not None]
        if len(geometries) == 1:
            mesh_name, geometry = geometries[0]
            scene.add_geometry(
                geometry,
                node_name=name,
                geom_name=mesh_name,
                parent_node_name=parent,
                transform=matrix,
            )
        else:
            scene.graph.update(frame_to=name, frame_from=parent, matrix=matrix)
            for mesh_name, geometry in geometries:
                scene.add_geometry(
                    geometry,
                    node_name=mesh_name,
                    geom_name=mesh_name,
                    parent_node_name=name,
                    transform=np.eye(4),
                )
        result.meshes += len(geometries)
        for mesh_name, _geometry_item in geometries:
            if world.meshes[mesh_name].unlit:
                unlit_materials.add(mesh_name)
        result.transforms += 1
        for light_name in world.lights_on(name):
            light = world.lights.get(light_name)
            if light is None:
                continue
            node = (
                f"{light_name}{_LIGHT_SUFFIX}" if light_name in exported else light_name
            )
            scene.graph.update(
                frame_to=node,
                frame_from=name,
                matrix=_aim_matrix(world_matrices[name], light),
            )
            light_nodes[node] = len(lights_json)
            lights_json.append(_light_json(light))
            result.lights += 1
        for index, camera_name in enumerate(world.cameras_on(name)):
            camera = world.cameras.get(camera_name)
            if camera is None:
                continue
            # A camera looks along its node's -Z, as the transform does, so
            # the first camera rides the transform node itself.
            node = name
            if index > 0:
                node = camera_name
                scene.graph.update(frame_to=node, frame_from=name, matrix=np.eye(4))
            camera_nodes[node] = len(cameras_json)
            cameras_json.append(_camera_json(camera))
            result.cameras += 1

    document, binary, buffers = _export_trimesh(scene, suffix)
    _patch_document(
        document,
        light_nodes,
        lights_json,
        unlit_materials,
        camera_nodes,
        cameras_json,
    )
    if suffix == ".glb":
        path.write_bytes(join_glb(document, binary))
    else:
        path.write_text(json.dumps(document, indent=1), encoding="utf-8")
        for buffer_name, payload in buffers.items():
            (path.parent / buffer_name).write_bytes(payload)
    return result


def _export_set(
    world: SceneWorld,
    roots: Sequence[str] | None,
    include_hidden: bool,
    result: ExportResult,
) -> list[str]:
    """Return the transforms to export, parents before children."""
    if roots is None:
        names = _sorted_parents_first(world)
    else:
        names = world.descendant_transforms([r for r in roots if r in world.transforms])
    if include_hidden:
        return names
    hidden: set[str] = set()
    for name in names:
        transform = world.transforms[name]
        if not transform.visible or transform.parent in hidden:
            hidden.add(name)
    if hidden:
        result.warnings.append(
            f"{len(hidden)} hidden transform(s) left out; use Export with "
            "hidden objects to include them"
        )
    return [name for name in names if name not in hidden]


def _sorted_parents_first(world: SceneWorld) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def visit(name: str) -> None:
        if name in seen or name not in world.transforms:
            return
        parent = world.transforms[name].parent
        if parent:
            visit(parent)
        seen.add(name)
        ordered.append(name)

    for name in world.transforms:
        visit(name)
    return ordered


def _world_matrices(world: SceneWorld) -> dict[str, NDArray[np.float64]]:
    matrices: dict[str, NDArray[np.float64]] = {}
    for name in _sorted_parents_first(world):
        transform = world.transforms[name]
        local = mat_from_trs(transform.position, transform.rotation, transform.scale)
        parent = matrices.get(transform.parent)
        matrices[name] = local if parent is None else parent @ local
    return matrices


def _geometry(
    component: MeshComponent, result: ExportResult
) -> tuple[str, trimesh.Trimesh] | None:
    """Build the trimesh mesh for one mesh component, or ``None`` with a warning."""
    surface: MaterialSurface | None = None
    maps: ImportedMaps | None = None
    if component.kind == "file":
        try:
            from scene_viewport.mesh.importer import load_model

            model = load_model(component.source)
        except ModelImportError as exc:
            result.warnings.append(f"{component.name}: not exported ({exc})")
            return None
        buffers = model.meshes.get(component.source_mesh)
        if buffers is None:
            result.warnings.append(
                f"{component.name}: mesh {component.source_mesh!r} not in "
                f"{component.source.name}; not exported"
            )
            return None
        surface, maps = file_material(component.source, component.source_mesh)
    elif component.kind == "box":
        buffers = box(1.0, 1.0, 1.0)
    elif component.kind == "plane":
        buffers = plane(1.0, 1.0)
    elif component.kind == "sphere":
        buffers = uv_sphere(0.5, stacks=16, slices=24)
    else:
        result.warnings.append(f"{component.name}: unknown kind {component.kind!r}")
        return None
    mesh = trimesh.Trimesh(
        vertices=buffers.positions,
        faces=buffers.indices,
        vertex_normals=buffers.normals,
        process=False,
    )
    mesh.visual = _visual(component, buffers, surface, maps, result)
    return component.name, mesh


def _visual(
    component: MeshComponent,
    buffers: MeshBuffers,
    surface: MaterialSurface | None,
    maps: ImportedMaps | None,
    result: ExportResult,
) -> TextureVisuals | ColorVisuals:
    """Build the trimesh visual: PBR material with maps, or vertex colors."""
    albedo: Image.Image | None = None
    override = str(component.albedo_map).strip()
    if override and override != ".":
        try:
            with Image.open(component.albedo_map) as image:
                albedo = image.convert("RGBA")
        except (OSError, ValueError):
            result.warnings.append(
                f"{component.name}: albedo map {component.albedo_map.name} "
                "could not be read; exported without it"
            )
    elif maps is not None and maps.base_color is not None:
        albedo = Image.fromarray(maps.base_color, "RGBA")
    textured = albedo is not None or (maps is not None and maps.has_images())
    vertex_colors = surface is not None and surface.vertex_colors
    if not textured and buffers.colors is not None and vertex_colors:
        colors = np.clip(buffers.colors * 255.0, 0, 255).astype(np.uint8)
        return ColorVisuals(vertex_colors=colors)
    red, green, blue = _rgb_from_hex(component.color)
    emissive = surface.emissive if surface is not None else (0.0, 0.0, 0.0)
    material = PBRMaterial(
        name=component.name,
        baseColorFactor=[red, green, blue, component.opacity],
        baseColorTexture=albedo,
        metallicFactor=component.metalness,
        roughnessFactor=component.roughness,
        emissiveFactor=list(emissive) if any(emissive) else None,
        doubleSided=component.double_sided,
        alphaMode=None
        if component.alpha_mode == "opaque"
        else component.alpha_mode.upper(),
        alphaCutoff=(
            component.alpha_cutoff if component.alpha_mode == "mask" else None
        ),
    )
    if maps is not None:
        for kind, slot in _CORE_MAP_SLOTS.items():
            image = maps.image(kind)
            if image is not None:
                setattr(material, slot, Image.fromarray(image, "RGBA"))
        dropped = [
            kind
            for kind in maps.sampling
            if kind not in _EXPORTED_MAPS and maps.image(kind) is not None
        ]
        if dropped:
            result.warnings.append(
                f"{component.name}: {', '.join(sorted(dropped))} map(s) not exported "
                "(KHR_materials_* textures)"
            )
    return TextureVisuals(uv=buffers.texcoords, material=material)


def _rgb_from_hex(color: str) -> tuple[float, float, float]:
    text = color.lstrip("#")
    if len(text) != 6:
        return (0.8, 0.8, 0.8)
    try:
        red, green, blue = (int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return (0.8, 0.8, 0.8)
    return (red, green, blue)


def _aim_matrix(
    transform_world: NDArray[np.float64], light: LightComponent
) -> NDArray[np.float64]:
    """Local matrix for a light node whose -Z points at the scene origin.

    The viewport aims directional and spot lights at the origin, so the
    exported node is rotated the same way. Point lights need no aim.
    """
    if light.kind == "point":
        return np.eye(4)
    position = transform_world[:3, 3]
    if float(np.linalg.norm(position)) < 1e-9:
        return np.eye(4)
    # glTF lights shine along -Z; look_at_matrix points -Z at the origin.
    world = look_at_matrix(
        (float(position[0]), float(position[1]), float(position[2])),
        (0.0, 0.0, 0.0),
    )
    parent_inverse = np.linalg.inv(transform_world)
    return parent_inverse @ world


def _light_json(light: LightComponent) -> dict[str, object]:
    red, green, blue = _rgb_from_hex(light.color)
    entry: dict[str, object] = {
        "name": light.name,
        "type": light.kind,
        "color": [red, green, blue],
        "intensity": light.intensity,
    }
    if light.kind != "directional" and light.distance > 0:
        entry["range"] = light.distance
    if light.kind == "spot":
        entry["spot"] = {"innerConeAngle": 0.0, "outerConeAngle": _SPOT_OUTER_ANGLE}
    return entry


def _export_trimesh(
    scene: trimesh.Scene, suffix: str
) -> tuple[dict[str, object], bytes | None, dict[str, bytes]]:
    """Run trimesh's exporter and return the JSON document plus buffers."""
    if suffix == ".glb":
        data = scene.export(file_type="glb")
        if not isinstance(data, (bytes, bytearray)):
            msg = "trimesh produced no GLB bytes"
            raise ValueError(msg)
        document, binary = split_glb(bytes(data))
        return document, binary, {}
    files = scene.export(file_type="gltf")
    if not isinstance(files, dict):
        msg = "trimesh produced no glTF files"
        raise ValueError(msg)
    document: dict[str, object] | None = None
    buffers: dict[str, bytes] = {}
    for name, payload in files.items():
        if name.endswith(".gltf"):
            document = json.loads(bytes(payload).decode("utf-8"))
        else:
            buffers[name] = bytes(payload)
    if document is None:
        msg = "trimesh produced no glTF document"
        raise ValueError(msg)
    return document, None, buffers


def _camera_json(camera: CameraComponent) -> dict[str, object]:
    if camera.kind == "orthographic":
        half = camera.ortho_size * 0.5
        return {
            "name": camera.name,
            "type": "orthographic",
            "orthographic": {
                "xmag": half * 16 / 9,
                "ymag": half,
                "znear": camera.near,
                "zfar": camera.far,
            },
        }
    return {
        "name": camera.name,
        "type": "perspective",
        "perspective": {
            "yfov": math.radians(camera.fov),
            "znear": camera.near,
            "zfar": camera.far,
        },
    }


def _patch_document(
    document: dict[str, object],
    light_nodes: dict[str, int],
    lights: list[dict[str, object]],
    unlit_materials: set[str],
    camera_nodes: dict[str, int] | None = None,
    cameras: list[dict[str, object]] | None = None,
) -> None:
    """Add what trimesh doesn't write: lights, unlit materials, cameras."""
    used: list[str] = []
    nodes = document.get("nodes")
    if cameras and camera_nodes and isinstance(nodes, list):
        document["cameras"] = cameras
        for node in nodes:
            if isinstance(node, dict) and node.get("name") in camera_nodes:
                node["camera"] = camera_nodes[str(node["name"])]
    if lights and isinstance(nodes, list):
        used.append("KHR_lights_punctual")
        extensions = document.setdefault("extensions", {})
        if isinstance(extensions, dict):
            extensions["KHR_lights_punctual"] = {"lights": lights}
        for node in nodes:
            if isinstance(node, dict) and node.get("name") in light_nodes:
                node_extensions = node.setdefault("extensions", {})
                if isinstance(node_extensions, dict):
                    node_extensions["KHR_lights_punctual"] = {
                        "light": light_nodes[str(node["name"])]
                    }
    materials = document.get("materials")
    if unlit_materials and isinstance(materials, list):
        for material in materials:
            if isinstance(material, dict) and material.get("name") in unlit_materials:
                used.append("KHR_materials_unlit")
                material_extensions = material.setdefault("extensions", {})
                if isinstance(material_extensions, dict):
                    material_extensions["KHR_materials_unlit"] = {}
    if used:
        existing = document.get("extensionsUsed")
        existing = existing if isinstance(existing, list) else []
        document["extensionsUsed"] = list(dict.fromkeys([*existing, *used]))


__all__ = ["ExportResult", "export_scene"]
