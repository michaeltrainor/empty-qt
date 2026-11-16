"""Move, rotate, and scale prims by their world matrix, writing local xformOps.

The gizmo works in world space; :func:`set_world_matrix` turns the result
back into the prim's own transform ops. A prim whose op stack the
``XformCommonAPI`` understands keeps its translate/rotate/scale/pivot ops.
A prim with a single ``xformOp:transform`` keeps that op. Anything else is
rewritten as one matrix op, which is exact but replaces the authored stack.
"""

from __future__ import annotations

from collections.abc import Sequence

from pxr import Gf, Sdf, Usd, UsdGeom

#: Op names the gizmo may author, for undo snapshots.
XFORM_PROPERTIES: tuple[str, ...] = (
    "xformOpOrder",
    "xformOp:translate",
    "xformOp:translate:pivot",
    "xformOp:rotateXYZ",
    "xformOp:rotateXZY",
    "xformOp:rotateYXZ",
    "xformOp:rotateYZX",
    "xformOp:rotateZXY",
    "xformOp:rotateZYX",
    "xformOp:scale",
    "xformOp:transform",
)


def xform_spec_paths(prim: Usd.Prim, target: Usd.EditTarget) -> list[Sdf.Path]:
    """Return the spec paths a transform edit of ``prim`` may touch.

    Args:
        prim: The prim being moved.
        target: The edit target the edit writes to.
    """
    names = set(XFORM_PROPERTIES)
    names.update(prop.GetName() for prop in prim.GetPropertiesInNamespace("xformOp"))
    spec = target.MapToSpecPath(prim.GetPath())
    return [spec] + [spec.AppendProperty(name) for name in sorted(names)]


def world_matrix(prim: Usd.Prim, time: float) -> Gf.Matrix4d:
    """Return the prim's local-to-world matrix at ``time``."""
    return UsdGeom.XformCache(Usd.TimeCode(time)).GetLocalToWorldTransform(prim)


def _time_for(xformable: UsdGeom.Xformable, time: float) -> Usd.TimeCode:
    for op in xformable.GetOrderedXformOps():
        if op.GetAttr().GetNumTimeSamples():
            return Usd.TimeCode(time)
    return Usd.TimeCode.Default()


_ROTATION_AXES = {
    UsdGeom.XformCommonAPI.RotationOrderXYZ: "XYZ",
    UsdGeom.XformCommonAPI.RotationOrderXZY: "XZY",
    UsdGeom.XformCommonAPI.RotationOrderYXZ: "YXZ",
    UsdGeom.XformCommonAPI.RotationOrderYZX: "YZX",
    UsdGeom.XformCommonAPI.RotationOrderZXY: "ZXY",
    UsdGeom.XformCommonAPI.RotationOrderZYX: "ZYX",
}
_AXIS = {"X": Gf.Vec3d.XAxis(), "Y": Gf.Vec3d.YAxis(), "Z": Gf.Vec3d.ZAxis()}


def _euler(rotation: Gf.Rotation, order: str) -> Gf.Vec3f:
    """Return angles (x, y, z) such that the ``order`` rotations compose ``rotation``.

    ``rotateXYZ`` applies X first; with row vectors that is Rx * Ry * Rz,
    which ``Gf.Rotation.Decompose`` reads with its axes in reverse order.
    """
    axes = [_AXIS[c] for c in reversed(order)]
    angles = rotation.Decompose(*axes)
    by_axis = dict(zip(reversed(order), angles, strict=True))
    return Gf.Vec3f(by_axis["X"], by_axis["Y"], by_axis["Z"])


