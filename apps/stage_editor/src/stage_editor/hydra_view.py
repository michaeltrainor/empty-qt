"""Hydra Storm view: a ``QOpenGLWidget`` that draws the document with Storm.

``UsdImagingGL`` ships only with an imaging build of OpenUSD
(``scripts/build_usd_imaging.cmd``), not with the ``usd-core`` wheel, so the
import is optional. :func:`hydra_available` tells the window whether this
view can run. The GL calls go through Qt's ``QOpenGLFunctions``, so PyOpenGL
is not needed. :class:`OrbitCamera` and :func:`conform_camera` use only
``Gf`` and work without the imaging build.
"""

from __future__ import annotations

import logging
import math
import time
from collections import Counter
from collections.abc import Sequence
from typing import Any, cast

from pxr import Gf, Sdf, Tf, Usd, UsdGeom
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QOpenGLFunctions,
    QPainter,
    QSurfaceFormat,
    QWheelEvent,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget

from stage_editor.display import ViewSettings, complexity_label, pick_target
from stage_editor.document import SESSION_ROOT, StageDocument

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

PURPOSES = (UsdGeom.Tokens.default_, UsdGeom.Tokens.proxy, UsdGeom.Tokens.render)
SELECTION_COLOR = (1.0, 1.0, 0.0, 0.5)


def hydra_available() -> bool:
    """Return whether ``pxr.UsdImagingGL`` imported, so Storm can draw."""
    return _HYDRA


def conform_camera(camera: Gf.Camera, aspect: float) -> Gf.Camera:
    """Return a copy of ``camera`` whose aperture matches ``aspect``.

    The vertical aperture is kept and the horizontal one widened or
    narrowed, which is ``CameraUtil.MatchVertically``: looking through a
    stage camera shows its full height in any window shape.

    Args:
        camera: A stage camera, for example from ``UsdGeom.Camera.GetCamera``.
        aspect: Window width over height.
    """
    conformed = Gf.Camera(camera)
    conformed.horizontalAperture = conformed.verticalAperture * aspect
    conformed.horizontalApertureOffset = 0.0
    return conformed


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

    def adopt(self, camera: Gf.Camera) -> None:
        """Take over a camera's position and direction, dropping any roll.

        The orbit centre is the camera's focus point, so tumbling after
        looking through a stage camera turns around what it was aimed at.

        Args:
            camera: Usually the stage camera the view was looking through.
        """
        frustum = camera.frustum
        eye = frustum.position
        direction = frustum.ComputeViewDirection().GetNormalized()
        dist = camera.focusDistance
        if dist <= 0.0:
            dist = max(self.dist, 1e-3)
        self.fov = camera.GetFieldOfView(Gf.Camera.FOVVertical) or self.fov
        self.center = eye + direction * dist
        self.dist = dist
        # Undo the up-axis rotation, then read the angles back out of the
        # unit offset from centre to eye: (-cos(phi) sin(theta), sin(phi),
        # cos(phi) cos(theta)).
        offset = self._yz_inv.GetInverse().TransformDir(-direction)
        self.phi = math.degrees(math.asin(max(-1.0, min(1.0, offset[1]))))
        self.theta = math.degrees(math.atan2(-offset[0], offset[2]))

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


def stage_cameras(stage: Usd.Stage) -> list[str]:
    """Return the paths of every ``UsdGeom.Camera`` on the stage, in order.

    Args:
        stage: The stage to search, through instance proxies.
    """
    predicate = Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate)
    return [
        str(prim.GetPath())
        for prim in Usd.PrimRange(stage.GetPseudoRoot(), predicate)
        if prim.IsA(UsdGeom.Camera)
    ]


