"""Undoable structural edits on a :class:`StageDocument`.

Each function pushes one command on the document's undo stack and authors
in the current edit target. Renames, reparents, and deletes go through
``Usd.NamespaceEditor``, which also fixes relationship targets and
connections that point at the moved prims.
"""

from __future__ import annotations

from collections.abc import Sequence

from pxr import Gf, Sdf, Tf, Usd, UsdGeom, UsdLux, UsdShade

from stage_editor.document import ROOT, StageDocument
from stage_editor.eject import is_shadowed
from stage_editor.undo import LayerSnapshotCommand, SdfEditCommand

#: Create menu entries: (menu group, label, schema type name).
PRIM_TYPES: tuple[tuple[str, str, str], ...] = (
    ("", "Xform", "Xform"),
    ("", "Scope", "Scope"),
    ("Geometry", "Cube", "Cube"),
    ("Geometry", "Sphere", "Sphere"),
    ("Geometry", "Cylinder", "Cylinder"),
    ("Geometry", "Cone", "Cone"),
    ("Geometry", "Capsule", "Capsule"),
    ("Geometry", "Plane", "Plane"),
    ("", "Camera", "Camera"),
    ("Lights", "Distant Light", "DistantLight"),
    ("Lights", "Dome Light", "DomeLight"),
    ("Lights", "Rect Light", "RectLight"),
    ("Lights", "Sphere Light", "SphereLight"),
    ("Lights", "Disk Light", "DiskLight"),
    ("Lights", "Cylinder Light", "CylinderLight"),
)

#: MaterialX node definition used for new materials.
MTLX_SURFACE_ID = "ND_standard_surface_surfaceshader"
#: Inputs authored on a new ``standard_surface``, so the inspector lists them.
MTLX_DEFAULT_INPUTS: tuple[tuple[str, Sdf.ValueTypeName, object], ...] = (
    ("base", Sdf.ValueTypeNames.Float, 1.0),
    ("base_color", Sdf.ValueTypeNames.Color3f, Gf.Vec3f(0.8, 0.8, 0.8)),
    ("metalness", Sdf.ValueTypeNames.Float, 0.0),
    ("specular", Sdf.ValueTypeNames.Float, 1.0),
    ("specular_roughness", Sdf.ValueTypeNames.Float, 0.4),
    ("emission", Sdf.ValueTypeNames.Float, 0.0),
    ("emission_color", Sdf.ValueTypeNames.Color3f, Gf.Vec3f(1.0, 1.0, 1.0)),
    ("opacity", Sdf.ValueTypeNames.Color3f, Gf.Vec3f(1.0, 1.0, 1.0)),
)


def _local_layers(document: StageDocument) -> list[Sdf.Layer]:
    return list(document.stage.GetLayerStack(includeSessionLayers=False))


def unique_child(stage: Usd.Stage, parent: Sdf.Path, stem: str) -> Sdf.Path:
    """Return ``parent/stem``, or ``parent/stem2``, ... if taken.

    Args:
        stage: The stage to check.
        parent: The parent prim path.
        stem: The preferred name; made a valid identifier first.
    """
    stem = Tf.MakeValidIdentifier(stem)
    candidate = parent.AppendChild(stem)
    index = 1
    while stage.GetPrimAtPath(candidate):
        index += 1
        candidate = parent.AppendChild(f"{stem}{index}")
    return candidate


def default_parent(document: StageDocument, selection: Sequence[str]) -> Sdf.Path:
    """Return where new prims go: the first selected prim, else the default prim.

    Args:
        document: The document.
        selection: Selected prim paths.
    """
    stage = document.stage
    for path in selection:
        prim = stage.GetPrimAtPath(path)
        if prim and not prim.IsInstanceProxy():
            return prim.GetPath()
    default = stage.GetDefaultPrim()
    return default.GetPath() if default else ROOT


