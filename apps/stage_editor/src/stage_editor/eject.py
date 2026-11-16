"""Eject a prim to its own layer file and reference it back in place.

:func:`eject` moves everything that defines a prim into a sidecar layer,
``<scene>_assets/<Name>.usda`` beside the scene, and replaces the prim with
a reference to that file, so the composed stage looks the same. The file
can then be opened and edited on its own, and because both stages share
the same ``Sdf.Layer``, edits show up in the scene at once.
:func:`merge_back` folds a sidecar back into the scene's layer.

What the sidecar receives, strongest first:

* the prim's specs from the scene's own layer stack (moved, not copied);
* the specs of every *ancestral* arc, for a prim that reaches the scene
  through a reference or payload on one of its ancestors. Those files are
  never changed, so their content still composes under the sidecar; the
  sidecar is marked ``shadowed`` and deletes there must deactivate.

Composition arcs, variants, and instancing inside the prim are copied as
authored, not flattened. Relative asset paths are rewritten for the new
folder. Opinions that point outside the prim (a material binding to
``/World/Looks``, an inherit from a class prim) cannot live in a
referenced file, so they stay in the scene's layer as ``over`` opinions.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pxr import Pcp, Sdf, Usd, UsdGeom, UsdUtils

#: Root-layer ``customLayerData`` key a sidecar carries: where it came from.
EJECT_KEY = "stage_editor:ejected"
SIDECAR_SUFFIX = ".usda"


@dataclass
class EjectResult:
    """What :func:`eject` did.

    Attributes:
        prim_path: The ejected prim's path on the scene stage.
        file: The sidecar file written.
        shadowed: Content from files the editor does not own still
            composes under the sidecar; deleting a prim there deactivates.
        warnings: Things that did not carry over exactly.
    """

    prim_path: str
    file: Path
    shadowed: bool = False
    warnings: list[str] = field(default_factory=list)


def sidecar_folder(scene: Path) -> Path:
    """Return the folder ejected prims of ``scene`` are written to."""
    return scene.parent / f"{scene.stem}_assets"


def sidecar_file(scene: Path, prim_name: str) -> Path:
    """Return an unused sidecar path for a prim named ``prim_name``.

    Args:
        scene: The scene file the prim belongs to.
        prim_name: The prim's name; becomes the file stem.
    """
    folder = sidecar_folder(scene)
    candidate = folder / f"{prim_name}{SIDECAR_SUFFIX}"
    suffix = 1
    while candidate.exists() or Sdf.Layer.Find(str(candidate)) is not None:
        suffix += 1
        candidate = folder / f"{prim_name}_{suffix}{SIDECAR_SUFFIX}"
    return candidate


def ejected_info(layer: Sdf.Layer) -> dict[str, object] | None:
    """Return the eject record of a sidecar layer, or ``None`` for other layers.

    Args:
        layer: Any layer.
    """
    info = layer.customLayerData.get(EJECT_KEY)
    return dict(info) if info else None


def is_shadowed(layer: Sdf.Layer) -> bool:
    """Return whether deletes in sidecar ``layer`` must deactivate instead.

    Args:
        layer: Any layer; ``False`` for layers that are not sidecars.
    """
    info = ejected_info(layer)
    return bool(info and info.get("shadowed"))


def relative_asset_path(asset: str, anchor: Sdf.Layer, folder: Path) -> str:
    """Re-express a relative asset path of ``anchor`` relative to ``folder``.

    Absolute paths, URIs, and package-relative paths are returned as is.

    Args:
        asset: An asset path authored in ``anchor``.
        anchor: The layer the path was authored in.
        folder: The folder the path must be relative to now.
    """
    if not asset or os.path.isabs(asset) or "://" in asset or "[" in asset:
        return asset
    if len(asset) > 1 and asset[1] == ":":  # a Windows drive letter
        return asset
    if anchor.anonymous:
        return asset
    absolute = anchor.ComputeAbsolutePath(asset)
    if not absolute:
        return asset
    relative = Path(os.path.relpath(absolute, folder)).as_posix()
    return relative if relative.startswith("../") else f"./{relative}"


@dataclass
class _Outside:
    """A path-valued opinion that points outside the ejected prim."""

    owner: Sdf.Path  # stage path of the prim or property that holds it
    kind: str  # targets, connections, inherits, specializes, references
    paths: list[Sdf.Path]
    type_name: object = None  # attribute value type, for connections
    custom: bool = False


def _split_list_op(list_op: Any, keep: Callable[[Any], bool]) -> list[Any]:
    """Remove items failing ``keep`` from every list of ``list_op``; return them."""
    removed: list[Sdf.Path] = []
    was_explicit = list_op.isExplicit
    for name in (
        "explicitItems",
        "prependedItems",
        "appendedItems",
        "addedItems",
        "deletedItems",
        "orderedItems",
    ):
        items = list(getattr(list_op, name))
        kept = [item for item in items if keep(item)]
        if len(kept) != len(items):
            if name != "deletedItems" and name != "orderedItems":
                removed.extend(item for item in items if not keep(item))
            setattr(list_op, name, kept)
    if removed and was_explicit and not list(list_op.explicitItems):
        # An empty explicit list would block the opinion moved to the scene.
        list_op.ClearEdits()
    return removed


def _collect_outside(
    layer: Sdf.Layer, root: Sdf.Path, stage_root: Sdf.Path, mapping: Pcp.MapFunction
) -> list[_Outside]:
    """Move path opinions pointing outside ``root`` out of ``layer``.

    Args:
        layer: The scratch layer holding the copied prim at ``root``.
        root: The copied prim's path in ``layer``.
        stage_root: The ejected prim's path on the stage.
        mapping: Maps paths of the source layer stack to stage paths.
    """
    found: list[_Outside] = []
    emptied: list[Sdf.Path] = []

    def to_stage(path: Sdf.Path, owner: Sdf.Path) -> Sdf.Path:
        absolute = path if path.IsAbsolutePath() else path.MakeAbsolutePath(owner)
        mapped = mapping.MapSourceToTarget(absolute)
        return mapped if not mapped.isEmpty else absolute

    def owner_on_stage(path: Sdf.Path) -> Sdf.Path:
        plain = path.StripAllVariantSelections()
        return plain.ReplacePrefix(root, stage_root)

    def inside(path: Sdf.Path) -> bool:
        return (
            path.StripAllVariantSelections().HasPrefix(root)
            or not path.IsAbsolutePath()
        )

    def visit(path: Sdf.Path) -> None:
        if path.IsPrimPropertyPath():
            spec = layer.GetObjectAtPath(path)
            if isinstance(spec, Sdf.RelationshipSpec):
                gone = _split_list_op(spec.targetPathList, inside)
                if gone:
                    emptied.append(path)
                    found.append(
                        _Outside(
                            owner_on_stage(path),
                            "targets",
                            [to_stage(p, path.GetPrimPath()) for p in gone],
                            custom=spec.custom,
                        )
                    )
            elif isinstance(spec, Sdf.AttributeSpec):
                gone = _split_list_op(spec.connectionPathList, inside)
                if gone:
                    emptied.append(path)
                    found.append(
                        _Outside(
                            owner_on_stage(path),
                            "connections",
                            [to_stage(p, path.GetPrimPath()) for p in gone],
                            spec.typeName,
                            spec.custom,
                        )
                    )
            return
        if not (path.IsPrimPath() or path.IsPrimVariantSelectionPath()):
            return
        spec = layer.GetPrimAtPath(path)
        if spec is None:
            return
        for kind, attr in (
            ("inherits", "inheritPathList"),
            ("specializes", "specializesList"),
        ):
            gone = _split_list_op(getattr(spec, attr), inside)
            if gone:
                found.append(
                    _Outside(
                        owner_on_stage(path), kind, [to_stage(p, path) for p in gone]
                    )
                )
        for kind, attr in (
            ("references", "referenceList"),
            ("payloads", "payloadList"),
        ):
            list_op = getattr(spec, attr)

            def keep(item: Any) -> bool:
                asset = item.assetPath
                target = item.primPath
                return bool(asset) or target.isEmpty or inside(target)

            gone_items = _split_list_op(list_op, keep)
            if gone_items:
                found.append(
                    _Outside(
                        owner_on_stage(path),
                        kind,
                        [to_stage(item.primPath, path) for item in gone_items],
                    )
                )

    layer.Traverse(root, visit)
    bare = {"custom", "variability", "typeName"}
    for path in emptied:
        spec = layer.GetObjectAtPath(path)
        if spec is not None and set(spec.ListInfoKeys()) <= bare:
            spec.owner.RemoveProperty(spec)
    return found


def _author_outside(layer: Sdf.Layer, item: _Outside) -> None:
    """Author one outside-pointing opinion as an ``over`` in the scene layer."""
    if item.owner.IsPropertyPath():
        prim_spec = Sdf.CreatePrimInLayer(layer, item.owner.GetPrimPath())
        name = item.owner.name
        if item.kind == "targets":
            spec = prim_spec.relationships.get(name) or Sdf.RelationshipSpec(
                prim_spec, name, item.custom
            )
            for path in item.paths:
                spec.targetPathList.Prepend(path)
        else:
            spec = prim_spec.attributes.get(name) or Sdf.AttributeSpec(
                prim_spec, name, item.type_name, Sdf.VariabilityVarying, item.custom
            )
            for path in item.paths:
                spec.connectionPathList.Prepend(path)
        return
    spec = Sdf.CreatePrimInLayer(layer, item.owner)
    for path in item.paths:
        if item.kind == "inherits":
            spec.inheritPathList.Prepend(path)
        elif item.kind == "specializes":
            spec.specializesList.Prepend(path)
        elif item.kind == "references":
            spec.referenceList.Prepend(Sdf.Reference("", path))
        else:
            spec.payloadList.Prepend(Sdf.Payload("", path))


def _remove_prim_spec(layer: Sdf.Layer, path: Sdf.Path) -> None:
    parent_path = path.GetParentPath()
    parent = (
        layer.pseudoRoot
        if parent_path == Sdf.Path.absoluteRootPath
        else layer.GetPrimAtPath(parent_path)
    )
    if parent is not None and path.name in parent.nameChildren:
        del parent.nameChildren[path.name]


def _relative_to(file: Path, layer: Sdf.Layer) -> str:
    """Return ``file`` relative to ``layer``'s folder, or absolute if anonymous."""
    if layer.anonymous:
        return file.as_posix()
    relative = Path(os.path.relpath(file, Path(layer.realPath).parent)).as_posix()
    return relative if relative.startswith("../") else f"./{relative}"


