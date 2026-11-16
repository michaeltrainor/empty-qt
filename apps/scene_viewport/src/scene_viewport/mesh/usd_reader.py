"""Read an OpenUSD stage as a model: nodes, triangle meshes, and materials.

The viewport draws ``file`` meshes from model files, so a stage from another
tool (a UsdSkel example, an asset library) is read like a glTF: every
``Xformable`` prim is a node, every ``Mesh`` and ``Cube`` / ``Sphere`` /
``Plane`` is a mesh keyed by its prim path, and the bound
``UsdPreviewSurface`` is the surface. The same mesh conversion will serve
the viewport when it draws from the live stage.

What is read, at the earliest authored time:

- Polygons are fan-triangulated; ``leftHanded`` faces are flipped and
  ``holeIndices`` dropped. Face-varying primvars (``st``, ``normals``,
  ``displayColor``) split the vertices per corner, with normals still
  computed over the shared points so the surface stays smooth.
- ``normals`` are used when authored on a polygonal mesh; subdivision
  surfaces are drawn as their control mesh with smooth computed normals.
- ``UsdPreviewSurface`` ``diffuseColor``, ``roughness``, ``metallic``,
  ``opacity`` (and ``opacityThreshold``), ``emissiveColor``, and the
  gprim's ``doubleSided``. A ``UsdUVTexture`` on ``diffuseColor``,
  ``normal``, ``emissiveColor``, ``occlusion``, ``roughness``, ``metallic``,
  and ``opacity`` becomes the matching map; roughness and metallic are
  packed into one glTF-style image. ``UsdPrimvarReader_float2`` picks the
  UV set, ``UsdTransform2d`` the UV transform, and ``wrapS`` / ``wrapT``
  the wrap modes. Texture files are read through the Ar resolver, so
  ``.usdz`` packages work.
- ``displayColor`` gives the surface color when constant, or vertex colors.
- ``UsdLux`` distant, sphere, and shaped lights, and ``UsdGeom.Camera``.
- The stage's ``upAxis`` and ``metersPerUnit`` are reported for the
  importer's conversion.

Skinning (``UsdSkel``) and blend shapes are not applied: skinned meshes
show their rest pose. ``GeomSubset`` material bindings are ignored; the
mesh's own binding is used.
"""

from __future__ import annotations

import io
import logging
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from pxr import Ar, Gf, Sdf, Tf, Usd, UsdGeom, UsdLux, UsdShade, UsdSkel

from scene_viewport.mesh.gltf_extras import (
    ImportedCamera,
    ImportedLight,
    MapSampling,
    TextureTransform,
)
from scene_viewport.mesh.primitives import box, plane, uv_sphere
from scene_viewport.mesh.types import AlphaMode, MapKind, MeshBuffers

if TYPE_CHECKING:
    from scene_viewport.mesh.importer import (
        ImportedMaps,
        ImportedModel,
        ImportedNode,
        MaterialSurface,
    )

_log = logging.getLogger(__name__)

#: File suffixes read as USD stages, lower case.
USD_MODEL_SUFFIXES = (".usd", ".usda", ".usdc", ".usdz")
#: ``UsdUVTexture`` wrap modes → pygfx ``TextureMap`` wrap names.
_WRAP_MODES = {
    "repeat": "repeat",
    "clamp": "clamp",
    "mirror": "mirror",
    "black": "clamp",
}
#: ``UsdUVTexture`` output names → channel index in an RGBA image.
_CHANNELS = {"r": 0, "g": 1, "b": 2, "a": 3}
#: ``UsdPreviewSurface`` inputs read as texture maps, and the map kind each
#: feeds. Roughness and metallic are packed into ``metallic_roughness``.
_TEXTURE_INPUTS: dict[str, MapKind] = {
    "diffuseColor": "base_color",
    "normal": "normal",
    "emissiveColor": "emissive",
    "occlusion": "occlusion",
}
#: Every surface input a texture may feed.
_TEXTURED_INPUTS = (*_TEXTURE_INPUTS, "roughness", "metallic", "opacity")

#: One attribute's values with their layout: ``vertex`` (one row per point),
#: ``corner`` (one row per face corner), or ``constant`` (one row).
type _Attribute = tuple[str, NDArray[np.float32]]
#: Decodes a resolved texture path to RGBA, or ``None``.
type _ImageLoader = Callable[[str], NDArray[np.uint8] | None]


def is_usd_model(path: Path) -> bool:
    """Return whether ``path`` has a suffix :func:`read_usd_model` opens.

    Args:
        path: Candidate file path.
    """
    return path.suffix.lower() in USD_MODEL_SUFFIXES


