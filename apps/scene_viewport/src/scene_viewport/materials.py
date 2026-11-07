"""Build pygfx mesh materials: the one place a surface feature is rendered.

The importer's :class:`~scene_viewport.mesh.importer.MaterialSurface` is the
one place a feature is read from a file; this module is where it becomes a
pygfx material. Adding a surface feature means one field there, one line
here, and a test.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypedDict

import pygfx as gfx

from scene_viewport.components import MapKind, MeshComponent
from scene_viewport.mesh.gltf_extras import SurfaceExtensions
from scene_viewport.mesh.importer import ImportedMaps, MaterialSurface

#: Maps holding colors (sRGB); the others hold linear data.
COLOR_MAPS = frozenset({"base_color", "emissive", "specular_color", "sheen_color"})

#: Looks up the cached pygfx texture for one map kind, or ``None``.
TextureLookup = Callable[[MapKind], gfx.Texture | None]

#: ``KHR_materials_*`` maps → the ``MeshPhysicalMaterial`` attribute they set.
_PHYSICAL_MAP_ATTRIBUTES: dict[MapKind, str] = {
    "clearcoat": "clearcoat_map",
    "clearcoat_roughness": "clearcoat_roughness_map",
    "clearcoat_normal": "clearcoat_normal_map",
    "specular": "specular_intensity_map",
    "specular_color": "specular_map",
    "sheen_color": "sheen_color_map",
    "sheen_roughness": "sheen_roughness_map",
    "iridescence": "iridescence_map",
    "iridescence_thickness": "iridescence_thickness_map",
    "anisotropy": "anisotropy_map",
}


class SurfaceKwargs(TypedDict):
    """Constructor arguments shared by lit and unlit mesh materials."""

    side: str
    opacity: float
    alpha_mode: str
    alpha_test: float


def surface_kwargs(component: MeshComponent) -> SurfaceKwargs:
    """Material constructor arguments for sides, opacity, and alpha.

    ``opaque`` ignores alpha, ``mask`` discards fragments below the cutoff,
    and ``blend`` composites with the over operator. Passed at construction
    because pygfx types the ``alpha_mode`` setter with its own enum class.

    Args:
        component: The mesh's editable surface fields.
    """
    kwargs: SurfaceKwargs = {
        "side": "both" if component.double_sided else "front",
        "opacity": component.opacity,
        "alpha_mode": "solid",
        "alpha_test": 0.0,
    }
    if component.alpha_mode == "blend":
        kwargs["alpha_mode"] = "blend"
    elif component.alpha_mode == "mask":
        kwargs["alpha_test"] = max(component.alpha_cutoff, 1e-6)
    return kwargs


def texture_map(
    texture: gfx.Texture,
    kind: MapKind,
    maps: ImportedMaps | None,
) -> gfx.TextureMap:
    """Wrap ``texture`` with the file's UV set, wrap modes, and transform.

    Args:
        texture: The decoded image.
        kind: Which map this is, to look up its sampling.
        maps: The mesh's maps, or ``None`` for plain repeat sampling.
    """
    sampling = maps.sampling.get(kind) if maps is not None else None
    if sampling is None:
        return gfx.TextureMap(texture)
    result = gfx.TextureMap(
        texture,
        uv_channel=sampling.uv_channel,
        wrap_s=sampling.wrap_s,
        wrap_t=sampling.wrap_t,
    )
    if sampling.transform is not None:
        result.offset = sampling.transform.offset
        result.rotation = sampling.transform.rotation
        result.scale = sampling.transform.scale
        result.update_matrix()
    return result


def build_material(
    component: MeshComponent,
    *,
    surface: MaterialSurface | None,
    maps: ImportedMaps | None,
    albedo: gfx.Texture | None,
    texture_for: TextureLookup,
    textured: bool,
    env_map: gfx.Texture | None,
    env_map_intensity: float,
) -> gfx.MeshStandardMaterial | gfx.MeshBasicMaterial:
    """Build the material for the shaded and textured draw modes.

    Unlit meshes get a basic material. Meshes whose file uses a
    ``KHR_materials_*`` extension pygfx can render (ior, clearcoat,
    specular, sheen, iridescence, anisotropy) get a physical material;
    everything else a standard PBR one. Vertex colors show when the mesh
    carries them and no base color texture is in use.

    Args:
        component: Editable surface fields (color, roughness, alpha, ...).
        surface: What the model file says, or ``None`` for primitives.
        maps: The mesh's texture images, or ``None``.
        albedo: The base color texture to use, or ``None``.
        texture_for: Returns the cached texture for a map kind.
        textured: Whether the textured draw mode is active.
        env_map: Scene environment map for reflections.
        env_map_intensity: Strength of ``env_map``.
    """
    extensions = surface.extensions if surface is not None else None
    base = surface_kwargs(component)
    material: gfx.MeshStandardMaterial | gfx.MeshBasicMaterial
    if component.unlit:
        material = gfx.MeshBasicMaterial(color=component.color, pick_write=True, **base)
    else:
        physical = extensions is not None and extensions.is_physical
        cls = gfx.MeshPhysicalMaterial if physical else gfx.MeshStandardMaterial
        material = cls(
            color=component.color,
            roughness=component.roughness,
            metalness=component.metalness,
            env_map=env_map,
            env_map_intensity=env_map_intensity,
            pick_write=True,
            **base,
        )
    if textured and albedo is not None:
        material.map = texture_map(albedo, "base_color", maps)
    elif surface is not None and surface.vertex_colors:
        material.color_mode = "vertex"
    if not isinstance(material, gfx.MeshStandardMaterial):
        return material
    if surface is not None and any(surface.emissive):
        # glTF factors are linear; pygfx colors are sRGB. The strength
        # extension scales it, with or without an emissive texture.
        material.emissive = gfx.Color.from_physical(*surface.emissive)
        if extensions is not None:
            material.emissive_intensity = extensions.emissive_strength
    if not textured:
        return material

    def attach(kind: MapKind, attribute: str) -> bool:
        texture = texture_for(kind)
        if texture is None:
            return False
        setattr(material, attribute, texture_map(texture, kind, maps))
        return True

    packed = texture_for("metallic_roughness")
    if packed is not None:
        # glTF packs roughness in green and metalness in blue, the channels
        # pygfx samples for each map; one TextureMap serves both.
        shared = texture_map(packed, "metallic_roughness", maps)
        material.roughness_map = shared
        material.metalness_map = shared
    attach("normal", "normal_map")
    if surface is not None and any(surface.emissive):
        attach("emissive", "emissive_map")
    if attach("occlusion", "ao_map") and maps is not None:
        material.ao_map_intensity = maps.occlusion_strength
    if isinstance(material, gfx.MeshPhysicalMaterial) and extensions is not None:
        _apply_physical(material, extensions)
        for kind, attribute in _PHYSICAL_MAP_ATTRIBUTES.items():
            attach(kind, attribute)
    return material


def _apply_physical(
    material: gfx.MeshPhysicalMaterial, extensions: SurfaceExtensions
) -> None:
    """Set the ``KHR_materials_*`` factors on a physical material."""
    material.ior = extensions.ior
    material.clearcoat = extensions.clearcoat
    material.clearcoat_roughness = extensions.clearcoat_roughness
    material.specular_intensity = extensions.specular
    material.specular = gfx.Color.from_physical(*extensions.specular_color)
    if any(extensions.sheen_color):
        material.sheen = 1.0
        material.sheen_color = gfx.Color.from_physical(*extensions.sheen_color)
        material.sheen_roughness = extensions.sheen_roughness
    material.iridescence = extensions.iridescence
    material.iridescence_ior = extensions.iridescence_ior
    material.iridescence_thickness_range = extensions.iridescence_thickness
    material.anisotropy = extensions.anisotropy
    material.anisotropy_rotation = extensions.anisotropy_rotation
