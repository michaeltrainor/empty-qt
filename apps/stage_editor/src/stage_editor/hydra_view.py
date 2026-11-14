"""Hydra Storm view: a ``QOpenGLWidget`` that draws the document with Storm.

``UsdImagingGL`` ships only with an imaging build of OpenUSD
(``scripts/build_usd_imaging.cmd``), not with the ``usd-core`` wheel, so the
import is optional. :func:`hydra_available` tells the window whether this
view can run. The GL calls go through Qt's ``QOpenGLFunctions``, so PyOpenGL
is not needed.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Sequence
from typing import Any, cast

from pxr import Gf, Tf, Usd, UsdGeom
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QMouseEvent,
    QOpenGLFunctions,
    QPainter,
    QSurfaceFormat,
    QWheelEvent,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from stage_editor.document import StageDocument

try:
    # Imaging-only modules: stubbed in typings/pxr, absent from the usd-core wheel.
    from pxr import (
        CameraUtil,  # pyright: ignore[reportMissingModuleSource]
        Glf,  # pyright: ignore[reportMissingModuleSource]
        UsdImagingGL,  # pyright: ignore[reportMissingModuleSource]
    )

    _HYDRA = True
except ImportError:  # pragma: no cover - depends on the local build
    CameraUtil = Glf = UsdImagingGL = cast("Any", None)
    _HYDRA = False

log = logging.getLogger(__name__)

GL_COLOR_BUFFER_BIT = 0x00004000
GL_DEPTH_BUFFER_BIT = 0x00000100
GL_BLEND = 0x0BE2
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_UNPACK_ALIGNMENT = 0x0CF5

CLEAR_COLOR = (0.18, 0.18, 0.20, 1.0)
PURPOSES = (UsdGeom.Tokens.default_, UsdGeom.Tokens.proxy, UsdGeom.Tokens.render)


def hydra_available() -> bool:
    """Return whether ``pxr.UsdImagingGL`` imported, so Storm can draw."""
    return _HYDRA


class OrbitCamera:
    """usdview-style free camera: tumble, truck, and dolly around a centre.

    Args:
        z_up: Whether the stage's up axis is Z; the tumble keeps that axis up.
        fov: Vertical field of view in degrees.
    """

    def __init__(self, z_up: bool, fov: float = 60.0) -> None:
        self.z_up = z_up
        self.fov = fov
        # A three-quarter view to start; tumbling keeps the angles across re-framing.
        self.theta = -30.0
        self.phi = 20.0
        self.center = Gf.Vec3d(0, 0, 0)
        self.dist = 10.0
        self.size = 10.0
        self._yz_inv = (
            Gf.Matrix4d(1.0).SetRotate(Gf.Rotation(Gf.Vec3d.XAxis(), 90))
            if z_up
            else Gf.Matrix4d(1.0)
        )

    def frame(self, bbox: Gf.BBox3d) -> None:
        """Centre on ``bbox`` and back off until it fits the field of view.

        Args:
            bbox: World-space bound; an empty one leaves the camera alone.
        """
        rng = bbox.ComputeAlignedRange()
        if rng.IsEmpty():
            return
        self.center = bbox.ComputeCentroid()
        self.size = max(max(*rng.GetSize()), 1e-3)
        self.dist = (self.size * 0.55) / math.tan(math.radians(self.fov * 0.5))

    def tumble(self, dtheta: float, dphi: float) -> None:
        """Rotate by degrees around the centre."""
        self.theta += dtheta
        self.phi += dphi

    def dolly(self, factor: float) -> None:
        """Scale the distance to the centre."""
        self.dist = max(self.dist * factor, self.size * 1e-3)

    def truck(self, dx: float, dy: float) -> None:
        """Slide the centre in the camera's right and up directions."""
        frustum = self.camera(1.0).frustum
        up = frustum.ComputeUpVector()
        right = Gf.Cross(frustum.ComputeViewDirection(), up)
        self.center += up * dy + right * dx

    def camera(self, aspect: float) -> Gf.Camera:
        """Return the ``Gf.Camera`` for ``aspect`` with safe clipping planes."""

        def rot(axis: Gf.Vec3d, angle: float) -> Gf.Matrix4d:
            return Gf.Matrix4d(1.0).SetRotate(Gf.Rotation(axis, angle))

        cam = Gf.Camera()
        cam.SetPerspectiveFromAspectRatioAndFieldOfView(
            aspect, self.fov, Gf.Camera.FOVVertical
        )
        cam.transform = (
            Gf.Matrix4d().SetTranslate(Gf.Vec3d.ZAxis() * self.dist)
            * rot(Gf.Vec3d.XAxis(), -self.phi)
            * rot(Gf.Vec3d.YAxis(), -self.theta)
            * self._yz_inv
            * Gf.Matrix4d().SetTranslate(self.center)
        )
        cam.focusDistance = self.dist
        near = max(self.dist - self.size * 2.0, self.size * 1e-3, 1e-3)
        far = self.dist + self.size * 4.0
        cam.clippingRange = Gf.Range1f(near, far)
        return cam