def _push_new_prim(document: StageDocument, text: str, path: Sdf.Path, define) -> None:
    stage = document.stage
    target = stage.GetEditTarget()

    def edit() -> None:
        with Usd.EditContext(stage, target):
            define()

    document.undo_stack.push(
        SdfEditCommand(text, target.GetLayer(), [target.MapToSpecPath(path)], edit)
    )


def create_prim(document: StageDocument, parent: Sdf.Path, type_name: str) -> Sdf.Path:
    """Define a new prim of ``type_name`` under ``parent``.

    Lights get a visible intensity; everything else keeps schema defaults.

    Args:
        document: The document to edit.
        parent: The parent prim path.
        type_name: A schema type name from :data:`PRIM_TYPES`.

    Returns:
        The new prim's path.
    """
    stage = document.stage
    path = unique_child(stage, parent, type_name)

    def define() -> None:
        prim = stage.DefinePrim(path, type_name)
        if prim.HasAPI(UsdLux.LightAPI) or UsdLux.LightAPI(prim):
            light = UsdLux.LightAPI(prim)
            if type_name == "DistantLight":
                light.CreateIntensityAttr(3.0)
            elif type_name != "DomeLight":
                light.CreateIntensityAttr(30.0)

    _push_new_prim(document, f"Create {type_name}", path, define)
    return path


def create_material(
    document: StageDocument, parent: Sdf.Path | None = None
) -> Sdf.Path:
    """Create a MaterialX ``standard_surface`` material.

    The material goes in a ``Looks`` scope under the default prim unless a
    parent is given, with a ``Surface`` shader whose inputs are authored so
    the inspector can edit them.

    Args:
        document: The document to edit.
        parent: Where to put the material; ``None`` uses ``<default>/Looks``.

    Returns:
        The material's path.
    """
    stage = document.stage
    if parent is None:
        default = stage.GetDefaultPrim()
        parent = (default.GetPath() if default else ROOT).AppendChild("Looks")
    path = unique_child(stage, parent, "Material")
    created_parent = not stage.GetPrimAtPath(parent)

    def define() -> None:
        if created_parent:
            UsdGeom.Scope.Define(stage, parent)
        material = UsdShade.Material.Define(stage, path)
        shader = UsdShade.Shader.Define(stage, path.AppendChild("Surface"))
        shader.SetShaderId(MTLX_SURFACE_ID)
        for name, type_name, value in MTLX_DEFAULT_INPUTS:
            shader.CreateInput(name, type_name).Set(value)
        out = shader.CreateOutput("out", Sdf.ValueTypeNames.Token)
        material.CreateSurfaceOutput("mtlx").ConnectToSource(out)

    root = parent if created_parent else path
    _push_new_prim(document, "Create MaterialX Material", root, define)
    return path


def stage_materials(stage: Usd.Stage) -> list[str]:
    """Return the paths of every ``UsdShade.Material`` on the stage."""
    return [
        str(prim.GetPath())
        for prim in Usd.PrimRange(
            stage.GetPseudoRoot(), Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate)
        )
        if prim.IsA(UsdShade.Material)
    ]


def bind_material(document: StageDocument, paths: Sequence[str], material: str) -> None:
    """Bind ``material`` to each prim in ``paths``; ``""`` unbinds.

    Args:
        document: The document to edit.
        paths: Prim paths to bind.
        material: The material's path, or ``""`` to remove direct bindings.
    """
    stage = document.stage
    prims = [stage.GetPrimAtPath(p) for p in paths]
    prims = [p for p in prims if p and not p.IsInstanceProxy()]
    if not prims:
        return
    target = stage.GetEditTarget()
    specs: list[Sdf.Path] = []
    for prim in prims:
        spec_path = target.MapToSpecPath(prim.GetPath())
        specs += [spec_path, spec_path.AppendProperty(UsdShade.Tokens.materialBinding)]

    prim_paths = [prim.GetPath() for prim in prims]

    def edit() -> None:
        with Usd.EditContext(stage, target):
            for path in prim_paths:
                api = UsdShade.MaterialBindingAPI.Apply(stage.GetPrimAtPath(path))
                if material:
                    api.Bind(UsdShade.Material(stage.GetPrimAtPath(material)))
                else:
                    api.UnbindDirectBinding()

    label = "Bind Material" if material else "Unbind Material"
    document.undo_stack.push(SdfEditCommand(label, target.GetLayer(), specs, edit))