def _rewrite_assets(layer: Sdf.Layer, anchor: Sdf.Layer, folder: Path) -> None:
    UsdUtils.ModifyAssetPaths(
        layer, lambda asset: relative_asset_path(asset, anchor, folder)
    )


def _copy_stage_metadata(stage: Usd.Stage, layer: Sdf.Layer) -> None:
    root = stage.GetRootLayer()
    layer.pseudoRoot.SetInfo(UsdGeom.Tokens.upAxis, UsdGeom.GetStageUpAxis(stage))
    layer.pseudoRoot.SetInfo(
        UsdGeom.Tokens.metersPerUnit, UsdGeom.GetStageMetersPerUnit(stage)
    )
    layer.timeCodesPerSecond = stage.GetTimeCodesPerSecond()
    layer.framesPerSecond = stage.GetFramesPerSecond()
    if root.HasStartTimeCode() and root.HasEndTimeCode():
        layer.startTimeCode = root.startTimeCode
        layer.endTimeCode = root.endTimeCode


def check_ejectable(stage: Usd.Stage, prim_path: str) -> Usd.Prim:
    """Return the prim at ``prim_path`` if it can be ejected.

    Raises:
        ValueError: The prim is missing, the pseudo-root, an instance proxy,
            inside a prototype, or a stage root prim without a parent to
            hold the reference.
    """
    prim = stage.GetPrimAtPath(prim_path)
    if not prim or not prim.IsValid():
        msg = f"no prim at {prim_path}"
        raise ValueError(msg)
    if prim.IsPseudoRoot() or prim.GetParent().IsPseudoRoot():
        msg = "eject a prim below a root prim, not a root prim itself"
        raise ValueError(msg)
    if prim.IsInstanceProxy() or prim.IsInPrototype():
        msg = (
            f"{prim_path} is inside an instance; eject the instance prim, or "
            "make it non-instanceable first"
        )
        raise ValueError(msg)
    return prim