def _framing(width: int, height: int) -> object:
    display = Gf.Range2f(Gf.Vec2f(0, 0), Gf.Vec2f(width, height))
    data = Gf.Rect2i(Gf.Vec2i(0, 0), width, height)
    return CameraUtil.Framing(display, data)


class HydraView(QOpenGLWidget):
    """Draw the document's stage with Storm and pick prims with the mouse.

    Left drag tumbles, middle drag trucks, right drag or the wheel dollies,
    and a left click picks. The engine is created on the first paint and
    rebuilt when the document's stage is replaced. Call :meth:`shutdown`
    before the GL context goes away, or Storm reports errors on teardown.

    Signals:
        pickedPath(str): The prim path under a click, or ``""`` for none.
        renderTimed(float): Milliseconds spent in the last ``Render`` call.

    Args:
        document: The document to draw and follow.
        parent: Owning widget.

    Raises:
        RuntimeError: ``UsdImagingGL`` is not importable.
    """

    pickedPath = Signal(str)
    renderTimed = Signal(float)

    def __init__(self, document: StageDocument, parent=None) -> None:
        if not hydra_available():
            raise RuntimeError(
                "pxr.UsdImagingGL is not available; build OpenUSD with imaging"
            )
        super().__init__(parent)
        fmt = QSurfaceFormat()
        fmt.setDepthBufferSize(24)
        fmt.setStencilBufferSize(8)
        self.setFormat(fmt)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(320, 240)
        self._document = document
        self._engine = None
        self._gl: QOpenGLFunctions | None = None
        self._selected: list[str] = []
        self._bbox_cache = UsdGeom.BBoxCache(document.time, list(PURPOSES), True)
        self._camera = OrbitCamera(self._stage_is_z_up())
        self._press: QPointF | None = None
        self._last: QPointF | None = None
        self._button = Qt.MouseButton.NoButton
        self._needs_frame = True
        self._params = self._make_params()
        document.stageReplaced.connect(self._on_stage_replaced)
        document.changed.connect(self._on_changed)
        document.timeChanged.connect(self._on_time_changed)
        self.frame_paths([])

    # -- public ---------------------------------------------------------------

    def set_selected_paths(self, paths: Sequence[str]) -> None:
        """Highlight ``paths`` in the view.

        Args:
            paths: Prim paths as strings.
        """
        self._selected = list(paths)
        if self._engine is not None:
            self._push_selection()
        self.update()

    def frame_paths(self, paths: Sequence[str]) -> None:
        """Frame the world bound of ``paths``, or of the whole stage if empty.

        Args:
            paths: Prim paths as strings.
        """
        stage = self._document.stage
        prims = [stage.GetPrimAtPath(p) for p in paths]
        prims = [p for p in prims if p and p.IsValid()]
        if not prims:
            prims = [stage.GetPseudoRoot()]
        self._bbox_cache.SetTime(self._document.time)
        bbox = Gf.BBox3d()
        for prim in prims:
            bbox = Gf.BBox3d.Combine(bbox, self._bbox_cache.ComputeWorldBound(prim))
        # An empty stage has nothing to frame; frame again once content arrives.
        self._needs_frame = bbox.ComputeAlignedRange().IsEmpty()
        self._camera.frame(bbox)
        self.update()

    def shutdown(self) -> None:
        """Destroy the engine while the GL context is still current."""
        if self._engine is None:
            return
        self.makeCurrent()
        self._engine = None
        self.doneCurrent()

    # -- document -------------------------------------------------------------

    def _stage_is_z_up(self) -> bool:
        return UsdGeom.GetStageUpAxis(self._document.stage) == UsdGeom.Tokens.z

    def _on_stage_replaced(self) -> None:
        self.shutdown()
        self._bbox_cache = UsdGeom.BBoxCache(self._document.time, list(PURPOSES), True)
        self._camera = OrbitCamera(self._stage_is_z_up())
        self._selected = []
        self.frame_paths([])

    def _on_changed(self, resynced: list[str], _info: list[str]) -> None:
        self._bbox_cache.Clear()
        if resynced and self._needs_frame:
            self.frame_paths([])
            return
        self.update()

    def _on_time_changed(self, _time: float) -> None:
        self.update()

    # -- engine ---------------------------------------------------------------

    def _make_params(self):
        params = UsdImagingGL.RenderParams()
        params.drawMode = UsdImagingGL.DrawMode.DRAW_SHADED_SMOOTH
        params.cullStyle = UsdImagingGL.CullStyle.CULL_STYLE_BACK_UNLESS_DOUBLE_SIDED
        params.enableSceneMaterials = True
        params.enableSceneLights = True
        params.showGuides = False
        params.showProxy = True
        params.showRender = False
        params.gammaCorrectColors = False
        params.colorCorrectionMode = "sRGB"
        params.clearColor = Gf.Vec4f(*CLEAR_COLOR)
        params.enableSampleAlphaToCoverage = True
        return params

    def _ensure_engine(self):
        if self._engine is None:
            options = UsdImagingGL.Engine.Parameters()
            options.allowAsynchronousSceneProcessing = True
            engine = UsdImagingGL.Engine(options)
            engine.SetRendererAov("color")
            engine.SetSelectionColor(Gf.Vec4f(1.0, 0.8, 0.0, 1.0))
            self._engine = engine
            self._push_selection()
            log.info("Hydra renderer: %s", engine.GetCurrentRendererId())
        return self._engine

    def _push_selection(self) -> None:
        engine = self._engine
        if engine is None:
            return
        engine.ClearSelected()
        for path in self._selected:
            engine.AddSelected(path, UsdImagingGL.ALL_INSTANCES)

    def _physical_size(self) -> tuple[int, int]:
        size = self.size() * self.devicePixelRatioF()
        return int(size.width()), int(size.height())

    # -- QOpenGLWidget --------------------------------------------------------

    def initializeGL(self) -> None:
        self._gl = QOpenGLFunctions()
        self._gl.initializeOpenGLFunctions()
        Glf.RegisterDefaultDebugOutputMessageCallback()

    def paintGL(self) -> None:
        width, height = self._physical_size()
        gl = self._gl
        if width <= 0 or height <= 0 or gl is None:
            return
        engine = self._ensure_engine()
        camera = self._camera.camera(width / height)
        frustum = camera.frustum
        engine.SetRenderBufferSize(Gf.Vec2i(width, height))
        engine.SetFraming(_framing(width, height))
        engine.SetOverrideWindowPolicy(CameraUtil.MatchVertically)
        engine.SetCameraState(
            frustum.ComputeViewMatrix(), frustum.ComputeProjectionMatrix()
        )

        key = Glf.SimpleLight()
        position = frustum.position
        key.position = (position[0], position[1], position[2], 1)
        key.transform = frustum.ComputeViewInverse()
        material = Glf.SimpleMaterial()
        material.ambient = (0.2, 0.2, 0.2, 1.0)
        material.specular = (0.1, 0.1, 0.1, 1.0)
        material.shininess = 32.0
        engine.SetLightingState([key], material, (0.01, 0.01, 0.01, 1.0))

        gl.glDepthMask(True)
        gl.glClearColor(*CLEAR_COLOR)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        gl.glViewport(0, 0, width, height)
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        self._params.frame = Usd.TimeCode(self._document.time)
        self._params.clipPlanes = [Gf.Vec4d(c) for c in camera.clippingPlanes]
        started = time.perf_counter()
        try:
            engine.Render(self._document.stage.GetPseudoRoot(), self._params)
        except Tf.ErrorException:
            log.exception("Storm render failed")
        elapsed = (time.perf_counter() - started) * 1000.0
        gl.glPixelStorei(GL_UNPACK_ALIGNMENT, 4)
        self.renderTimed.emit(elapsed)

        painter = QPainter(self)
        painter.setPen(QColor(230, 200, 90))
        painter.drawText(10, 20, f"{elapsed:.1f} ms  frame {self._document.time:g}")
        painter.end()

    # -- input ----------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._button = event.button()
        self._press = self._last = event.position()
        self.setFocus()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._last is None:
            return
        delta = event.position() - self._last
        self._last = event.position()
        if self._button == Qt.MouseButton.LeftButton:
            self._camera.tumble(delta.x() * 0.4, delta.y() * 0.4)
        elif self._button == Qt.MouseButton.MiddleButton:
            scale = self._camera.dist * 0.002
            self._camera.truck(-delta.x() * scale, delta.y() * scale)
        elif self._button == Qt.MouseButton.RightButton:
            self._camera.dolly(1.0 + delta.x() * 0.01)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        pressed = self._press
        self._press = self._last = None
        self._button = Qt.MouseButton.NoButton
        if (
            pressed is not None
            and event.button() == Qt.MouseButton.LeftButton
            and (event.position() - pressed).manhattanLength() < 3
        ):
            self.pickedPath.emit(self.pick(event.position()))

    def wheelEvent(self, event: QWheelEvent) -> None:
        self._camera.dolly(0.9 if event.angleDelta().y() > 0 else 1.1)
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_F:
            self.frame_paths(self._selected)
        elif event.key() == Qt.Key.Key_A:
            self.frame_paths([])
        else:
            super().keyPressEvent(event)

    def pick(self, position: QPointF) -> str:
        """Return the prim path under ``position`` (widget pixels), or ``""``.

        Args:
            position: Point in widget coordinates.
        """
        width, height = self._physical_size()
        if width <= 0 or height <= 0:
            return ""
        ratio = self.devicePixelRatioF()
        x, y = position.x() * ratio, position.y() * ratio
        camera = self._camera.camera(width / height)
        point = Gf.Vec2d(x / width * 2.0 - 1.0, -(y / height * 2.0 - 1.0))
        size = Gf.Vec2d(1.0 / width, 1.0 / height)
        narrowed = camera.frustum.ComputeNarrowedFrustum(point, size)
        pick = UsdImagingGL.Engine.PickParams()
        pick.resolveMode = "resolveNearestToCenter"
        self.makeCurrent()
        try:
            engine = self._ensure_engine()
            self._params.frame = Usd.TimeCode(self._document.time)
            hits = engine.TestIntersection(
                pick,
                narrowed.ComputeViewMatrix(),
                narrowed.ComputeProjectionMatrix(),
                self._document.stage.GetPseudoRoot(),
                self._params,
            )
        finally:
            self.doneCurrent()
        if not hits:
            return ""
        return str(hits[0].hitPrimPath)