def _namespace_edit(document: StageDocument, text: str, apply) -> None:
    """Run ``apply(editor)`` and the namespace edits it queued, undoably.

    Raises:
        ValueError: The editor cannot apply the edits; the stage is unchanged.
    """
    stage = document.stage
    # Check first: an exception inside QUndoStack.push is hard to recover from.
    probe = Usd.NamespaceEditor(stage)
    apply(probe)
    result = probe.CanApplyEdits()
    if not result:
        raise ValueError(result.whyNot or "the edit cannot be applied")

    def edit() -> None:
        editor = Usd.NamespaceEditor(stage)
        apply(editor)
        if not editor.ApplyEdits():
            raise ValueError("the edit failed")

    document.undo_stack.push(LayerSnapshotCommand(text, _local_layers(document), edit))


def delete_prims(document: StageDocument, paths: Sequence[str]) -> list[str]:
    """Delete prims, or deactivate those the edit target cannot remove.

    A prim whose specs all live in the local layer stack is removed. A prim
    that also comes from a referenced file (or lives in a shadowed sidecar,
    see :func:`stage_editor.eject.is_shadowed`) is deactivated instead, which
    hides it the same way and is the only edit USD allows there.

    Args:
        document: The document to edit.
        paths: Prim paths to delete.

    Returns:
        The paths that were deactivated rather than removed.
    """
    stage = document.stage
    shadowed = is_shadowed(stage.GetRootLayer())
    local = set(_local_layers(document))
    prims = [stage.GetPrimAtPath(p) for p in paths]
    prims = [p for p in prims if p and not p.IsInstanceProxy()]
    # Deleting an ancestor already deletes its descendants.
    prims = [
        p
        for p in prims
        if not any(p.GetPath().HasPrefix(q.GetPath()) and p != q for q in prims)
    ]
    removable, deactivate = [], []
    for prim in prims:
        layers = {spec.layer for spec in prim.GetPrimStack()}
        if shadowed or not layers <= local:
            deactivate.append(prim)
        else:
            removable.append(prim)
    target = stage.GetEditTarget()
    if removable:
        probe = Usd.NamespaceEditor(stage)
        for prim in removable:
            probe.DeletePrim(prim)
        result = probe.CanApplyEdits()
        if not result:
            # Specs the editor cannot reach (a weaker sublayer it may not
            # edit, say): hide those prims instead.
            deactivate += removable
            removable = []

    remove_paths = [prim.GetPath() for prim in removable]
    hide_paths = [prim.GetPath() for prim in deactivate]

    def edit() -> None:
        if remove_paths:
            editor = Usd.NamespaceEditor(stage)
            for path in remove_paths:
                editor.DeletePrimAtPath(path)
            editor.ApplyEdits()
        with Usd.EditContext(stage, target):
            for path in hide_paths:
                stage.GetPrimAtPath(path).SetActive(False)

    count = len(removable) + len(deactivate)
    text = f"Delete {count} prims" if count > 1 else "Delete prim"
    document.undo_stack.push(LayerSnapshotCommand(text, _local_layers(document), edit))
    return [str(p.GetPath()) for p in deactivate]