def linear_to_hex(color: object) -> str:
    """Format a linear RGB color, 0 to 1, as sRGB ``#rrggbb``.

    Args:
        color: Three floats, such as a ``Gf.Vec3f``.
    """
    out = []
    for c in cast(Iterable[float], color):
        c = max(0.0, min(1.0, float(c)))
        s = c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1.0 / 2.4) - 0.055
        out.append(f"{round(s * 255):02x}")
    return "#" + "".join(out)


def open_stage(path: Path) -> Usd.Stage:
    """Open the stage at ``path`` with every payload loaded.

    Args:
        path: Root layer.

    Raises:
        ValueError: When the file is not a USD layer.
    """
    try:
        stage = Usd.Stage.Open(str(path))
    except Tf.ErrorException as exc:
        raise ValueError(f"not a USD file: {path.name} ({exc})") from exc
    if stage is None:
        raise ValueError(f"not a USD file: {path.name}")
    return stage


def stage_files(path: Path) -> tuple[Path, ...]:
    """Return the layer and texture files the stage at ``path`` uses.

    Files inside a ``.usdz`` package are not listed; the package is the
    file that changes.

    Args:
        path: Root layer.

    Returns:
        Resolved paths without duplicates, the root layer excluded. Empty
        when the stage cannot be opened.
    """
    try:
        stage = open_stage(path)
    except ValueError:
        return ()
    files: list[Path] = []
    resolved_root = str(path.resolve())
    for layer in stage.GetUsedLayers():
        real = str(layer.realPath)
        if real and not layer.anonymous and "[" not in real and real != resolved_root:
            files.append(Path(real))
    for prim in stage.Traverse():
        if not prim.IsA(UsdShade.Shader):
            continue
        shader = UsdShade.Shader(prim)
        if shader.GetIdAttr().Get() != "UsdUVTexture":
            continue
        resolved = _asset_path(shader)
        if resolved and "[" not in resolved:
            files.append(Path(resolved))
    return tuple(dict.fromkeys(files))


def read_usd_model(path: Path) -> ImportedModel:
    """Read the stage at ``path`` as an :class:`ImportedModel`.

    Args:
        path: ``.usd``, ``.usda``, ``.usdc``, or ``.usdz`` file.

    Returns:
        Nodes for every ``Xformable`` prim (parents first), meshes keyed by
        prim path, their surfaces and texture images, lights, cameras, and
        the stage's up axis and units.

    Raises:
        ValueError: When the file is not a USD layer.
    """
    from scene_viewport.mesh.importer import ImportedModel

    stage = open_stage(path)
    reader = _StageReader(stage, path)
    reader.read()
    up_axis = UsdGeom.GetStageUpAxis(stage)
    return ImportedModel(
        path=path,
        nodes=tuple(reader.nodes),
        meshes=reader.meshes,
        materials=reader.materials,
        maps=reader.maps,
        warnings=tuple(reader.warnings()),
        lights=tuple(reader.lights),
        cameras=tuple(reader.cameras),
        up_axis="z" if up_axis == UsdGeom.Tokens.z else "y",
        meters_per_unit=float(UsdGeom.GetStageMetersPerUnit(stage)),
    )


# -- stage walk ---------------------------------------------------------------