def set_local_matrix(prim: Usd.Prim, local: Gf.Matrix4d, time: float) -> str:
    """Author ``local`` as the prim's own transform at ``time``.

    Args:
        prim: An xformable prim.
        local: The wanted local-to-parent matrix.
        time: Time code; used when the prim's ops are animated.

    Returns:
        ``common``, ``matrix``, or ``replaced``: which way it was written.
    """
    xformable = UsdGeom.Xformable(prim)
    code = _time_for(xformable, time)
    ops = xformable.GetOrderedXformOps()
    api = UsdGeom.XformCommonAPI(prim)
    shear_free = Gf.Transform(local)
    rebuilt = shear_free.GetMatrix()
    exact = all(Gf.IsClose(rebuilt.GetRow(i), local.GetRow(i), 1e-6) for i in range(4))
    if exact and api:
        vectors = api.GetXformVectorsByAccumulation(code)
        if vectors:
            _translate, _rotate, _scale, pivot, order = vectors
            axes = _ROTATION_AXES.get(order, "XYZ")
            transform = Gf.Transform(local)
            rotation = transform.GetRotation()
            scale = transform.GetScale()
            # M = T(-p) S R T(p) T(t), so t = m + p (S R) - p.
            linear = Gf.Matrix4d(1.0).SetScale(scale) * Gf.Matrix4d(1.0).SetRotate(
                rotation
            )
            p = Gf.Vec3d(pivot)
            translate = local.ExtractTranslation() + linear.TransformDir(p) - p
            if api.SetXformVectors(
                translate,
                _euler(rotation, axes),
                Gf.Vec3f(scale),
                Gf.Vec3f(pivot),
                order,
                code,
            ):
                return "common"
    if len(ops) == 1 and ops[0].GetOpType() == UsdGeom.XformOp.TypeTransform:
        ops[0].Set(local, code)
        return "matrix"
    xformable.MakeMatrixXform().Set(local, code)
    return "replaced"


def set_world_matrix(prim: Usd.Prim, world: Gf.Matrix4d, time: float) -> str:
    """Author the local transform that puts ``prim`` at ``world``.

    Args:
        prim: An xformable prim.
        world: The wanted local-to-world matrix.
        time: Time code to evaluate the parent and write animated ops at.

    Returns:
        See :func:`set_local_matrix`.
    """
    cache = UsdGeom.XformCache(Usd.TimeCode(time))
    parent = cache.GetParentToWorldTransform(prim)
    if UsdGeom.Xformable(prim).GetResetXformStack():
        parent = Gf.Matrix4d(1.0)
    return set_local_matrix(prim, world * parent.GetInverse(), time)


def translated(world: Gf.Matrix4d, delta: Gf.Vec3d) -> Gf.Matrix4d:
    """Return ``world`` moved by ``delta`` in world space."""
    return world * Gf.Matrix4d(1.0).SetTranslate(delta)


def rotated(
    world: Gf.Matrix4d, axis: Gf.Vec3d, degrees: float, center: Gf.Vec3d
) -> Gf.Matrix4d:
    """Return ``world`` rotated about a world ``axis`` through ``center``."""
    spin = Gf.Matrix4d(1.0).SetRotate(Gf.Rotation(axis, degrees))
    return (
        world
        * Gf.Matrix4d(1.0).SetTranslate(-center)
        * spin
        * Gf.Matrix4d(1.0).SetTranslate(center)
    )


def scaled(world: Gf.Matrix4d, factors: Sequence[float]) -> Gf.Matrix4d:
    """Return ``world`` scaled along the prim's own axes."""
    return Gf.Matrix4d(1.0).SetScale(Gf.Vec3d(*factors)) * world


def axes(world: Gf.Matrix4d, space: str) -> list[Gf.Vec3d]:
    """Return the gizmo's three unit axes in world space.

    Args:
        world: The prim's world matrix.
        space: ``world`` for the world axes, ``local`` for the prim's own.
    """
    if space != "local":
        return [Gf.Vec3d.XAxis(), Gf.Vec3d.YAxis(), Gf.Vec3d.ZAxis()]
    result = []
    for i in range(3):
        row = Gf.Vec3d(world.GetRow3(i))
        length = row.GetLength()
        result.append(row / length if length > 1e-12 else _AXIS["XYZ"[i]])
    return result