def rename_prim(document: StageDocument, path: str, name: str) -> str:
    """Rename a prim, fixing paths that point at it.

    Args:
        document: The document to edit.
        path: The prim to rename.
        name: The new name; must be a valid identifier.

    Returns:
        The prim's new path.

    Raises:
        ValueError: The name is invalid or taken, or the prim's specs are
            not all in the local layer stack.
    """
    if not Sdf.Path.IsValidIdentifier(name):
        msg = f"{name!r} is not a valid prim name"
        raise ValueError(msg)
    stage = document.stage
    prim = stage.GetPrimAtPath(path)
    new_path = prim.GetPath().ReplaceName(name)
    if stage.GetPrimAtPath(new_path):
        msg = f"{new_path} already exists"
        raise ValueError(msg)
    old_path = prim.GetPath()
    _namespace_edit(
        document,
        f"Rename {prim.GetName()}",
        lambda ed: ed.MovePrimAtPath(old_path, new_path),
    )
    return str(new_path)


def reparent_prims(
    document: StageDocument, paths: Sequence[str], parent: str
) -> list[str]:
    """Move prims under ``parent``, keeping names unless they collide.

    Args:
        document: The document to edit.
        paths: Prims to move.
        parent: The new parent's path; ``/`` for the root.

    Returns:
        The prims' new paths.

    Raises:
        ValueError: A prim would move under itself, or cannot be moved.
    """
    stage = document.stage
    new_parent = stage.GetPrimAtPath(parent)
    if not new_parent:
        msg = f"no prim at {parent}"
        raise ValueError(msg)
    moves: list[tuple[Sdf.Path, Sdf.Path]] = []
    taken: set[str] = set()
    for path in paths:
        prim = stage.GetPrimAtPath(path)
        if not prim or prim.GetParent() == new_parent:
            continue
        if new_parent.GetPath().HasPrefix(prim.GetPath()):
            msg = f"cannot move {path} under itself"
            raise ValueError(msg)
        name = prim.GetName()
        candidate = new_parent.GetPath().AppendChild(name)
        index = 1
        while stage.GetPrimAtPath(candidate) or str(candidate) in taken:
            index += 1
            candidate = new_parent.GetPath().AppendChild(f"{prim.GetName()}{index}")
        taken.add(str(candidate))
        moves.append((prim.GetPath(), candidate))
    if not moves:
        return []

    def apply(editor: Usd.NamespaceEditor) -> None:
        for old, new in moves:
            editor.MovePrimAtPath(old, new)

    _namespace_edit(document, "Reparent", apply)
    return sorted(taken)


def duplicate_prims(document: StageDocument, paths: Sequence[str]) -> list[str]:
    """Copy prims beside themselves.

    A prim authored in the local layer stack is copied spec for spec in
    every local layer. A prim that only comes from a referenced file
    becomes a new prim with an internal reference to the original.

    Args:
        document: The document to edit.
        paths: Prims to duplicate.

    Returns:
        The new prims' paths.
    """
    stage = document.stage
    local = _local_layers(document)
    plans: list[tuple[Sdf.Path, Sdf.Path, list[Sdf.Layer]]] = []
    taken: set[Sdf.Path] = set()
    for path in paths:
        prim = stage.GetPrimAtPath(path)
        if not prim or prim.IsPseudoRoot() or prim.IsInstanceProxy():
            continue
        new = unique_child(stage, prim.GetPath().GetParentPath(), prim.GetName())
        while new in taken:
            new = unique_child(stage, new.GetParentPath(), f"{new.name}_")
        taken.add(new)
        layers = [layer for layer in local if layer.GetPrimAtPath(prim.GetPath())]
        plans.append((prim.GetPath(), new, layers))
    target = stage.GetEditTarget().GetLayer()

    def edit() -> None:
        for source, new, layers in plans:
            if layers:
                for layer in layers:
                    Sdf.CreatePrimInLayer(layer, new.GetParentPath())
                    Sdf.CopySpec(layer, source, layer, new)
            else:
                spec = Sdf.CreatePrimInLayer(target, new)
                spec.specifier = Sdf.SpecifierDef
                spec.referenceList.Prepend(Sdf.Reference("", source))

    document.undo_stack.push(LayerSnapshotCommand("Duplicate", local, edit))
    return [str(new) for _source, new, _layers in plans]