@dataclass
class _StageReader:
    """One pass over a stage, collecting the model's parts."""

    stage: Usd.Stage
    path: Path
    nodes: list[ImportedNode] = field(default_factory=list)
    meshes: dict[str, MeshBuffers] = field(default_factory=dict)
    materials: dict[str, MaterialSurface | None] = field(default_factory=dict)
    maps: dict[str, ImportedMaps] = field(default_factory=dict)
    lights: list[ImportedLight] = field(default_factory=list)
    cameras: list[ImportedCamera] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    skinned: int = 0
    subdivided: int = 0
    subsets: int = 0
    names: dict[Sdf.Path, str] = field(default_factory=dict)
    used: set[str] = field(default_factory=set)
    images: dict[str, NDArray[np.uint8] | None] = field(default_factory=dict)

    def read(self) -> None:
        """Walk the stage, instance proxies included, parents first."""
        from scene_viewport.mesh.importer import ImportedNode

        time = Usd.TimeCode.EarliestTime()
        iterator = iter(Usd.PrimRange.Stage(self.stage, Usd.TraverseInstanceProxies()))
        for prim in iterator:
            if prim.IsPseudoRoot():
                continue
            if not prim.IsA(UsdGeom.Imageable):
                iterator.PruneChildren()
                continue
            imageable = UsdGeom.Imageable(prim)
            if imageable.GetPurposeAttr().Get() == UsdGeom.Tokens.guide:
                iterator.PruneChildren()
                continue
            if prim.IsA(UsdGeom.Subset):
                binding = UsdShade.MaterialBindingAPI(prim).GetDirectBindingRel()
                if binding and binding.GetTargets():
                    self.subsets += 1
                continue
            is_scope = prim.IsA(UsdGeom.Scope)
            if not prim.IsA(UsdGeom.Xformable) and not is_scope:
                continue
            if is_scope and not _has_xformable(prim):
                iterator.PruneChildren()
                continue
            name = self._claim(prim.GetName())
            self.names[prim.GetPath()] = name
            if is_scope:
                matrix = np.eye(4)
            else:
                local = UsdGeom.Xformable(prim).GetLocalTransformation(time)
                matrix = np.asarray(local, dtype=np.float64).T
            visibility = imageable.GetVisibilityAttr().Get(time)
            self.nodes.append(
                ImportedNode(
                    name=name,
                    parent=self._parent_name(prim),
                    matrix=matrix,
                    mesh=self._read_gprim(prim, time),
                    visible=visibility != UsdGeom.Tokens.invisible,
                )
            )
            if prim.HasAPI(UsdLux.LightAPI):
                self.lights.append(_light(prim, name, time))
            elif prim.IsA(UsdGeom.Camera):
                self.cameras.append(_camera(prim, name, time))

    def warnings(self) -> list[str]:
        """Texture problems, then one note per unsupported feature."""
        name = self.path.name
        notes = list(self.problems)
        if self.skinned:
            plural = "mesh shows" if self.skinned == 1 else "meshes show"
            notes.append(
                f"{name}: skeletons are not supported; {self.skinned} skinned "
                f"{plural} the rest pose"
            )
        if self.subdivided:
            plural = "surface is" if self.subdivided == 1 else "surfaces are"
            notes.append(
                f"{name}: {self.subdivided} subdivision {plural} drawn as the "
                "control mesh"
            )
        if self.subsets:
            plural = "binding" if self.subsets == 1 else "bindings"
            notes.append(
                f"{name}: {self.subsets} GeomSubset material {plural} ignored; "
                "each mesh uses its own material"
            )
        return notes

    def _claim(self, stem: str) -> str:
        name, index = stem, 1
        while name in self.used:
            name = f"{stem}_{index}"
            index += 1
        self.used.add(name)
        return name

    def _parent_name(self, prim: Usd.Prim) -> str:
        parent = prim.GetParent()
        while parent and not parent.IsPseudoRoot():
            name = self.names.get(parent.GetPath())
            if name is not None:
                return name
            parent = parent.GetParent()
        return ""

    def _read_gprim(self, prim: Usd.Prim, time: Usd.TimeCode) -> str:
        """Convert a gprim to buffers under its prim path; ``""`` otherwise."""
        if not prim.IsA(UsdGeom.Gprim):
            return ""
        key = str(prim.GetPath())
        textures = _textures(prim)
        if prim.IsA(UsdGeom.Mesh):
            mesh = _MeshReader(UsdGeom.Mesh(prim), time)
            if mesh.subdivision_scheme() != UsdGeom.Tokens.none:
                self.subdivided += 1
            if _is_skinned(prim):
                self.skinned += 1
            buffers = mesh.buffers(_uv_sets(textures))
        else:
            buffers = _primitive_buffers(prim, time)
        if buffers is None:
            _log.info("skipping empty gprim %s in %s", key, self.path.name)
            return ""
        surface = _Surface.of(
            prim, textures, has_vertex_colors=buffers.colors is not None
        )
        self.materials[key] = surface.material
        maps = surface.maps(self._image, self.path.name, self.problems)
        if maps is not None:
            self.maps[key] = maps
            if maps.normal is not None:
                buffers = _with_tangents(buffers)
        self.meshes[key] = buffers
        return key

    def _image(self, resolved: str) -> NDArray[np.uint8] | None:
        """Decode a texture once per stage, through the Ar resolver."""
        if resolved not in self.images:
            self.images[resolved] = _read_image(resolved)
        return self.images[resolved]


def _has_xformable(prim: Usd.Prim) -> bool:
    return any(
        child.IsA(UsdGeom.Xformable)
        for child in Usd.PrimRange(prim, Usd.TraverseInstanceProxies())
        if child != prim
    )


def _is_skinned(prim: Usd.Prim) -> bool:
    """Whether a skeleton, bound here or on an ancestor, deforms ``prim``."""
    if not prim.HasAPI(UsdSkel.BindingAPI):
        return False
    binding = UsdSkel.BindingAPI(prim)
    if binding.GetJointIndicesPrimvar().HasAuthoredValue():
        return True
    skeleton = binding.GetInheritedSkeleton()
    return bool(skeleton and skeleton.GetPrim().IsValid())


def _uv_sets(textures: Iterable[_Texture]) -> tuple[str, str]:
    """Return the primvar names of a mesh's first and second UV set.

    The set the base color texture reads comes first, so it is the one
    plain texturing uses; another set a texture reads is second.
    """
    used: list[str] = []
    base = ""
    for texture in textures:
        if texture.varname not in used:
            used.append(texture.varname)
        if texture.input == "diffuseColor" and not base:
            base = texture.varname
    if base:
        used.remove(base)
        used.insert(0, base)
    if not used:
        used.append("st")
    return used[0], used[1] if len(used) > 1 else ""


