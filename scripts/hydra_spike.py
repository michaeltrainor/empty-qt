"""Hydra Storm inside a PySide6 QOpenGLWidget: build-spike viewer for mtqt.

Opens a USD stage, draws it with ``UsdImagingGL.Engine`` (Storm), orbits with
the mouse, picks prims on click, plays stage time, and prints timings.

Usage (through run.cmd, which sets PYTHONPATH/PATH to the local install)::

    run.cmd STAGE.usd [--play] [--bench N] [--screenshot out.png]
                      [--complexity 1.0] [--renderer HdStormRendererPlugin]

Left drag tumbles, middle drag trucks, right drag or wheel dollies, click
picks, space toggles playback, F frames the scene, Esc quits.
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time

from OpenGL import GL
from pxr import CameraUtil, Gf, Glf, Tf, Usd, UsdGeom, UsdImagingGL
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtOpenGLWidgets import QOpenGLWidget


def _framing(viewport, buffer_size):
    x, y, w, h = viewport
    bw, bh = buffer_size
    display = Gf.Range2f(Gf.Vec2f(x, bh - y - h), Gf.Vec2f(x + w, bh - y))
    data = Gf.Rect2i(Gf.Vec2i(0, 0), bw, bh).GetIntersection(
        Gf.Rect2i(Gf.Vec2i(x, bh - y - h), w, h)
    )
    return CameraUtil.Framing(display, data)


class OrbitCamera:
    """usdview-style free camera: tumble/truck/dolly around a centre."""

    def __init__(self, z_up: bool, fov: float = 60.0):
        self.z_up = z_up
        self.fov = fov
        self.theta = 0.0
        self.phi = 0.0
        self.center = Gf.Vec3d(0, 0, 0)
        self.dist = 100.0
        self.size = 100.0
        self._yz_inv = (
            Gf.Matrix4d(1.0).SetRotate(Gf.Rotation(Gf.Vec3d.XAxis(), 90))
            if z_up
            else Gf.Matrix4d(1.0)
        )

    def frame(self, bbox: Gf.BBox3d) -> None:
        rng = bbox.ComputeAlignedRange()
        if rng.IsEmpty():
            return
        self.center = bbox.ComputeCentroid()
        self.size = max(*rng.GetSize())
        self.dist = (self.size * 0.55) / math.tan(math.radians(self.fov * 0.5))
        self.theta, self.phi = -30.0, 20.0

    def tumble(self, dtheta: float, dphi: float) -> None:
        self.theta += dtheta
        self.phi += dphi

    def dolly(self, factor: float) -> None:
        self.dist = max(self.dist * factor, self.size * 1e-3)

    def truck(self, dx: float, dy: float) -> None:
        f = self.camera(1.0).frustum
        right = Gf.Cross(f.ComputeViewDirection(), f.ComputeUpVector())
        self.center += f.ComputeUpVector() * dy + right * dx

    def camera(self, aspect: float) -> Gf.Camera:
        def rot(axis, angle):
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


class HydraView(QOpenGLWidget):
    """QOpenGLWidget that draws one stage with Storm and reports timings."""

    def __init__(self, stage: Usd.Stage, args, parent=None):
        super().__init__(parent)
        fmt = QtGui.QSurfaceFormat()
        fmt.setDepthBufferSize(24)
        fmt.setStencilBufferSize(8)
        self.setFormat(fmt)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(320, 240)

        self.stage = stage
        self.args = args
        self._engine: UsdImagingGL.Engine | None = None
        self._first_image_t0: float | None = None
        self.render_ms: list[float] = []
        self.bench_frames: list[float] = []
        self._bench_t0 = 0.0
        self.hit_path = ""

        self.frame = stage.GetStartTimeCode()
        self.playing = False
        self._timer = QtCore.QTimer(self)
        self._timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)

        z_up = UsdGeom.GetStageUpAxis(stage) == UsdGeom.Tokens.z
        self.cam = OrbitCamera(z_up)
        purposes = [
            UsdGeom.Tokens.default_,
            UsdGeom.Tokens.proxy,
            UsdGeom.Tokens.render,
        ]
        self._bbox_cache = UsdGeom.BBoxCache(self.frame, purposes, True)
        t0 = time.perf_counter()
        self.cam.frame(self._bbox_cache.ComputeWorldBound(stage.GetPseudoRoot()))
        self.bbox_s = time.perf_counter() - t0

        p = UsdImagingGL.RenderParams()
        p.drawMode = UsdImagingGL.DrawMode.DRAW_SHADED_SMOOTH
        p.complexity = args.complexity
        p.cullStyle = UsdImagingGL.CullStyle.CULL_STYLE_BACK_UNLESS_DOUBLE_SIDED
        p.enableSceneMaterials = True
        p.enableSceneLights = True
        p.showGuides = False
        p.showProxy = True
        p.showRender = False
        p.gammaCorrectColors = False
        p.colorCorrectionMode = "sRGB"
        p.clearColor = Gf.Vec4f(0.18, 0.18, 0.2, 1.0)
        p.enableSampleAlphaToCoverage = True
        self.params = p

        self._drag_button = None
        self._last_pos = None
        self._press_pos = None

    # -- engine -----------------------------------------------------------
    def engine(self) -> UsdImagingGL.Engine:
        if self._engine is None:
            self._first_image_t0 = time.perf_counter()
            p = UsdImagingGL.Engine.Parameters()
            p.allowAsynchronousSceneProcessing = True
            self._engine = UsdImagingGL.Engine(p)
            if self.args.renderer:
                ok = self._engine.SetRendererPlugin(self.args.renderer)
                print("renderer", self.args.renderer, "ok" if ok else "FAILED")
            print("renderer plugins:", list(self._engine.GetRendererPlugins()))
            print(
                "active renderer:",
                self._engine.GetCurrentRendererId(),
                self._engine.GetRendererHgiDisplayName(),
            )
            self._engine.SetRendererAov("color")
            self._engine.SetSelectionColor(Gf.Vec4f(1.0, 1.0, 0.0, 1.0))
        return self._engine

    def shutdown(self):
        """Destroy the engine while the GL context is still current."""
        if self._engine is not None:
            self.makeCurrent()
            self._engine = None
            self.doneCurrent()

    def initializeGL(self):
        Glf.RegisterDefaultDebugOutputMessageCallback()
        version = GL.glGetString(GL.GL_VERSION).decode()
        renderer = GL.glGetString(GL.GL_RENDERER).decode()
        print("GL:", version, "|", renderer)

    def _physical_size(self):
        s = self.size() * self.devicePixelRatioF()
        return int(s.width()), int(s.height())

    def paintGL(self):
        w, h = self._physical_size()
        if w <= 0 or h <= 0:
            return
        renderer = self.engine()
        cam = self.cam.camera(w / h)
        frustum = cam.frustum
        renderer.SetRenderBufferSize(Gf.Vec2i(w, h))
        renderer.SetFraming(_framing((0, 0, w, h), (w, h)))
        renderer.SetOverrideWindowPolicy(CameraUtil.MatchVertically)
        renderer.SetCameraState(
            frustum.ComputeViewMatrix(), frustum.ComputeProjectionMatrix()
        )

        lights = []
        key = Glf.SimpleLight()
        pos = frustum.position
        key.position = (pos[0], pos[1], pos[2], 1)
        key.transform = frustum.ComputeViewInverse()
        lights.append(key)
        material = Glf.SimpleMaterial()
        material.ambient = (0.2, 0.2, 0.2, 1.0)
        material.specular = (0.1, 0.1, 0.1, 1.0)
        material.shininess = 32.0
        renderer.SetLightingState(lights, material, (0.01, 0.01, 0.01, 1.0))

        GL.glDepthMask(GL.GL_TRUE)
        GL.glClearColor(0.18, 0.18, 0.2, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        GL.glViewport(0, 0, w, h)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)

        self.params.frame = Usd.TimeCode(self.frame)
        self.params.clipPlanes = [Gf.Vec4d(c) for c in cam.clippingPlanes]
        t0 = time.perf_counter()
        try:
            renderer.Render(self.stage.GetPseudoRoot(), self.params)
        except Tf.ErrorException as exc:
            print("render error:", exc, file=sys.stderr)
        dt = (time.perf_counter() - t0) * 1000.0
        self.render_ms.append(dt)
        if self._first_image_t0 is not None:
            first = time.perf_counter() - self._first_image_t0
            print(f"time to first image: {first:.2f} s (Render call {dt:.1f} ms)")
            self._first_image_t0 = None
            QtCore.QTimer.singleShot(0, self._after_first_image)
        if self.playing:
            self.bench_frames.append(dt)

        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 4)
        painter = QtGui.QPainter(self)
        painter.setPen(QtGui.QColor(230, 200, 90))
        recent = self.render_ms[-30:]
        avg = sum(recent) / len(recent)
        state = "playing" if self.playing else ""
        painter.drawText(
            10,
            20,
            f"Render {avg:.1f} ms ({1000.0 / max(avg, 1e-3):.0f} fps)  "
            f"frame {self.frame:.0f}  {state}",
        )
        if self.hit_path:
            painter.drawText(10, 40, f"pick: {self.hit_path}")
        painter.end()

    def _after_first_image(self):
        if self.args.screenshot:
            img = self.grabFramebuffer()
            img.save(self.args.screenshot)
            print("screenshot:", self.args.screenshot, img.size())
        if self.args.pick:
            self.pick(QtCore.QPointF(self.width() / 2, self.height() / 2))
        if self.args.bench or self.args.play:
            self.set_playing(True)

    # -- playback ---------------------------------------------------------
    def set_playing(self, on: bool):
        self.playing = on
        if on:
            self.bench_frames = []
            self._bench_t0 = time.perf_counter()
            self._timer.start(0)
        else:
            self._timer.stop()
        self.update()

    def _tick(self):
        end = self.stage.GetEndTimeCode()
        start = self.stage.GetStartTimeCode()
        self.frame = start if self.frame + 1 > end else self.frame + 1
        if self.args.bench and len(self.bench_frames) >= self.args.bench:
            wall = time.perf_counter() - self._bench_t0
            n = len(self.bench_frames)
            print(
                f"bench: {n} frames, wall {wall:.2f} s = {n / wall:.1f} fps; "
                f"Render() mean {statistics.mean(self.bench_frames):.2f} ms, "
                f"median {statistics.median(self.bench_frames):.2f} ms, "
                f"max {max(self.bench_frames):.2f} ms"
            )
            QtWidgets.QApplication.instance().quit()
            return
        self.repaint()

    # -- interaction ------------------------------------------------------
    def mousePressEvent(self, ev: QtGui.QMouseEvent):
        self._drag_button = ev.button()
        self._last_pos = self._press_pos = ev.position()

    def mouseMoveEvent(self, ev: QtGui.QMouseEvent):
        if self._last_pos is None:
            return
        d = ev.position() - self._last_pos
        self._last_pos = ev.position()
        if self._drag_button == QtCore.Qt.MouseButton.LeftButton:
            self.cam.tumble(d.x() * 0.4, d.y() * 0.4)
        elif self._drag_button == QtCore.Qt.MouseButton.MiddleButton:
            scale = self.cam.dist * 0.002
            self.cam.truck(-d.x() * scale, d.y() * scale)
        elif self._drag_button == QtCore.Qt.MouseButton.RightButton:
            self.cam.dolly(1.0 + d.x() * 0.01)
        self.update()

    def mouseReleaseEvent(self, ev: QtGui.QMouseEvent):
        if (
            self._press_pos is not None
            and (ev.position() - self._press_pos).manhattanLength() < 3
            and ev.button() == QtCore.Qt.MouseButton.LeftButton
        ):
            self.pick(ev.position())
        self._drag_button = self._last_pos = self._press_pos = None

    def wheelEvent(self, ev: QtGui.QWheelEvent):
        self.cam.dolly(0.9 if ev.angleDelta().y() > 0 else 1.1)
        self.update()

    def keyPressEvent(self, ev: QtGui.QKeyEvent):
        if ev.key() == QtCore.Qt.Key.Key_Space:
            self.set_playing(not self.playing)
        elif ev.key() == QtCore.Qt.Key.Key_F:
            root = self.stage.GetPseudoRoot()
            self.cam.frame(self._bbox_cache.ComputeWorldBound(root))
            self.update()
        elif ev.key() == QtCore.Qt.Key.Key_Escape:
            QtWidgets.QApplication.instance().quit()

    def pick(self, pos):
        w, h = self._physical_size()
        r = self.devicePixelRatioF()
        x, y = pos.x() * r, pos.y() * r
        cam = self.cam.camera(w / h)
        point = Gf.Vec2d(x / w * 2.0 - 1.0, -(y / h * 2.0 - 1.0))
        size = Gf.Vec2d(1.0 / w, 1.0 / h)
        narrowed = cam.frustum.ComputeNarrowedFrustum(point, size)
        pick = UsdImagingGL.Engine.PickParams()
        pick.resolveMode = "resolveNearestToCenter"
        self.makeCurrent()
        self.params.frame = Usd.TimeCode(self.frame)
        t0 = time.perf_counter()
        result = self.engine().TestIntersection(
            pick,
            narrowed.ComputeViewMatrix(),
            narrowed.ComputeProjectionMatrix(),
            self.stage.GetPseudoRoot(),
            self.params,
        )
        dt = (time.perf_counter() - t0) * 1000.0
        self.engine().ClearSelected()
        if result:
            hit = result[0]
            prim_path, instance_index = hit.hitPrimPath, hit.hitInstanceIndex
            self.hit_path = f"{prim_path} inst={instance_index} ({dt:.1f} ms)"
            self.engine().AddSelected(prim_path, UsdImagingGL.ALL_INSTANCES)
        else:
            self.hit_path = f"(nothing, {dt:.1f} ms)"
        print("pick:", self.hit_path)
        self.update()


def main(argv=None) -> int:
    """Open the stage named on the command line and run the viewer."""
    ap = argparse.ArgumentParser()
    ap.add_argument("stage")
    ap.add_argument("--play", action="store_true")
    ap.add_argument(
        "--bench", type=int, default=0, help="play N frames, print fps, quit"
    )
    ap.add_argument("--screenshot")
    ap.add_argument("--complexity", type=float, default=1.0)
    ap.add_argument("--renderer", default="")
    ap.add_argument("--size", default="1280x800")
    ap.add_argument(
        "--pick", action="store_true", help="pick the view centre after the first image"
    )
    args = ap.parse_args(argv)

    app = QtWidgets.QApplication(sys.argv[:1])

    t0 = time.perf_counter()
    stage = Usd.Stage.Open(args.stage, Usd.Stage.LoadAll)
    open_s = time.perf_counter() - t0
    if not stage:
        print("cannot open", args.stage)
        return 1
    t0 = time.perf_counter()
    prims = list(Usd.PrimRange.Stage(stage, Usd.TraverseInstanceProxies()))
    n_meshes = sum(1 for p in prims if p.IsA(UsdGeom.Mesh))
    count_s = time.perf_counter() - t0
    print(
        f"stage open {open_s:.2f} s; {len(prims)} prims, {n_meshes} meshes "
        f"(count {count_s:.2f} s); time {stage.GetStartTimeCode():.0f}.."
        f"{stage.GetEndTimeCode():.0f} @ {stage.GetTimeCodesPerSecond():g} tcps; "
        f"up {UsdGeom.GetStageUpAxis(stage)}"
    )

    view = HydraView(stage, args)
    print(f"bbox {view.bbox_s:.2f} s, size {view.cam.size:.3g}")
    w, h = (int(v) for v in args.size.split("x"))
    view.resize(w, h)
    view.setWindowTitle(f"Hydra spike: {args.stage}")
    view.show()
    app.aboutToQuit.connect(view.shutdown)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