def eject(
    stage: Usd.Stage, prim_path: str, file: Path, edit_layer: Sdf.Layer
) -> EjectResult:
    """Move a prim into ``file`` and reference that file at the same path.

    Args:
        stage: The scene stage.
        prim_path: The prim to eject.
        file: The sidecar to create; see :func:`sidecar_file`.
        edit_layer: The scene layer that receives the reference and any
            outside-pointing opinions; normally the edit target.

    Returns:
        What was written; see :class:`EjectResult`.

    Raises:
        ValueError: See :func:`check_ejectable`, or ``file`` already exists
            or cannot be written.
    """
    prim = check_ejectable(stage, prim_path)
    if file.exists():
        msg = f"{file} already exists"
        raise ValueError(msg)
    path = prim.GetPath()
    root = Sdf.Path.absoluteRootPath.AppendChild(path.name)
    folder = file.parent
    local = set(stage.GetLayerStack(includeSessionLayers=False))
    session = set(stage.GetLayerStack(includeSessionLayers=True)) - local
    result = EjectResult(str(path), file)

    scratch: list[Sdf.Layer] = []
    outside: list[_Outside] = []
    moved: list[tuple[Sdf.Layer, Sdf.Path]] = []
    offsets: set[tuple[float, float]] = set()
    seen: set[tuple[str, str]] = set()
    for arc in Usd.PrimCompositionQuery(prim).GetCompositionArcs():
        is_root = arc.GetArcType() == Pcp.ArcTypeRoot
        if not (is_root or arc.IsAncestral()) or not arc.HasSpecs():
            continue
        node = arc.GetTargetNode()
        mapping = node.mapToRoot
        for layer in node.layerStack.layers:
            if layer in session:
                continue
            key = (layer.identifier, str(node.path))
            if key in seen or layer.GetPrimAtPath(node.path) is None:
                continue
            seen.add(key)
            temp = Sdf.Layer.CreateAnonymous(SIDECAR_SUFFIX)
            if not Sdf.CopySpec(layer, node.path, temp, root):
                result.warnings.append(
                    f"could not copy {node.path} from {layer.identifier}"
                )
                continue
            _rewrite_assets(temp, layer, folder)
            outside += _collect_outside(temp, root, path, mapping)
            scratch.append(temp)
            if is_root and layer in local:
                moved.append((layer, node.path))
            else:
                result.shadowed = True
                offset = mapping.timeOffset
                offsets.add((offset.offset, offset.scale))
    if not scratch:
        msg = f"{prim_path} has no specs to eject"
        raise ValueError(msg)

    folder.mkdir(parents=True, exist_ok=True)
    sidecar = Sdf.Layer.CreateNew(str(file))
    if sidecar is None:
        msg = f"cannot create {file}"
        raise ValueError(msg)
    for temp in scratch:  # strongest first: earlier layers win the stitch
        UsdUtils.StitchLayers(sidecar, temp)
    sidecar.defaultPrim = path.name
    _copy_stage_metadata(stage, sidecar)
    sidecar.customLayerData = {
        "stage_editor:version": 1,
        EJECT_KEY: {
            "scene": stage.GetRootLayer().identifier,
            "path": str(path),
            "shadowed": result.shadowed,
        },
    }
    if not sidecar.Save():
        msg = f"cannot write {file}"
        raise ValueError(msg)

    layer_offset = Sdf.LayerOffset()
    if len(offsets) == 1:
        offset, scale = next(iter(offsets))
        layer_offset = Sdf.LayerOffset(offset, scale)
    elif len(offsets) > 1:
        result.warnings.append(
            "the prim's referenced content uses several time offsets; "
            "time samples keep their source timing"
        )
    with Sdf.ChangeBlock():
        for layer, spec_path in moved:
            _remove_prim_spec(layer, spec_path)
        spec = Sdf.CreatePrimInLayer(edit_layer, path)
        spec.specifier = Sdf.SpecifierDef if prim.IsDefined() else Sdf.SpecifierOver
        asset = _relative_to(file, edit_layer)
        spec.referenceList.Prepend(
            Sdf.Reference(asset, Sdf.Path.emptyPath, layer_offset)
        )
        for item in outside:
            _author_outside(edit_layer, item)
    return result