def _with_tangents(buffers: MeshBuffers) -> MeshBuffers:
    from scene_viewport.mesh.importer import vertex_tangents

    return MeshBuffers(
        positions=buffers.positions,
        normals=buffers.normals,
        indices=buffers.indices,
        texcoords=buffers.texcoords,
        tangents=vertex_tangents(
            buffers.positions, buffers.normals, buffers.texcoords, buffers.indices
        ),
        texcoords1=buffers.texcoords1,
        colors=buffers.colors,
    )


# -- meshes -------------------------------------------------------------------


class _MeshReader:
    """Read one ``UsdGeom.Mesh`` into triangle buffers."""

    def __init__(self, mesh: UsdGeom.Mesh, time: Usd.TimeCode) -> None:
        self.mesh = mesh
        self.time = time
        self.primvars = UsdGeom.PrimvarsAPI(mesh.GetPrim())

    def subdivision_scheme(self) -> str:
        """Return the ``subdivisionScheme`` token, ``catmullClark`` by default."""
        return str(self.mesh.GetSubdivisionSchemeAttr().Get(self.time) or "")

    def buffers(self, uv_sets: tuple[str, str]) -> MeshBuffers | None:
        """Return the triangle buffers, or ``None`` for an empty mesh.

        Args:
            uv_sets: Primvar names for the first and (optional) second UV
                set.
        """
        points = _array(self.mesh.GetPointsAttr().Get(self.time), np.float32, 3)
        counts = _array(self.mesh.GetFaceVertexCountsAttr().Get(self.time), np.int64)
        indices = _array(self.mesh.GetFaceVertexIndicesAttr().Get(self.time), np.int64)
        if points is None or counts is None or indices is None or not len(points):
            return None
        if indices.size != int(counts.sum()) or np.any(indices >= len(points)):
            return None
        holes = _array(self.mesh.GetHoleIndicesAttr().Get(self.time), np.int64)
        orientation = self.mesh.GetOrientationAttr().Get(self.time)
        corners = triangulate(counts, holes, orientation == UsdGeom.Tokens.leftHanded)
        if not len(corners):
            return None
        welded = indices[corners]
        attributes: dict[str, _Attribute] = {}
        for slot, name in (("uv0", uv_sets[0]), ("uv1", uv_sets[1])):
            entry = (
                self._primvar(name, 2, len(points), counts, indices) if name else None
            )
            if entry is not None and entry[0] != "constant":
                attributes[slot] = entry
        colors = self._primvar("displayColor", 3, len(points), counts, indices)
        if colors is not None and colors[0] != "constant":
            layout, values = colors
            alpha = np.ones((len(values), 1), dtype=np.float32)
            attributes["colors"] = (layout, np.hstack([values, alpha]))
        normals = self._normals(points, welded, counts, indices)
        if normals is None:
            from scene_viewport.mesh.importer import vertex_normals

            normals = vertex_normals(
                points, np.ascontiguousarray(welded, dtype=np.uint32)
            )
            attributes["normals"] = ("vertex", normals)
        else:
            attributes["normals"] = normals
        per_corner = any(layout == "corner" for layout, _ in attributes.values())

        def pick(name: str) -> NDArray[np.float32] | None:
            entry = attributes.get(name)
            if entry is None:
                return None
            layout, values = entry
            if per_corner and layout == "vertex":
                return np.ascontiguousarray(values[indices])
            return np.ascontiguousarray(values)

        positions = points[indices] if per_corner else points
        triangles = corners if per_corner else welded
        texcoords = pick("uv0")
        picked_normals = pick("normals")
        assert picked_normals is not None
        return MeshBuffers(
            positions=np.ascontiguousarray(positions, dtype=np.float32),
            normals=picked_normals,
            indices=np.ascontiguousarray(triangles, dtype=np.uint32),
            texcoords=(
                texcoords
                if texcoords is not None
                else np.zeros((len(positions), 2), dtype=np.float32)
            ),
            texcoords1=pick("uv1"),
            colors=pick("colors"),
        )

    def _primvar(
        self,
        name: str,
        width: int,
        point_count: int,
        counts: NDArray[np.int64],
        indices: NDArray[np.int64],
    ) -> _Attribute | None:
        """Read a primvar with its layout; uniform values are spread per corner."""
        primvar = self.primvars.FindPrimvarWithInheritance(name)
        if not primvar or not primvar.HasAuthoredValue():
            return None
        raw = (
            primvar.ComputeFlattened(self.time)
            if primvar.IsIndexed()
            else primvar.Get(self.time)
        )
        values = _array(raw, np.float32, width)
        if values is None or not len(values):
            return None
        return _layout(values, primvar.GetInterpolation(), point_count, counts, indices)

    def _normals(
        self,
        points: NDArray[np.float32],
        welded: NDArray[np.int64],
        counts: NDArray[np.int64],
        indices: NDArray[np.int64],
    ) -> _Attribute | None:
        """Authored normals of a polygonal mesh, or ``None`` to compute them."""
        if self.subdivision_scheme() != UsdGeom.Tokens.none:
            return None
        entry = self._primvar("normals", 3, len(points), counts, indices)
        if entry is None:
            values = _array(self.mesh.GetNormalsAttr().Get(self.time), np.float32, 3)
            if values is not None and len(values):
                interpolation = self.mesh.GetNormalsInterpolation()
                entry = _layout(values, interpolation, len(points), counts, indices)
        if entry is None or entry[0] == "constant":
            return None
        return entry


