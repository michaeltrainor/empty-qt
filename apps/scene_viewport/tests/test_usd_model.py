"""USD stages as model files: meshes, materials, textures, and conversion.

No GPU window is opened. Stages are written with usd-core into ``tmp_path``.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pxr = pytest.importorskip("pxr")
gfx = pytest.importorskip("pygfx")

from PIL import Image  # noqa: E402
from pxr import Gf, Sdf, Usd, UsdGeom, UsdLux, UsdShade, Vt  # noqa: E402

from scene_viewport.components import TransformComponent  # noqa: E402
from scene_viewport.mesh.geometry import file_geometry  # noqa: E402
from scene_viewport.mesh.importer import (  # noqa: E402
    IMPORT_SUFFIXES,
    ModelImportError,
    is_importable,
    load_model,
)
from scene_viewport.mesh.references import referenced_files  # noqa: E402
from scene_viewport.mesh.usd_reader import (  # noqa: E402
    USD_MODEL_SUFFIXES,
    is_usd_model,
    linear_to_hex,
    read_usd_model,
    triangulate,
)
from scene_viewport.model_import import import_conversion, import_objects  # noqa: E402
from scene_viewport.scene_io import SceneDocument, save_scene  # noqa: E402
from scene_viewport.usd_io import is_mtqt_stage  # noqa: E402
from scene_viewport.window import _is_foreign_stage  # noqa: E402


def _png(path: Path, red: int, green: int, blue: int, alpha: int = 255) -> Path:
    Image.new("RGBA", (4, 4), (red, green, blue, alpha)).save(path)
    return path


def _quad(stage: Usd.Stage, path: str) -> UsdGeom.Mesh:
    """A unit square in XY, counter-clockwise seen from +Z."""
    mesh = UsdGeom.Mesh.Define(stage, path)
    mesh.GetPointsAttr().Set(
        Vt.Vec3fArray([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    )
    mesh.GetFaceVertexCountsAttr().Set(Vt.IntArray([4]))
    mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray([0, 1, 2, 3]))
    mesh.GetSubdivisionSchemeAttr().Set(UsdGeom.Tokens.none)
    return mesh


def _stage(tmp_path: Path) -> Path:
    """A Z-up centimetre stage with the features the reader handles."""
    path = tmp_path / "other.usda"
    stage = Usd.Stage.CreateNew(str(path))
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 0.01)
    root = UsdGeom.Xform.Define(stage, "/Root")
    stage.SetDefaultPrim(root.GetPrim())
    part = UsdGeom.Xform.Define(stage, "/Root/part")
    UsdGeom.XformCommonAPI(part).SetTranslate((10.0, 20.0, 30.0))

    # Polygons: a quad and a triangle (a hole), left-handed, face-varying UVs,
    # constant display color, no material. Default subdivision scheme.
    poly = UsdGeom.Mesh.Define(stage, "/Root/part/poly")
    poly.GetPointsAttr().Set(
        Vt.Vec3fArray([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (2, 0, 0)])
    )
    poly.GetFaceVertexCountsAttr().Set(Vt.IntArray([4, 3]))
    poly.GetFaceVertexIndicesAttr().Set(Vt.IntArray([0, 1, 2, 3, 1, 4, 2]))
    poly.GetHoleIndicesAttr().Set(Vt.IntArray([1]))
    poly.GetOrientationAttr().Set(UsdGeom.Tokens.leftHanded)
    st = UsdGeom.PrimvarsAPI(poly).CreatePrimvar(
        "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.faceVarying
    )
    st.Set(
        Vt.Vec2fArray(
            [(0, 0), (1, 0), (1, 1), (0, 1), (0.5, 0.5), (0.5, 0.5), (0.5, 0.5)]
        )
    )
    st.SetIndices(Vt.IntArray([0, 1, 2, 3, 4, 5, 6]))
    color = UsdGeom.PrimvarsAPI(poly).CreatePrimvar(
        "displayColor", Sdf.ValueTypeNames.Color3fArray, UsdGeom.Tokens.constant
    )
    color.Set(Vt.Vec3fArray([(1.0, 0.0, 0.0)]))

    # Textured quad with vertex UVs and a UsdPreviewSurface.
    tex = _quad(stage, "/Root/part/tex")
    st = UsdGeom.PrimvarsAPI(tex).CreatePrimvar(
        "st", Sdf.ValueTypeNames.TexCoord2fArray, UsdGeom.Tokens.vertex
    )
    st.Set(Vt.Vec2fArray([(0, 0), (1, 0), (1, 1), (0, 1)]))
    tex.GetDoubleSidedAttr().Set(True)
    looks = UsdGeom.Scope.Define(stage, "/Root/Looks")
    material = UsdShade.Material.Define(stage, "/Root/Looks/Mat")
    surface = UsdShade.Shader.Define(stage, "/Root/Looks/Mat/Surface")
    surface.CreateIdAttr("UsdPreviewSurface")
    surface.CreateInput("metallic", Sdf.ValueTypeNames.Float).Set(0.25)
    surface.CreateInput("opacity", Sdf.ValueTypeNames.Float).Set(0.5)
    surface.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).Set((1, 0, 0))
    material.CreateSurfaceOutput().ConnectToSource(surface.ConnectableAPI(), "surface")
    reader = UsdShade.Shader.Define(stage, "/Root/Looks/Mat/Reader")
    reader.CreateIdAttr("UsdPrimvarReader_float2")
    reader.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
    for name, file, output, wrap in (
        ("diffuseColor", _png(tmp_path / "color.png", 10, 20, 30), "rgb", "clamp"),
        ("roughness", _png(tmp_path / "rough.png", 200, 0, 0), "r", "repeat"),
        ("normal", _png(tmp_path / "normal.png", 128, 128, 255), "rgb", "repeat"),
    ):
        shader = UsdShade.Shader.Define(stage, f"/Root/Looks/Mat/{name}Tex")
        shader.CreateIdAttr("UsdUVTexture")
        shader.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(f"./{file.name}")
        shader.CreateInput("wrapS", Sdf.ValueTypeNames.Token).Set(wrap)
        shader.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(
            reader.ConnectableAPI(), "result"
        )
        kind = Sdf.ValueTypeNames.Float if output == "r" else Sdf.ValueTypeNames.Color3f
        surface.CreateInput(name, kind).ConnectToSource(shader.ConnectableAPI(), output)
    UsdShade.MaterialBindingAPI.Apply(tex.GetPrim()).Bind(material)
    assert looks.GetPrim().IsValid()

    cube = UsdGeom.Cube.Define(stage, "/Root/cube")
    cube.GetSizeAttr().Set(2.0)
    guide = _quad(stage, "/Root/guide")
    guide.GetPurposeAttr().Set(UsdGeom.Tokens.guide)
    hidden = UsdGeom.Xform.Define(stage, "/Root/hidden")
    hidden.GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)
    UsdGeom.Scope.Define(stage, "/Root/rig")
    UsdGeom.Xform.Define(stage, "/Root/rig/Geom")
    UsdGeom.Xform.Define(stage, "/Root/part/Geom")
    sun = UsdLux.DistantLight.Define(stage, "/Root/sun")
    sun.GetIntensityAttr().Set(2.0)
    sun.GetExposureAttr().Set(1.0)
    cam = UsdGeom.Camera.Define(stage, "/Root/cam")
    cam.GetFocalLengthAttr().Set(50.0)
    cam.GetVerticalApertureAttr().Set(20.0)
    stage.GetRootLayer().Save()
    return path


def test_usd_suffixes_are_importable() -> None:
    assert USD_MODEL_SUFFIXES == (".usd", ".usda", ".usdc", ".usdz")
    assert set(USD_MODEL_SUFFIXES) < set(IMPORT_SUFFIXES)
    assert is_usd_model(Path("a.USDZ"))
    assert is_importable(Path("a.usda"))
    assert not is_usd_model(Path("a.glb"))


def test_triangulate_fans_flips_and_drops_holes() -> None:
    counts = np.array([4, 3, 2, 5])
    triangles = triangulate(counts, np.array([1]), left_handed=False)
    assert triangles.tolist() == [
        [0, 1, 2],
        [0, 2, 3],
        [9, 10, 11],
        [9, 11, 12],
        [9, 12, 13],
    ]
    flipped = triangulate(np.array([3]), None, left_handed=True)
    assert flipped.tolist() == [[0, 2, 1]]
    assert triangulate(np.array([], dtype=np.int64), None, False).shape == (0, 3)


def test_read_usd_model_reads_nodes_meshes_and_declared_axes(tmp_path: Path) -> None:
    path = _stage(tmp_path)
    model = read_usd_model(path)

    assert (model.up_axis, model.meters_per_unit) == ("z", 0.01)
    names = [(node.name, node.parent) for node in model.nodes]
    assert names == [
        ("Root", ""),
        ("part", "Root"),
        ("poly", "part"),
        ("tex", "part"),
        ("Geom", "part"),
        ("cube", "Root"),
        ("hidden", "Root"),
        ("rig", "Root"),
        ("Geom_1", "rig"),
        ("sun", "Root"),
        ("cam", "Root"),
    ]
    by_name = {node.name: node for node in model.nodes}
    assert by_name["part"].matrix[:3, 3].tolist() == [10.0, 20.0, 30.0]
    assert not by_name["hidden"].visible
    assert by_name["poly"].visible
    assert by_name["poly"].mesh == "/Root/part/poly"
    assert by_name["Geom"].mesh == ""
    assert set(model.meshes) == {"/Root/part/poly", "/Root/part/tex", "/Root/cube"}

    poly = model.meshes["/Root/part/poly"]
    # Face-varying UVs split the vertices per corner; the hole is dropped and
    # the left-handed quad flipped, so its normals point down -Z.
    assert poly.positions.shape == (7, 3)
    assert poly.indices.tolist() == [[0, 2, 1], [0, 3, 2]]
    assert poly.texcoords[:4].tolist() == [[0, 0], [1, 0], [1, 1], [0, 1]]
    assert np.allclose(poly.normals[:4], [[0, 0, -1]] * 4)
    assert poly.colors is None
    poly_surface = model.materials["/Root/part/poly"]
    assert poly_surface is not None
    assert (poly_surface.color, poly_surface.double_sided) == ("#ff0000", False)
    assert "/Root/part/poly" not in model.maps

    tex = model.meshes["/Root/part/tex"]
    assert tex.positions.shape == (4, 3)
    assert tex.indices.tolist() == [[0, 1, 2], [0, 2, 3]]
    assert tex.texcoords.tolist() == [[0, 0], [1, 0], [1, 1], [0, 1]]
    assert tex.tangents is not None
    assert tex.tangents.shape == (4, 4)
    surface = model.materials["/Root/part/tex"]
    assert surface is not None
    assert surface.color == "#ffffff"  # textured: the factor stays white
    assert (surface.roughness, surface.metalness) == (1.0, 0.25)
    assert (surface.opacity, surface.alpha_mode) == (0.5, "blend")
    assert surface.emissive == (1.0, 0.0, 0.0)
    assert surface.double_sided
    maps = model.maps["/Root/part/tex"]
    assert maps.base_color is not None
    assert maps.base_color.shape == (4, 4, 4)
    assert maps.base_color[0, 0].tolist() == [10, 20, 30, 255]
    assert maps.normal is not None
    packed = maps.metallic_roughness
    assert packed is not None
    assert packed[0, 0].tolist() == [0, 200, 255, 255]  # roughness in G, no metal
    assert maps.sampling["base_color"].wrap_s == "clamp"
    assert maps.sampling["base_color"].uv_channel == 0
    assert maps.emissive_factor == (1.0, 0.0, 0.0)

    cube = model.meshes["/Root/cube"]
    assert cube.indices.shape == (12, 3)
    assert cube.positions.min() == -1.0
    assert cube.positions.max() == 1.0

    assert [(light.kind, light.intensity) for light in model.lights] == [
        ("directional", 4.0)
    ]
    assert model.lights[0].node == "sun"
    (camera,) = model.cameras
    assert camera.node == "cam"
    assert camera.yfov == pytest.approx(2.0 * math.atan(20.0 / 100.0))
    assert model.warnings == (
        "other.usda: 1 subdivision surface is drawn as the control mesh",
    )


def test_load_model_imports_usd_with_conversion_and_references(tmp_path: Path) -> None:
    path = _stage(tmp_path)
    model = load_model(path)
    assert model.up_axis == "z"
    assert {p.name for p in referenced_files(path)} == {
        "color.png",
        "rough.png",
        "normal.png",
    }

    conversion = import_conversion(
        path, declared_up=model.up_axis, declared_metres=model.meters_per_unit
    )
    # Z-up centimetres: +Z becomes +Y, scaled to metres.
    assert np.allclose(conversion @ [0.0, 0.0, 100.0, 1.0], [0.0, 1.0, 0.0, 1.0])
    explicit = import_conversion(path, "y", "m", declared_up="z", declared_metres=0.01)
    assert np.allclose(explicit, np.eye(4))

    result = import_objects(model, set(), conversion=conversion)
    assert result.root == "other"
    transforms = {t.name: t for t in result.objects.transforms}
    assert not transforms["hidden"].visible
    assert transforms["poly"].parent == "part"
    assert transforms["part"].position == pytest.approx((10.0, 20.0, 30.0))
    meshes = {m.name: m for m in result.objects.meshes}
    tex = meshes["texShape"]
    assert (tex.kind, tex.source, tex.source_mesh) == ("file", path, "/Root/part/tex")
    assert (tex.opacity, tex.alpha_mode, tex.metalness) == (0.5, "blend", 0.25)
    assert meshes["polyShape"].color == "#ff0000"
    assert [light.kind for light in result.objects.lights] == ["directional"]
    assert [camera.transform for camera in result.objects.cameras] == ["cam"]

    geometry = file_geometry(path, "/Root/part/tex")
    assert geometry.indices.nitems == 2


def test_usd_without_meshes_or_broken_is_a_model_import_error(tmp_path: Path) -> None:
    empty = tmp_path / "empty.usda"
    stage = Usd.Stage.CreateNew(str(empty))
    UsdGeom.Xform.Define(stage, "/Root")
    stage.GetRootLayer().Save()
    with pytest.raises(ModelImportError, match="no triangle meshes"):
        load_model(empty)
    bad = tmp_path / "bad.usda"
    bad.write_text("not usd", encoding="utf-8")
    with pytest.raises(ModelImportError, match="cannot read"):
        load_model(bad)


def test_missing_texture_is_reported_not_fatal(tmp_path: Path) -> None:
    path = _stage(tmp_path)
    (tmp_path / "color.png").unlink()
    model = load_model(path)
    assert "other.usda: missing color.png" in model.warnings
    assert "other.usda: cannot read image color.png" in model.warnings
    maps = model.maps["/Root/part/tex"]
    assert maps.base_color is None
    assert maps.metallic_roughness is not None


def test_usdz_package_reads_textures_through_ar(tmp_path: Path) -> None:
    UsdUtils = pytest.importorskip("pxr.UsdUtils")
    path = _stage(tmp_path)
    package = tmp_path / "other.usdz"
    assert UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(str(path)), str(package))
    model = load_model(package)
    assert "/Root/part/tex" in model.meshes
    base = model.maps["/Root/part/tex"].base_color
    assert base is not None
    assert base[0, 0].tolist() == [10, 20, 30, 255]
    assert referenced_files(package) == ()


def test_foreign_stages_are_models_and_ours_are_scenes(tmp_path: Path) -> None:
    foreign = _stage(tmp_path)
    assert not is_mtqt_stage(foreign)
    assert _is_foreign_stage(foreign)
    ours = save_scene(
        SceneDocument(transforms=[TransformComponent(name="a")]), tmp_path / "s.usda"
    )
    assert is_mtqt_stage(ours)
    assert not _is_foreign_stage(ours)
    assert not is_mtqt_stage(tmp_path / "missing.usda")
    assert not is_mtqt_stage(tmp_path / "color.png")
    # A scene saved before the layer marker: recognised by its prims' data.
    layer = Sdf.Layer.FindOrOpen(str(ours))
    layer.customLayerData = {}
    layer.Save()
    assert is_mtqt_stage(ours)


def test_linear_to_hex_rounds_srgb() -> None:
    assert linear_to_hex(Gf.Vec3f(0.0, 1.0, 0.5)) == "#00ffbc"
    assert linear_to_hex((2.0, -1.0, 0.18)) == "#ff0076"


def test_usd_sourced_meshes_are_referenced_on_the_stage(tmp_path: Path) -> None:
    from pxr import Usd

    from scene_viewport.components import MeshComponent
    from scene_viewport.usd_io import load_usd_scene
    from scene_viewport.world import SceneWorld

    source = _stage(tmp_path)
    world = SceneWorld()
    world.add_transform(TransformComponent(name="part"))
    world.add_mesh(
        MeshComponent(
            name="tex",
            transform="part",
            kind="file",
            source=source,
            source_mesh="/Root/part/tex",
        )
    )
    prim = world.stage.GetPrimAtPath("/World/part/tex")
    # Composition brings the source mesh in; no triangles are copied.
    assert prim.IsA(UsdGeom.Mesh)
    assert len(UsdGeom.Mesh(prim).GetPointsAttr().Get()) == 4
    assert prim.GetPrim().HasAuthoredReferences()
    assert (
        world.stage.GetRootLayer().GetAttributeAtPath("/World/part/tex.points") is None
    )
    # The source prim's own placement is overridden: the transform places it.
    assert list(UsdGeom.Xformable(prim).GetOrderedXformOps()) == []
    # The source's material binding is replaced by the Mesh tab's.
    bound, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
    assert bound.GetPath() == Sdf.Path("/World/Looks/tex_Material")

    scene = tmp_path / "scenes" / "scene.usda"
    save_scene(world.to_document(), scene)
    layer = Sdf.Layer.FindOrOpen(str(scene))
    spec = layer.GetPrimAtPath("/World/part/tex")
    (reference,) = spec.referenceList.explicitItems
    assert reference.assetPath == "../other.usda"
    assert reference.primPath == Sdf.Path("/Root/part/tex")
    back = load_usd_scene(scene)
    assert [(m.source, m.source_mesh) for m in back.meshes] == [
        (source.resolve(), "/Root/part/tex")
    ]
    stage = Usd.Stage.Open(str(scene))
    assert (
        len(UsdGeom.Mesh(stage.GetPrimAtPath("/World/part/tex")).GetPointsAttr().Get())
        == 4
    )