def find_sidecar(
    stage: Usd.Stage, prim_path: str, edit_layer: Sdf.Layer
) -> Sdf.Layer | None:
    """Return the sidecar layer referenced at ``prim_path`` in ``edit_layer``.

    Args:
        stage: The scene stage.
        prim_path: A prim that was ejected.
        edit_layer: The layer holding the reference.
    """
    del stage
    spec = edit_layer.GetPrimAtPath(prim_path)
    if spec is None:
        return None
    for reference in spec.referenceList.GetAddedOrExplicitItems():
        if not reference.assetPath:
            continue
        resolved = edit_layer.ComputeAbsolutePath(reference.assetPath)
        layer = Sdf.Layer.FindOrOpen(resolved)
        if layer is not None and ejected_info(layer) is not None:
            return layer
    return None


def merge_back(stage: Usd.Stage, prim_path: str, edit_layer: Sdf.Layer) -> Sdf.Layer:
    """Fold an ejected prim's sidecar back into ``edit_layer``.

    The reference is removed and the sidecar's content is stitched under
    the prim, weaker than anything ``edit_layer`` already says there. The
    sidecar file is left on disk.

    Args:
        stage: The scene stage.
        prim_path: The ejected prim.
        edit_layer: The scene layer holding the reference.

    Returns:
        The sidecar layer that was merged.

    Raises:
        ValueError: No sidecar is referenced at ``prim_path`` in ``edit_layer``.
    """
    sidecar = find_sidecar(stage, prim_path, edit_layer)
    if sidecar is None:
        msg = f"{prim_path} has no ejected layer in {edit_layer.GetDisplayName()}"
        raise ValueError(msg)
    path = Sdf.Path(prim_path)
    source = Sdf.Path.absoluteRootPath.AppendChild(sidecar.defaultPrim or path.name)
    temp = Sdf.Layer.CreateAnonymous(SIDECAR_SUFFIX)
    Sdf.CreatePrimInLayer(temp, path)
    Sdf.CopySpec(sidecar, source, temp, path)
    if not edit_layer.anonymous:
        _rewrite_assets(temp, sidecar, Path(edit_layer.realPath).parent)
    spec = edit_layer.GetPrimAtPath(path)
    with Sdf.ChangeBlock():
        for name in ("explicitItems", "prependedItems", "appendedItems", "addedItems"):
            items = list(getattr(spec.referenceList, name))
            kept = [
                r
                for r in items
                if not r.assetPath
                or Sdf.Layer.Find(edit_layer.ComputeAbsolutePath(r.assetPath))
                != sidecar
            ]
            if len(kept) != len(items):
                setattr(spec.referenceList, name, kept)
        UsdUtils.StitchLayers(edit_layer, temp)
    return sidecar
