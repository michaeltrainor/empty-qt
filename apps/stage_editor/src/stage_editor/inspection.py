"""What the inspector shows about a prim, as plain rows: no widgets here.

usdview's attribute, metadata, layer stack, and composition views each
reduce to a function from a prim (and a time) to rows of strings, which
:mod:`stage_editor.inspector` displays and tests check directly.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pxr import Sdf, Usd, UsdGeom, UsdShade, Vt

#: Value-source names shown in the Source column.
SOURCE_NAMES: dict[object, str] = {
    Usd.ResolveInfoSourceNone: "none",
    Usd.ResolveInfoSourceFallback: "fallback",
    Usd.ResolveInfoSourceDefault: "default",
    Usd.ResolveInfoSourceTimeSamples: "time samples",
    Usd.ResolveInfoSourceValueClips: "value clips",
    Usd.ResolveInfoSourceSpline: "spline",
}

#: Elements shown inline for an array before it is cut short.
INLINE_ITEMS = 6


def format_value(value: Any, limit: int = INLINE_ITEMS) -> str:
    """Return a one-line display string for a USD value.

    Arrays show their length and the first ``limit`` items; matrices show
    their rows; asset paths show the authored path in ``@`` quotes.

    Args:
        value: Any value ``Usd.Attribute.Get`` returns, or ``None``.
        limit: Array items to show; ``0`` or less shows them all.
    """
    if value is None:
        return ""
    if isinstance(value, Sdf.AssetPath):
        return f"@{value.path}@" if value.path else ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, str):
        return value
    if _is_matrix(value):
        return "(" + ", ".join(_format_sequence(row) for row in value) + ")"
    if _is_vt_array(value):
        # Index instead of list(): a mesh's points can be millions long.
        count = len(value)
        shown = count if limit <= 0 else min(count, limit)
        body = ", ".join(format_value(value[i]) for i in range(shown))
        more = ", ..." if shown < count else ""
        return f"[{count}] [{body}{more}]"
    if isinstance(value, Iterable):
        return _format_sequence(value)
    return str(value)


def _format_sequence(values: Iterable[object]) -> str:
    return "(" + ", ".join(format_value(v) for v in values) + ")"


def _is_matrix(value: object) -> bool:
    return type(value).__name__.startswith("Matrix") and hasattr(value, "GetRow")


def _is_vt_array(value: object) -> bool:
    return type(value).__module__.endswith("Vt") or isinstance(
        value, Vt.TokenArray | Vt.StringArray
    )


@dataclass(frozen=True)
class PropertyRow:
    """One row of the Properties tab.

    Attributes:
        name: Property name, or a label for a computed row.
        kind: ``attribute``, ``relationship``, or ``computed``.
        type_name: Value type, ``rel`` for relationships, ``""`` if computed.
        custom: The property is not defined by the prim's schemas.
    """

    name: str
    kind: str
    type_name: str
    custom: bool = False


COMPUTED_XFORM = "World Transform"
COMPUTED_BOUND = "World Bound"
COMPUTED_MATERIAL = "Bound Material"


def property_rows(prim: Usd.Prim) -> list[PropertyRow]:
    """Return the Properties tab rows for ``prim``: computed rows, then properties.

    Args:
        prim: A valid prim.
    """
    rows: list[PropertyRow] = []
    if prim.IsA(UsdGeom.Xformable):
        rows.append(PropertyRow(COMPUTED_XFORM, "computed", ""))
    if prim.IsA(UsdGeom.Imageable):
        rows.append(PropertyRow(COMPUTED_BOUND, "computed", ""))
    if prim.IsA(UsdGeom.Gprim) or prim.HasAPI(UsdShade.MaterialBindingAPI):
        rows.append(PropertyRow(COMPUTED_MATERIAL, "computed", ""))
    for prop in prim.GetProperties():
        if isinstance(prop, Usd.Attribute):
            rows.append(
                PropertyRow(
                    prop.GetName(),
                    "attribute",
                    str(prop.GetTypeName()),
                    prop.IsCustom(),
                )
            )
        else:
            rows.append(
                PropertyRow(prop.GetName(), "relationship", "rel", prop.IsCustom())
            )
    return rows


def property_value(
    prim: Usd.Prim, row: PropertyRow, time: float, limit: int = INLINE_ITEMS
) -> tuple[str, str]:
    """Return the displayed value and its source for a property row at ``time``.

    Args:
        prim: The prim the row belongs to.
        row: A row from :func:`property_rows`.
        time: Time code to evaluate at.
        limit: Array items to show; see :func:`format_value`.

    Returns:
        ``(value, source)``; the source is a :data:`SOURCE_NAMES` value,
        ``targets`` for relationships, or ``computed``.
    """
    code = Usd.TimeCode(time)
    if row.kind == "computed":
        return _computed_value(prim, row.name, code, limit), "computed"
    if row.kind == "relationship":
        rel = prim.GetRelationship(row.name)
        targets = [str(t) for t in rel.GetTargets()]
        return ", ".join(targets), "targets" if targets else "none"
    attr = prim.GetAttribute(row.name)
    source = SOURCE_NAMES.get(attr.GetResolveInfo(code).GetSource(), "none")
    connections = attr.GetConnections()
    if connections:
        linked = ", ".join(f"-> {c}" for c in connections)
        value = attr.Get(code)
        text = format_value(value, limit) if value is not None else ""
        return (f"{text}  {linked}" if text else linked), source
    return format_value(attr.Get(code), limit), source


def _computed_value(prim: Usd.Prim, name: str, code: Usd.TimeCode, limit: int) -> str:
    if name == COMPUTED_XFORM:
        return format_value(UsdGeom.XformCache(code).GetLocalToWorldTransform(prim))
    if name == COMPUTED_BOUND:
        purposes = [
            UsdGeom.Tokens.default_,
            UsdGeom.Tokens.proxy,
            UsdGeom.Tokens.render,
        ]
        bound = UsdGeom.BBoxCache(code, purposes, True).ComputeWorldBound(prim)
        rng = bound.ComputeAlignedRange()
        if rng.IsEmpty():
            return "empty"
        return f"{format_value(rng.GetMin())} - {format_value(rng.GetMax())}"
    if name == COMPUTED_MATERIAL:
        material, _rel = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
        return str(material.GetPath()) if material else ""
    return ""


def property_details(prim: Usd.Prim, row: PropertyRow, time: float) -> str:
    """Return the full text for the value pane: whole value, samples, metadata.

    Args:
        prim: The prim the row belongs to.
        row: A row from :func:`property_rows`.
        time: Time code to evaluate at.
    """
    value, source = property_value(prim, row, time, limit=10000)
    lines = [f"{row.name}  ({row.type_name or row.kind}, {source})", "", value]
    if row.kind == "attribute":
        attr = prim.GetAttribute(row.name)
        samples = attr.GetTimeSamples()
        if samples:
            shown = ", ".join(f"{t:g}" for t in samples[:50])
            more = ", ..." if len(samples) > 50 else ""
            lines += ["", f"Time samples: {len(samples)}", f"  {shown}{more}"]
        metadata = attr.GetAllAuthoredMetadata()
    elif row.kind == "relationship":
        metadata = prim.GetRelationship(row.name).GetAllAuthoredMetadata()
    else:
        metadata = {}
    if metadata:
        lines += ["", "Authored metadata:"]
        lines += [
            f"  {key} = {format_value(val)}" for key, val in sorted(metadata.items())
        ]
    return "\n".join(lines)


def _specifier(specifier: object) -> str:
    """Return ``def``, ``over``, or ``class`` for an ``Sdf.Specifier``."""
    return str(specifier).rsplit(".", 1)[-1].removeprefix("Specifier").lower()


def metadata_rows(prim: Usd.Prim) -> list[tuple[str, str]]:
    """Return the Metadata tab rows: prim path, composed metadata, flags.

    Args:
        prim: A valid prim.
    """
    rows = [
        ("path", str(prim.GetPath())),
        ("specifier", _specifier(prim.GetSpecifier())),
        ("typeName", prim.GetTypeName()),
        ("kind", str(Usd.ModelAPI(prim).GetKind() or "")),
        ("active", format_value(prim.IsActive())),
        ("loaded", format_value(prim.IsLoaded())),
        ("instanceable", format_value(prim.IsInstanceable())),
        ("instance", format_value(prim.IsInstance())),
        ("appliedSchemas", ", ".join(prim.GetAppliedSchemas())),
    ]
    if prim.IsInstance():
        rows.append(("prototype", str(prim.GetPrototype().GetPath())))
    shown = {name for name, _value in rows}
    for key, value in sorted(prim.GetAllMetadata().items()):
        if key in shown or key in ("variantSelection", "variantSetNames"):
            continue
        rows.append((key, format_value(value)))
    return rows


def variant_rows(prim: Usd.Prim) -> list[tuple[str, list[str], str]]:
    """Return each variant set on ``prim`` as ``(name, variants, selection)``.

    Args:
        prim: A valid prim.
    """
    variant_sets = prim.GetVariantSets()
    return [
        (
            name,
            list(variant_sets.GetVariantSet(name).GetVariantNames()),
            variant_sets.GetVariantSet(name).GetVariantSelection(),
        )
        for name in variant_sets.GetNames()
    ]


@dataclass(frozen=True)
class SpecRow:
    """One prim spec contributing to a prim, strongest first.

    Attributes:
        layer: The layer's display name.
        identifier: The layer's identifier, for tooltips and lookups.
        path: The spec's path in that layer.
        specifier: ``def``, ``over``, or ``class``.
        offset: Layer offset and scale applied to the spec, ``""`` if identity.
    """

    layer: str
    identifier: str
    path: str
    specifier: str
    offset: str


def prim_stack_rows(prim: Usd.Prim) -> list[SpecRow]:
    """Return the prim stack: every spec that contributes opinions, strongest first.

    Args:
        prim: A valid prim.
    """
    rows = []
    for spec, offset in prim.GetPrimStackWithLayerOffsets():
        offset_text = (
            ""
            if offset.IsIdentity()
            else f"offset {offset.offset:g} scale {offset.scale:g}"
        )
        rows.append(
            SpecRow(
                spec.layer.GetDisplayName(),
                spec.layer.identifier,
                str(spec.path),
                _specifier(spec.specifier),
                offset_text,
            )
        )
    return rows


def spec_text(identifier: str, path: str) -> str:
    """Return one prim spec, with its subtree, as ``usda`` text.

    Args:
        identifier: Layer identifier from :class:`SpecRow`.
        path: Spec path in that layer.
    """
    layer = Sdf.Layer.Find(identifier) or Sdf.Layer.FindOrOpen(identifier)
    if layer is None:
        return f"# cannot open {identifier}"
    spec_path = Sdf.Path(path)
    if spec_path.ContainsPrimVariantSelection():
        spec_path = spec_path.StripAllVariantSelections()
        if not layer.GetPrimAtPath(spec_path):
            return f"# {path} is inside a variant; see the full layer"
    scratch = Sdf.Layer.CreateAnonymous(".usda")
    Sdf.CreatePrimInLayer(scratch, spec_path)
    if not Sdf.CopySpec(layer, spec_path, scratch, spec_path):
        return f"# cannot copy {path} from {identifier}"
    return f"# {path} in {identifier}\n" + scratch.ExportToString()


@dataclass(frozen=True)
class ArcRow:
    """One composition arc of a prim, as ``Usd.PrimCompositionQuery`` reports it.

    Attributes:
        arc: Arc type: root, reference, payload, inherit, specialize, variant.
        target_layer: Display name of the layer the arc targets.
        target_path: Prim path the arc targets in that layer stack.
        introduced_in: Display name of the layer that authored the arc.
        notes: Flags such as ``ancestral``, ``implicit``, ``no specs``.
    """

    arc: str
    target_layer: str
    target_path: str
    introduced_in: str
    notes: str


def composition_rows(prim: Usd.Prim) -> list[ArcRow]:
    """Return the prim's composition arcs, strongest first.

    Args:
        prim: A valid prim.
    """
    rows = []
    for arc in Usd.PrimCompositionQuery(prim).GetCompositionArcs():
        node = arc.GetTargetNode()
        target_layer = node.layerStack.identifier.rootLayer
        introducing = arc.GetIntroducingLayer()
        notes = [
            label
            for flag, label in (
                (arc.IsAncestral(), "ancestral"),
                (arc.IsImplicit(), "implicit"),
                (not arc.HasSpecs(), "no specs"),
            )
            if flag
        ]
        rows.append(
            ArcRow(
                str(arc.GetArcType())
                .rsplit(".", 1)[-1]
                .removeprefix("ArcType")
                .lower(),
                target_layer.GetDisplayName() if target_layer else "",
                str(node.path),
                introducing.GetDisplayName() if introducing else "",
                ", ".join(notes),
            )
        )
    return rows


def prim_index_text(prim: Usd.Prim) -> str:
    """Return the Pcp prim index dump, as usdview's debug view shows it.

    Args:
        prim: A valid prim; instance proxies report their prototype's index.
    """
    index = prim.GetPrimIndex()
    if not index.IsValid():
        return "# no prim index (instance proxy or pseudo-root)"
    return index.DumpToString()