def _layout(
    values: NDArray[np.float32],
    interpolation: str,
    point_count: int,
    counts: NDArray[np.int64],
    indices: NDArray[np.int64],
) -> _Attribute | None:
    """Pair attribute values with their layout, or ``None`` if the size is off."""
    if interpolation == UsdGeom.Tokens.constant:
        return ("constant", values[:1])
    if interpolation == UsdGeom.Tokens.uniform:
        if len(values) != len(counts):
            return None
        return ("corner", np.repeat(values, counts, axis=0))
    if interpolation == UsdGeom.Tokens.faceVarying:
        return ("corner", values) if len(values) == len(indices) else None
    # vertex and varying
    return ("vertex", values) if len(values) == point_count else None


def triangulate(
    counts: NDArray[np.int64],
    holes: NDArray[np.int64] | None,
    left_handed: bool,
) -> NDArray[np.int64]:
    """Fan-triangulate polygon faces into corner index triples.

    Args:
        counts: Vertices per face (``faceVertexCounts``).
        holes: Indices of faces to drop (``holeIndices``), or ``None``.
        left_handed: Flip every triangle's winding.

    Returns:
        ``(T, 3)`` indices into the flat corner list (positions in
        ``faceVertexIndices``), counter-clockwise for a right-handed mesh.
        Faces with fewer than three vertices are dropped.
    """
    if not len(counts):
        return np.zeros((0, 3), dtype=np.int64)
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    keep = counts >= 3
    if holes is not None and len(holes):
        valid = holes[(holes >= 0) & (holes < len(counts))]
        keep[valid] = False
    per_face = np.where(keep, counts - 2, 0)
    total = int(per_face.sum())
    if total == 0:
        return np.zeros((0, 3), dtype=np.int64)
    face = np.repeat(np.arange(len(counts)), per_face)
    first = np.repeat(np.cumsum(per_face) - per_face, per_face)
    step = np.arange(total) - first
    start = starts[face]
    triangles = np.column_stack([start, start + step + 1, start + step + 2])
    if left_handed:
        triangles = triangles[:, [0, 2, 1]]
    return triangles


def _primitive_buffers(prim: Usd.Prim, time: Usd.TimeCode) -> MeshBuffers | None:
    """Tessellate ``Cube`` / ``Sphere`` / ``Plane`` at their authored size."""
    if prim.IsA(UsdGeom.Cube):
        size = float(UsdGeom.Cube(prim).GetSizeAttr().Get(time) or 2.0)
        return box(size, size, size)
    if prim.IsA(UsdGeom.Sphere):
        radius = float(UsdGeom.Sphere(prim).GetRadiusAttr().Get(time) or 1.0)
        return uv_sphere(radius, stacks=16, slices=24)
    if prim.IsA(UsdGeom.Plane):
        geom = UsdGeom.Plane(prim)
        width = float(geom.GetWidthAttr().Get(time) or 2.0)
        length = float(geom.GetLengthAttr().Get(time) or 2.0)
        return plane(width, length)
    return None


def _array(value: object, dtype: type, width: int | None = None) -> Any:
    """Convert a ``Vt`` array to numpy, or ``None`` when absent or ill-shaped."""
    if value is None:
        return None
    array = np.asarray(value, dtype=dtype)
    if width is None:
        return array.reshape(-1)
    if array.ndim != 2 or array.shape[1] < width:
        return None
    return np.ascontiguousarray(array[:, :width])


# -- materials ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Texture:
    """A ``UsdUVTexture`` feeding one ``UsdPreviewSurface`` input."""

    input: str
    output: str
    resolved: str
    varname: str
    sampling: MapSampling