class HydraView(QOpenGLWidget):
    """Draw the document's stage with Storm and pick prims with the mouse.

    Left drag tumbles, middle drag trucks, right drag or the wheel dollies,
    and a left click picks. Moving the camera while looking through a stage
    camera switches back to the free camera from that camera's position.
    The engine is created on the first paint and rebuilt when the
    document's stage is replaced or the GL context is recreated. Call
    :meth:`shutdown` before the window closes, or Storm reports errors on
    teardown.

    Signals:
        pickedPath(str): The prim path under a click, mapped through the
            pick mode, or ``""`` for none.
        renderTimed(float): Milliseconds spent in the last ``Render`` call.
        cameraChanged(str): The look-through camera path, ``""`` for free.

    Args:
        document: The document to draw and follow.
        parent: Owning widget.

    Raises:
        RuntimeError: ``UsdImagingGL`` is not importable.
    """

    pickedPath = Signal(str)
    renderTimed = Signal(float)
    cameraChanged = Signal(str)

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
        self._settings = ViewSettings()
        self._engine = None
        self._gl: QOpenGLFunctions | None = None
        self._selected: list[str] = []
        self._camera_path = ""
        self._bbox_cache = UsdGeom.BBoxCache(document.time, list(PURPOSES), True)
        self._camera = OrbitCamera(self._stage_is_z_up())
        self._press: QPointF | None = None
        self._last: QPointF | None = None
        self._button = Qt.MouseButton.NoButton
        self._needs_frame = True
        self._params = self._make_params()
        self._stats: Counter[str] | None = None
        self._last_paint = 0.0
        self._fps = 0.0
        self._render_ms = 0.0
        document.stageReplaced.connect(self._on_stage_replaced)
        document.changed.connect(self._on_changed)
        document.timeChanged.connect(self._on_time_changed)
        self.frame_paths([])

    # -- public ---------------------------------------------------------------

    @property
    def settings(self) -> ViewSettings:
        """A copy of the current view settings."""
        return ViewSettings(**vars(self._settings))

    def set_settings(self, settings: ViewSettings) -> None:
        """Apply new view settings and redraw.

        Args:
            settings: The settings to draw with; the view keeps a copy.
        """
        previous = self._settings
        self._settings = ViewSettings(**vars(settings))
        if settings.dome_light != previous.dome_light:
            self._document.set_dome_light(settings.dome_light)
        self._params = self._make_params()
        if self._engine is not None and settings.aov != previous.aov:
            self._engine.SetRendererAov(settings.aov)
        self.update()

    @property
    def camera_path(self) -> str:
        """The stage camera the view looks through, or ``""`` for the free camera."""
        return self._camera_path

    def set_camera_path(self, path: str) -> None:
        """Look through the stage camera at ``path``, or ``""`` for the free camera.

        Args:
            path: Prim path of a ``UsdGeom.Camera`` as a string.
        """
        if path and not self._document.stage.GetPrimAtPath(path).IsA(UsdGeom.Camera):
            log.warning("not a camera: %s", path)
            path = ""
        if path == self._camera_path:
            return
        self._camera_path = path
        self.cameraChanged.emit(path)
        self.update()

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

        Framing switches to the free camera.

        Args:
            paths: Prim paths as strings.
        """
        self._leave_stage_camera()
        bbox = self._world_bound(paths or ["/"])
        # An empty stage has nothing to frame; frame again once content arrives.
        self._needs_frame = bbox.ComputeAlignedRange().IsEmpty()
        self._camera.frame(bbox)
        self.update()

    def grab_image(self) -> QImage:
        """Render a frame and return it, for "Save Viewer Image"."""
        return self.grabFramebuffer()

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

    def _world_bound(self, paths: Sequence[str]) -> Gf.BBox3d:
        stage = self._document.stage
        self._bbox_cache.SetTime(self._document.time)
        bbox = Gf.BBox3d()
        for path in paths:
            prim = stage.GetPrimAtPath(path)
            if prim and prim.IsValid():
                bbox = Gf.BBox3d.Combine(bbox, self._bbox_cache.ComputeWorldBound(prim))
        return bbox

    def _on_stage_replaced(self) -> None:
        self.shutdown()
        self._bbox_cache = UsdGeom.BBoxCache(self._document.time, list(PURPOSES), True)
        self._camera = OrbitCamera(self._stage_is_z_up())
        self._selected = []
        self._stats = None
        self._camera_path = ""
        self.cameraChanged.emit("")
        if self._settings.dome_light:
            self._document.set_dome_light(True)
        self.frame_paths([])

    def _on_changed(self, resynced: list[str], _info: list[str]) -> None:
        self._bbox_cache.Clear()
        if any(not Sdf.Path(p).IsPropertyPath() for p in resynced):
            self._stats = None
        if resynced and self._needs_frame:
            self.frame_paths([])
            return
        self.update()

    def _on_time_changed(self, _time: float) -> None:
        self.update()

    # -- engine ---------------------------------------------------------------

    def _make_params(self):
        settings = self._settings
        params = UsdImagingGL.RenderParams()
        params.drawMode = getattr(UsdImagingGL.DrawMode, settings.draw_mode)
        params.complexity = settings.complexity
        params.cullStyle = (
            UsdImagingGL.CullStyle.CULL_STYLE_BACK_UNLESS_DOUBLE_SIDED
            if settings.cull_backfaces
            else UsdImagingGL.CullStyle.CULL_STYLE_NOTHING
        )
        params.enableLighting = True
        params.enableSceneMaterials = settings.scene_materials
        params.enableSceneLights = settings.scene_lights
        params.showGuides = settings.show_guides
        params.showProxy = settings.show_proxy
        params.showRender = settings.show_render
        params.gammaCorrectColors = False
        params.colorCorrectionMode = "sRGB"
        params.clearColor = Gf.Vec4f(*settings.background)
        params.enableSampleAlphaToCoverage = True
        params.highlight = True
        return params

    def _ensure_engine(self):
        if self._engine is None:
            options = UsdImagingGL.Engine.Parameters()
            options.allowAsynchronousSceneProcessing = True
            engine = UsdImagingGL.Engine(options)
            engine.SetRendererAov(self._settings.aov)
            engine.SetSelectionColor(Gf.Vec4f(*SELECTION_COLOR))
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

    def _stage_camera(self, aspect: float) -> Gf.Camera | None:
        if not self._camera_path:
            return None
        prim = self._document.stage.GetPrimAtPath(self._camera_path)
        if not prim.IsA(UsdGeom.Camera):
            return None
        camera = UsdGeom.Camera(prim).GetCamera(Usd.TimeCode(self._document.time))
        return conform_camera(camera, aspect)

    def _active_camera(self, aspect: float) -> Gf.Camera:
        return self._stage_camera(aspect) or self._camera.camera(aspect)

    def _leave_stage_camera(self) -> None:
        """Switch to the free camera, starting where the stage camera was."""
        if not self._camera_path:
            return
        width, height = self._physical_size()
        camera = self._stage_camera(width / height if height > 0 else 1.0)
        if camera is not None:
            self._camera.adopt(camera)
        self._camera_path = ""
        self.cameraChanged.emit("")

    def _prim_stats(self) -> Counter[str]:
        if self._stats is None:
            predicate = Usd.TraverseInstanceProxies(Usd.PrimDefaultPredicate)
            counts: Counter[str] = Counter()
            root = self._document.stage.GetPseudoRoot()
            for prim in Usd.PrimRange(root, predicate):
                if prim.IsPseudoRoot() or prim.GetPath().HasPrefix(SESSION_ROOT):
                    continue
                counts["prims"] += 1
                if prim.IsA(UsdGeom.Mesh):
                    counts["meshes"] += 1
                elif prim.IsA(UsdGeom.Gprim):
                    counts["gprims"] += 1
                if prim.IsInstance():
                    counts["instances"] += 1
            self._stats = counts
        return self._stats

    # -- QOpenGLWidget --------------------------------------------------------

    def initializeGL(self) -> None:
        self._gl = QOpenGLFunctions()
        self._gl.initializeOpenGLFunctions()
        Glf.RegisterDefaultDebugOutputMessageCallback()
        context = self.context()
        if context is not None:
            # Re-docking can recreate the context; the engine must die with it.
            context.aboutToBeDestroyed.connect(self.shutdown)

    def paintGL(self) -> None:
        width, height = self._physical_size()
        gl = self._gl
        if width <= 0 or height <= 0 or gl is None:
            return
        engine = self._ensure_engine()
        camera = self._active_camera(width / height)
        frustum = camera.frustum
        engine.SetRenderBufferSize(Gf.Vec2i(width, height))
        engine.SetFraming(_framing(width, height))
        engine.SetOverrideWindowPolicy(CameraUtil.MatchVertically)
        engine.SetCameraState(
            frustum.ComputeViewMatrix(), frustum.ComputeProjectionMatrix()
        )

        lights = []
        if self._settings.camera_light:
            key = Glf.SimpleLight()
            position = frustum.position
            key.position = (position[0], position[1], position[2], 1)
            key.transform = frustum.ComputeViewInverse()
            lights.append(key)
        material = Glf.SimpleMaterial()
        material.ambient = (0.2, 0.2, 0.2, 1.0)
        material.specular = (0.1, 0.1, 0.1, 1.0)
        material.shininess = 32.0
        engine.SetLightingState(lights, material, (0.01, 0.01, 0.01, 1.0))

        background = self._settings.background
        gl.glDepthMask(True)
        gl.glClearColor(*background)
        gl.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        gl.glViewport(0, 0, width, height)
        gl.glEnable(GL_BLEND)
        gl.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        self._params.frame = Usd.TimeCode(self._document.time)
        self._params.clipPlanes = [Gf.Vec4d(c) for c in camera.clippingPlanes]
        self._params.bboxes = (
            [self._world_bound([p]) for p in self._selected]
            if self._settings.show_bounds
            else []
        )
        started = time.perf_counter()
        try:
            engine.Render(self._document.stage.GetPseudoRoot(), self._params)
        except Tf.ErrorException:
            log.exception("Storm render failed")
        elapsed = (time.perf_counter() - started) * 1000.0
        gl.glPixelStorei(GL_UNPACK_ALIGNMENT, 4)
        self._render_ms = elapsed
        interval = started - self._last_paint
        if interval < 0.5:
            instant = 1.0 / max(interval, 1e-6)
            self._fps = instant if not self._fps else self._fps * 0.9 + instant * 0.1
        else:
            # Idle between redraws: a rate means nothing until frames flow again.
            self._fps = 0.0
        self._last_paint = started
        self.renderTimed.emit(elapsed)
        if self._settings.show_hud:
            self._paint_hud()

    def _hud_lines(self) -> list[str]:
        stats = self._prim_stats()
        complexity = complexity_label(self._settings.complexity)
        camera = self._camera_path.rsplit("/", 1)[-1] if self._camera_path else "Free"
        return [
            f"Storm  {self._render_ms:.1f} ms"
            + (f"  {self._fps:.0f} fps" if self._fps else ""),
            f"Prims {stats['prims']}  Meshes {stats['meshes']}"
            f"  Other gprims {stats['gprims']}  Instances {stats['instances']}",
            f"Camera {camera}  Complexity {complexity}",
            f"Frame {self._document.time:g}",
        ]

    def _paint_hud(self) -> None:
        painter = QPainter(self)
        font = QFont(painter.font())
        font.setStyleHint(QFont.StyleHint.Monospace)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        y = metrics.ascent() + 8
        for line in self._hud_lines():
            painter.setPen(QColor(0, 0, 0, 160))
            painter.drawText(11, y + 1, line)
            painter.setPen(QColor(230, 200, 90))
            painter.drawText(10, y, line)
            y += metrics.height()
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
        if delta.isNull():
            return
        self._leave_stage_camera()
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
            hit = self.pick(event.position())
            self.pickedPath.emit(
                pick_target(self._document.stage, hit, self._settings.pick_mode)
            )

    def wheelEvent(self, event: QWheelEvent) -> None:
        self._leave_stage_camera()
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

        Session-layer helper prims (the dome light) are never returned.

        Args:
            position: Point in widget coordinates.
        """
        width, height = self._physical_size()
        if width <= 0 or height <= 0:
            return ""
        ratio = self.devicePixelRatioF()
        x, y = position.x() * ratio, position.y() * ratio
        camera = self._active_camera(width / height)
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
        for hit in hits or []:
            if not hit.hitPrimPath.HasPrefix(SESSION_ROOT):
                return str(hit.hitPrimPath)
        return ""