@dataclass(frozen=True, slots=True)
class _Surface:
    """What a gprim's bound ``UsdPreviewSurface`` and ``displayColor`` say."""

    material: MaterialSurface | None
    textures: tuple[_Texture, ...]
    emissive: tuple[float, float, float]

    @staticmethod
    def of(
        prim: Usd.Prim, textures: tuple[_Texture, ...], *, has_vertex_colors: bool
    ) -> _Surface:
        """Read the surface of ``prim``.

        Args:
            prim: The gprim.
            textures: Its textures, from :func:`_textures`.
            has_vertex_colors: Whether the mesh buffers carry colors.
        """
        from scene_viewport.mesh.importer import MaterialSurface

        time = Usd.TimeCode.EarliestTime()
        double_sided = bool(UsdGeom.Gprim(prim).GetDoubleSidedAttr().Get(time))
        shader = _surface_shader(prim)
        if shader is None:
            display = _display_color(prim, time)
            if display is None:
                return _Surface(None, (), (0.0, 0.0, 0.0))
            return _Surface(
                MaterialSurface(
                    color=linear_to_hex(display),
                    roughness=0.6,
                    metalness=0.0,
                    double_sided=double_sided,
                    vertex_colors=has_vertex_colors,
                ),
                (),
                (0.0, 0.0, 0.0),
            )

        def value(name: str, default: Any) -> Any:
            shader_input = shader.GetInput(name)
            got = shader_input.Get(time) if shader_input else None
            return default if got is None else got

        def connected(name: str) -> bool:
            shader_input = shader.GetInput(name)
            return bool(shader_input) and shader_input.HasConnectedSource()

        if connected("diffuseColor"):
            reader_color = _primvar_reader_color(shader, prim, time)
            color = "#ffffff" if reader_color is None else linear_to_hex(reader_color)
        else:
            color = linear_to_hex(value("diffuseColor", Gf.Vec3f(0.18, 0.18, 0.18)))
        opacity = 1.0 if connected("opacity") else float(value("opacity", 1.0))
        threshold = float(value("opacityThreshold", 0.0))
        alpha_mode: AlphaMode = "opaque"
        if threshold > 0.0:
            alpha_mode = "mask"
        elif connected("opacity") or opacity < 1.0:
            alpha_mode = "blend"
        if connected("emissiveColor"):
            emissive = (1.0, 1.0, 1.0)
        else:
            emissive = _rgb(value("emissiveColor", Gf.Vec3f(0.0, 0.0, 0.0)))
        roughness = 1.0 if connected("roughness") else float(value("roughness", 0.5))
        if connected("metallic"):
            metalness = 1.0
        elif value("useSpecularWorkflow", 0):
            metalness = 0.0
        else:
            metalness = float(value("metallic", 0.0))
        return _Surface(
            MaterialSurface(
                color=color,
                roughness=min(max(roughness, 0.0), 1.0),
                metalness=min(max(metalness, 0.0), 1.0),
                opacity=min(max(opacity, 0.0), 1.0),
                alpha_mode=alpha_mode,
                alpha_cutoff=min(threshold, 1.0) if threshold > 0.0 else 0.5,
                double_sided=double_sided,
                emissive=emissive,
                vertex_colors=has_vertex_colors,
            ),
            textures,
            emissive,
        )

    def maps(
        self, image: _ImageLoader, file_name: str, problems: list[str]
    ) -> ImportedMaps | None:
        """Decode the textures into an :class:`ImportedMaps`, if any load.

        Args:
            image: Decodes a resolved texture path, caching per stage.
            file_name: Stage file name for problem messages.
            problems: Receives one message per unreadable image.
        """
        from scene_viewport.mesh.importer import ImportedMaps

        images: dict[MapKind, NDArray[np.uint8] | None] = {}
        sampling: dict[MapKind, MapSampling] = {}
        by_input = {texture.input: texture for texture in self.textures}
        for name, kind in _TEXTURE_INPUTS.items():
            texture = by_input.get(name)
            if texture is None:
                continue
            pixels = image(texture.resolved)
            if pixels is None:
                image_name = Path(texture.resolved).name
                problems.append(f"{file_name}: cannot read image {image_name}")
                continue
            images[kind] = pixels
            sampling[kind] = texture.sampling
        base = images.get("base_color")
        opacity = by_input.get("opacity")
        if (
            base is not None
            and opacity is not None
            and opacity.resolved != by_input["diffuseColor"].resolved
        ):
            alpha = image(opacity.resolved)
            if alpha is not None:
                channel = _CHANNELS.get(opacity.output, 3)
                base = base.copy()
                base[..., 3] = _fit(alpha, base.shape[:2])[..., channel]
                images["base_color"] = base
        packed = _pack_roughness_metallic(by_input, image)
        if packed is not None:
            images["metallic_roughness"], sampling["metallic_roughness"] = packed
        if not images:
            return None
        return ImportedMaps(**images, emissive_factor=self.emissive, sampling=sampling)


def _textures(prim: Usd.Prim) -> tuple[_Texture, ...]:
    """Return the textures connected to the bound surface's inputs."""
    shader = _surface_shader(prim)
    if shader is None:
        return ()
    found: list[_Texture] = []
    for name in _TEXTURED_INPUTS:
        shader_input = shader.GetInput(name)
        if not shader_input or not shader_input.HasConnectedSource():
            continue
        source, output, _ = shader_input.GetConnectedSource()
        texture = _texture(UsdShade.Shader(source), name, str(output))
        if texture is not None:
            found.append(texture)
    return tuple(found)


def _pack_roughness_metallic(
    by_input: Mapping[str, _Texture], image: _ImageLoader
) -> tuple[NDArray[np.uint8], MapSampling] | None:
    """Pack USD's separate roughness and metallic textures glTF-style.

    Roughness goes to green and metalness to blue, the channels pygfx
    samples. A missing map is white, so the material's factor stands.
    """
    roughness = by_input.get("roughness")
    metallic = by_input.get("metallic")
    source = roughness or metallic
    if source is None:
        return None
    shape: tuple[int, int] | None = None
    channels: dict[int, NDArray[np.uint8]] = {}
    for slot, texture in ((1, roughness), (2, metallic)):
        if texture is None:
            continue
        pixels = image(texture.resolved)
        if pixels is None:
            continue
        if shape is None:
            shape = (pixels.shape[0], pixels.shape[1])
        channels[slot] = _fit(pixels, shape)[..., _CHANNELS.get(texture.output, 0)]
    if shape is None:
        return None
    packed = np.full((*shape, 4), 255, dtype=np.uint8)
    packed[..., 0] = 0
    for slot, values in channels.items():
        packed[..., slot] = values
    return packed, source.sampling


def _fit(pixels: NDArray[np.uint8], shape: tuple[int, int]) -> NDArray[np.uint8]:
    """Resize an RGBA image to ``(height, width)`` when it differs."""
    if (pixels.shape[0], pixels.shape[1]) == shape:
        return pixels
    resized = Image.fromarray(pixels, "RGBA").resize((shape[1], shape[0]))
    return np.asarray(resized, dtype=np.uint8)


def _surface_shader(prim: Usd.Prim) -> UsdShade.Shader | None:
    """Return the bound material's ``UsdPreviewSurface`` shader, if any."""
    material, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    if not material:
        return None
    surface = material.ComputeSurfaceSource()[0]
    if not surface or surface.GetIdAttr().Get() != "UsdPreviewSurface":
        return None
    return surface


def _asset_path(shader: UsdShade.Shader) -> str:
    """Return a ``UsdUVTexture``'s resolved file path, or its raw path."""
    asset = shader.GetInput("file")
    value = asset.Get() if asset else None
    resolved = str(getattr(value, "resolvedPath", "") or "")
    return resolved or str(getattr(value, "path", "") or "")


def _texture(shader: UsdShade.Shader, input_name: str, output: str) -> _Texture | None:
    """Describe a ``UsdUVTexture`` shader, following its ``st`` connection."""
    if shader.GetIdAttr().Get() != "UsdUVTexture":
        return None
    resolved = _asset_path(shader)
    if not resolved:
        return None

    def wrap(name: str) -> str:
        shader_input = shader.GetInput(name)
        token = str(shader_input.Get() or "") if shader_input else ""
        return _WRAP_MODES.get(token, "repeat")

    varname = "st"
    transform: TextureTransform | None = None
    st = shader.GetInput("st")
    if st and st.HasConnectedSource():
        source, _, _ = st.GetConnectedSource()
        upstream = UsdShade.Shader(source)
        if upstream.GetIdAttr().Get() == "UsdTransform2d":
            transform = _transform2d(upstream)
            inner = upstream.GetInput("in")
            if inner and inner.HasConnectedSource():
                source, _, _ = inner.GetConnectedSource()
                upstream = UsdShade.Shader(source)
        if str(upstream.GetIdAttr().Get() or "").startswith("UsdPrimvarReader"):
            name_input = upstream.GetInput("varname")
            name = name_input.Get() if name_input else None
            if name:
                varname = str(name)
    return _Texture(
        input=input_name,
        output=output,
        resolved=resolved,
        varname=varname,
        sampling=MapSampling(
            texture=0, wrap_s=wrap("wrapS"), wrap_t=wrap("wrapT"), transform=transform
        ),
    )


def _transform2d(shader: UsdShade.Shader) -> TextureTransform | None:
    def read(name: str, default: Any) -> Any:
        shader_input = shader.GetInput(name)
        value = shader_input.Get() if shader_input else None
        return default if value is None else value

    translation = read("translation", Gf.Vec2f(0.0, 0.0))
    scale = read("scale", Gf.Vec2f(1.0, 1.0))
    transform = TextureTransform(
        offset=(float(translation[0]), float(translation[1])),
        rotation=math.radians(float(read("rotation", 0.0))),
        scale=(float(scale[0]), float(scale[1])),
    )
    return None if transform.is_identity else transform


def _primvar_reader_color(
    shader: UsdShade.Shader, prim: Usd.Prim, time: Usd.TimeCode
) -> Gf.Vec3f | None:
    """Return the constant color a ``UsdPrimvarReader`` on ``diffuseColor`` reads.

    ``None`` when the input is fed by a texture, or the primvar varies.
    """
    diffuse = shader.GetInput("diffuseColor")
    source, _, _ = diffuse.GetConnectedSource()
    reader = UsdShade.Shader(source)
    if not str(reader.GetIdAttr().Get() or "").startswith("UsdPrimvarReader"):
        return None
    name_input = reader.GetInput("varname")
    name = str(name_input.Get() or "") if name_input else ""
    primvar = UsdGeom.PrimvarsAPI(prim).FindPrimvarWithInheritance(name)
    if primvar and primvar.HasAuthoredValue():
        values = primvar.Get(time)
        if primvar.GetInterpolation() != UsdGeom.Tokens.constant and len(values) != 1:
            return None
        if values:
            return Gf.Vec3f(*(float(c) for c in values[0]))
    fallback = reader.GetInput("fallback")
    value = fallback.Get() if fallback else None
    return Gf.Vec3f(*(float(c) for c in value)) if value is not None else None


def _display_color(prim: Usd.Prim, time: Usd.TimeCode) -> Gf.Vec3f | None:
    """Return a constant (or single-value) ``displayColor``."""
    primvar = UsdGeom.PrimvarsAPI(prim).FindPrimvarWithInheritance("displayColor")
    if not primvar or not primvar.HasAuthoredValue():
        return None
    values = primvar.Get(time)
    if not values:
        return None
    if primvar.GetInterpolation() != UsdGeom.Tokens.constant and len(values) != 1:
        return None
    return Gf.Vec3f(*(float(c) for c in values[0]))


def _rgb(value: object) -> tuple[float, float, float]:
    red, green, blue = (float(c) for c in cast(Iterable[float], value))
    return (red, green, blue)


def _read_image(resolved: str) -> NDArray[np.uint8] | None:
    """Decode an image asset to RGBA, top row first, through the Ar resolver."""
    try:
        asset = Ar.GetResolver().OpenAsset(Ar.ResolvedPath(resolved))
        if asset is None:
            return None
        with Image.open(io.BytesIO(asset.GetBuffer())) as image:
            return np.array(image.convert("RGBA"), dtype=np.uint8)
    except (OSError, ValueError, Tf.ErrorException):
        # Pillow raises for truncated or foreign data; Ar for bad paths.
        return None


# -- lights and cameras -------------------------------------------------------


def _light(prim: Usd.Prim, node: str, time: Usd.TimeCode) -> ImportedLight:
    """Read a ``UsdLux`` light: distant is directional, shaped is a spot."""
    lux = UsdLux.LightAPI(prim)
    if prim.IsA(UsdLux.DistantLight):
        kind = "directional"
    elif prim.HasAPI(UsdLux.ShapingAPI):
        kind = "spot"
    else:
        kind = "point"
    color = lux.GetColorAttr().Get(time) or Gf.Vec3f(1.0, 1.0, 1.0)
    intensity = float(lux.GetIntensityAttr().Get(time) or 1.0)
    exposure = float(lux.GetExposureAttr().Get(time) or 0.0)
    cone = math.pi / 4
    if kind == "spot":
        angle = UsdLux.ShapingAPI(prim).GetShapingConeAngleAttr().Get(time)
        if angle is not None:
            cone = math.radians(float(angle))
    return ImportedLight(
        node=node,
        kind=kind,
        color=_rgb(color),
        intensity=intensity * 2.0**exposure,
        outer_cone=cone,
    )


def _camera(prim: Usd.Prim, node: str, time: Usd.TimeCode) -> ImportedCamera:
    """Read a ``UsdGeom.Camera``'s lens; the node itself carries the aim."""
    geom = UsdGeom.Camera(prim)
    clipping = geom.GetClippingRangeAttr().Get(time)
    near, far = (float(clipping[0]), float(clipping[1])) if clipping else (0.1, 0.0)
    aperture = float(geom.GetVerticalApertureAttr().Get(time) or 15.2908)
    if geom.GetProjectionAttr().Get(time) == UsdGeom.Tokens.orthographic:
        # Orthographic apertures are in tenths of scene units.
        return ImportedCamera(
            node=node, kind="orthographic", ymag=aperture / 20.0, znear=near, zfar=far
        )
    focal = float(geom.GetFocalLengthAttr().Get(time) or 50.0)
    return ImportedCamera(
        node=node,
        kind="perspective",
        yfov=2.0 * math.atan(aperture / (2.0 * focal)),
        znear=near,
        zfar=far,
    )
