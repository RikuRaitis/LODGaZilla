"""Shared OpenGL viewport for flat video and direct dual-fisheye projection."""
import math
import logging
import time

import numpy as np
from OpenGL import GL as gl
from OpenGL.GL.shaders import compileProgram, compileShader
from PySide6.QtCore import Qt, Signal
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtGui import QSurfaceFormat
from src.gui.plots.theme import canvas_background

from .calibration import Metadata
from .preview import preview_size, render_size

VERTEX = """#version 330 core
out vec2 screen;
void main() {
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    screen = p * 2.0 - 1.0;
    gl_Position = vec4(screen, 0.0, 1.0);
}
"""

FRAGMENT = """#version 330 core
in vec2 screen;
out vec4 color;
uniform sampler2D y0, u0, v0, y1, u1, v1;
uniform sampler2D rgb0, rgb1;
uniform int rgb_mode[2];
uniform float tangent, aspect;
uniform mat3 view_rotation;
uniform bool flat_video;
uniform float source_aspect;
uniform vec2 flat_size, flat_rotation;
uniform vec4 canvas_color;
struct Lens {
    float xi;
    vec2 focal, center;
    vec4 radial;
    float k5;
    vec4 tangential, prism;
};
uniform Lens lens[2];
uniform vec4 coefficients[2];
uniform vec2 levels[2];

vec3 project(vec3 d, Lens l) {
    float theta = acos(clamp(d.z, -1.0, 1.0));
    if (theta > 1.80) return vec3(0.0);
    vec2 xy;
    if (l.xi > 0.0) {
        if (l.xi * d.z + 1.0 <= 0.0) return vec3(0.0);
        vec2 p = d.xy / (l.xi + d.z);
        float r = dot(p, p);
        float D = 1.0 + r * (l.radial.x + r * (l.radial.y + r *
                  (l.radial.z + r * (l.radial.w + r * l.k5))));
        float a = l.tangential.x + l.tangential.z * r;
        float b = l.tangential.y + l.tangential.w * r;
        xy = p * D + vec2((r + 2.0*p.x*p.x)*a + 2.0*p.x*p.y*b,
                          (r + 2.0*p.y*p.y)*b + 2.0*p.x*p.y*a)
             + vec2(l.prism.x*r + l.prism.y*r*r,
                    l.prism.z*r + l.prism.w*r*r);
    } else {
        float t = theta*theta;
        float radius = theta * (1.0 + t*(l.radial.x + t*(l.radial.y +
                              t*(l.radial.z + t*l.radial.w))));
        xy = d.xy * radius / max(length(d.xy), 1e-8);
    }
    vec2 uv = l.center + l.focal * xy;
    float valid = step(0.0, uv.x)*step(uv.x, 1.0)*step(0.0, uv.y)*step(uv.y, 1.0);
    return vec3(uv, valid * smoothstep(-0.17, 0.17, d.z));
}

vec3 sampleYuv(sampler2D Y, sampler2D U, sampler2D V, vec2 uv, int i) {
    if (rgb_mode[i] != 0) {
        if (i == 0) return texture(rgb0, uv).rgb;
        return texture(rgb1, uv).rgb;
    }
    float y = (texture(Y, uv).r - levels[i].x) * levels[i].y;
    vec2 c = vec2(texture(U, uv).r, texture(V, uv).r) - vec2(128.0/255.0);
    vec4 k = coefficients[i];
    return vec3(y + k.x*c.y, y + k.y*c.x + k.z*c.y, y + k.w*c.x);
}

void main() {
    if (flat_video) {
        float image_aspect = flat_size.x / flat_size.y;
        vec2 fit = image_aspect > aspect ? vec2(1.0, aspect / image_aspect)
                                        : vec2(image_aspect / aspect, 1.0);
        vec2 p = screen / fit * flat_size;
        float c = flat_rotation.x, s = flat_rotation.y;
        vec2 uv = vec2((c*p.x + s*p.y) / source_aspect, s*p.x - c*p.y) * 0.5 + 0.5;
        if (any(lessThan(uv, vec2(0.0))) || any(greaterThan(uv, vec2(1.0)))) {
            color = canvas_color;
        } else {
            color = vec4(clamp(sampleYuv(y0, u0, v0, uv, 0), 0.0, 1.0), 1.0);
        }
        return;
    }
    vec3 d = normalize(vec3(screen.x*tangent, -screen.y*tangent/aspect, 1.0));
    d = view_rotation * d;
    vec3 a = project(d, lens[0]);
    vec3 b = project(vec3(-d.x, d.y, -d.z), lens[1]);
    vec3 rgb = (sampleYuv(y0,u0,v0,a.xy,0)*a.z + sampleYuv(y1,u1,v1,b.xy,1)*b.z)
               / max(a.z + b.z, 1e-6);
    color = vec4(clamp(rgb, 0.0, 1.0), 1.0);
}
"""


class Viewport(QOpenGLWidget):
    failed = Signal(str)
    preview_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
        fmt.setSwapInterval(1)
        self.setFormat(fmt)
        self.setMinimumSize(400, 240)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.metadata = Metadata()
        self.picture = None
        self.upload = False
        self.view_rotation = np.eye(3)
        self.fov = 90.0
        self.drag = None
        self.program = None
        self.textures = []
        self.sizes = [None] * 6
        self.fbo = self.target_texture = self.vao = None
        self.target_size = None
        self.error = None
        self.shared_lenses = [None, None]
        self.max_side = 1536
        self.render_ms = 0.0
        self.stage_ms = {}

    def set_picture(self, picture):
        old = self.picture
        self.picture = picture
        self.upload = True
        if old is None or (old.projection, old.source_size, old.source_aspect, old.rotation) != (
                picture.projection, picture.source_size, picture.source_aspect, picture.rotation):
            self.preview_changed.emit()
        self.update()

    def suggested_preview_size(self):
        if self.picture is None:
            return None
        picture = self.picture
        source_size = picture.source_size
        if not all(source_size):
            source_size = (picture.frames[0].width, picture.frames[0].height)
        return preview_size(self.width(), self.height(), self.devicePixelRatioF(),
                            picture.projection, source_size, picture.source_aspect,
                            picture.rotation, self.fov)

    def resizeGL(self, width, height):
        self.preview_changed.emit()

    def reset_view(self):
        self.view_rotation = np.eye(3)
        self.fov = 90.0
        self.drag = None
        self.unsetCursor()
        self.preview_changed.emit()
        self.update()

    def look(self, horizontal, vertical):
        """Turn in degrees around the current screen axes, including at the poles."""
        yaw, pitch = math.radians(horizontal), math.radians(vertical)
        cy, sy = math.cos(yaw), math.sin(yaw)
        cp, sp = math.cos(pitch), math.sin(pitch)
        sideways = np.array(((cy, 0, sy), (0, 1, 0), (-sy, 0, cy)))
        up_down = np.array(((1, 0, 0), (0, cp, sp), (0, -sp, cp)))
        # Post-multiply so dragging follows the view, not a fixed scene axis
        # that becomes a roll axis when looking up or down.
        self.view_rotation = self.view_rotation @ sideways @ up_down
        self.update()

    def initializeGL(self):
        try:
            self.program = compileProgram(compileShader(VERTEX, gl.GL_VERTEX_SHADER),
                                          compileShader(FRAGMENT, gl.GL_FRAGMENT_SHADER))
            self.vao = gl.glGenVertexArrays(1)
            self.textures = list(gl.glGenTextures(6))
            self.fbo = gl.glGenFramebuffers(1)
            self.target_texture = gl.glGenTextures(1)
            self.context().aboutToBeDestroyed.connect(self.cleanup)
            logging.getLogger(__name__).info('OpenGL renderer: %s', gl.glGetString(gl.GL_RENDERER))
            self.upload = True
        except Exception as exc:
            self.error = str(exc)
            self.failed.emit('OpenGL 3.3 initialization failed: ' + str(exc))

    def cleanup(self):
        # Explicit shutdown and aboutToBeDestroyed may both reach this method.
        if not (self.program or self.textures or self.target_texture or self.fbo
                or self.vao or any(self.shared_lenses)):
            return
        self.makeCurrent()
        self.clear_shared()
        if self.program:
            gl.glDeleteProgram(self.program)
        if self.textures:
            gl.glDeleteTextures(self.textures)
        if self.target_texture:
            gl.glDeleteTextures([self.target_texture])
        if self.fbo:
            gl.glDeleteFramebuffers(1, [self.fbo])
        if self.vao:
            gl.glDeleteVertexArrays(1, [self.vao])
        self.program = self.fbo = self.vao = self.target_texture = None
        self.textures = []
        self.sizes = [None] * 6
        self.target_size = None
        self.doneCurrent()

    def clear_shared(self):
        for lens in self.shared_lenses:
            if lens:
                lens.close()
        self.shared_lenses = [None, None]

    def clear_picture(self):
        if self.context() and self.context().isValid():
            self.makeCurrent()
            self.clear_shared()
            self.doneCurrent()
        self.picture = None
        self.error = None
        self.update()

    def uniform(self, name, *values):
        location = gl.glGetUniformLocation(self.program, name)
        {1: gl.glUniform1f, 2: gl.glUniform2f, 3: gl.glUniform3f,
         4: gl.glUniform4f}[len(values)](location, *values)

    @staticmethod
    def texture_parameters():
        for parameter in (gl.GL_TEXTURE_MIN_FILTER, gl.GL_TEXTURE_MAG_FILTER):
            gl.glTexParameteri(gl.GL_TEXTURE_2D, parameter, gl.GL_LINEAR)
        for parameter in (gl.GL_TEXTURE_WRAP_S, gl.GL_TEXTURE_WRAP_T):
            gl.glTexParameteri(gl.GL_TEXTURE_2D, parameter, gl.GL_CLAMP_TO_EDGE)

    def paintGL(self):
        started = time.perf_counter()
        gl.glClearColor(*canvas_background(self).getRgbF())
        gl.glClear(gl.GL_COLOR_BUFFER_BIT)
        if not self.program or self.picture is None or self.error:
            return
        try:
            self._draw()
        except Exception as exc:
            self.error = str(exc)
            self.failed.emit('Rendering failed: ' + str(exc))
        finally:
            unlock_at = time.perf_counter()
            for lens in reversed(self.shared_lenses):
                if lens:
                    lens.unlock()
            self.render_ms = (time.perf_counter() - started) * 1000
            self.stage_ms['unlock'] = (time.perf_counter() - unlock_at) * 1000

    def _draw(self):
        phase_at = time.perf_counter()
        frames = self.picture.frames
        flat_video = self.picture.projection == 'flat'
        if not flat_video and self.metadata.reversed_tracks:
            frames = frames[::-1]
        # Finish every D3D operation before locking any resource for OpenGL.
        # Interleaving D3D work from one device with another device's GL lock
        # can serialize the driver or deadlock on Intel's WGL implementation.
        for i, frame in enumerate(frames):
            if frame.format.name != 'd3d11':
                continue
            from .d3d11 import SharedLens
            shared = self.shared_lenses[i]
            if shared and shared.max_side != self.max_side:
                shared.close()
                shared = self.shared_lenses[i] = None
            if shared is None:
                shared = self.shared_lenses[i] = SharedLens(self.context(), frame, self.max_side)
            if self.upload:
                shared.process(frame)
        self.stage_ms = {'prepare': (time.perf_counter() - phase_at)*1000, 'lock': 0}
        # Cap shader work even on a maximized/high-DPI window, then GPU upscale.
        width = max(1, round(self.width() * self.devicePixelRatioF()))
        height = max(1, round(self.height() * self.devicePixelRatioF()))
        size = render_size(self.width(), self.height(), self.devicePixelRatioF())
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self.fbo)
        if size != self.target_size:
            gl.glBindTexture(gl.GL_TEXTURE_2D, self.target_texture)
            self.texture_parameters()
            gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_RGB8, *size, 0,
                            gl.GL_RGB, gl.GL_UNSIGNED_BYTE, None)
            gl.glFramebufferTexture2D(gl.GL_FRAMEBUFFER, gl.GL_COLOR_ATTACHMENT0,
                                      gl.GL_TEXTURE_2D, self.target_texture, 0)
            if gl.glCheckFramebufferStatus(gl.GL_FRAMEBUFFER) != gl.GL_FRAMEBUFFER_COMPLETE:
                raise RuntimeError('Viewport framebuffer is incomplete')
            self.target_size = size
        gl.glViewport(0, 0, *size)
        gl.glUseProgram(self.program)
        self.uniform('canvas_color', *canvas_background(self).getRgbF())
        gl.glBindVertexArray(self.vao)
        gl.glUniformMatrix3fv(gl.glGetUniformLocation(self.program, 'view_rotation'),
                             1, gl.GL_TRUE, self.view_rotation.astype(np.float32))
        self.uniform('tangent', math.tan(math.radians(self.fov / 2)))
        self.uniform('aspect', width / height)
        gl.glUniform1i(gl.glGetUniformLocation(self.program, 'flat_video'), int(flat_video))
        if flat_video:
            angle = math.radians(self.picture.rotation)
            c, s = math.cos(angle), math.sin(angle)
            source_aspect = self.picture.source_aspect
            self.uniform('source_aspect', source_aspect)
            self.uniform('flat_rotation', c, s)
            self.uniform('flat_size', abs(c)*source_aspect + abs(s),
                         abs(s)*source_aspect + abs(c))
        for i, frame in enumerate(frames):
            is_gpu = frame.format.name == 'd3d11'
            gl.glUniform1i(gl.glGetUniformLocation(self.program, f'rgb_mode[{i}]'), int(is_gpu))
            gl.glUniform1i(gl.glGetUniformLocation(self.program, f'rgb{i}'), 6 + i)
            if not flat_video:
                lens = self.metadata.lenses[i]
                for name, values in (('xi', (lens.xi,)), ('focal', lens.focal),
                                     ('center', lens.center), ('radial', lens.radial[:4]),
                                     ('k5', (lens.radial[4],)), ('tangential', lens.tangential),
                                     ('prism', lens.prism)):
                    self.uniform(f'lens[{i}].{name}', *values)
            if is_gpu:
                shared = self.shared_lenses[i]
                lock_at = time.perf_counter()
                shared.lock()
                self.stage_ms['lock'] += (time.perf_counter() - lock_at)*1000
                gl.glActiveTexture(gl.GL_TEXTURE0 + 6 + i)
                gl.glBindTexture(gl.GL_TEXTURE_2D, shared.texture)
                continue
            # Preserve stream color matrix/range; no implicit log/HDR tone map.
            space = int(frame.colorspace)
            kr, kb = ((0.2627, 0.0593) if space == 9 else
                      (0.299, 0.114) if space in (5, 6) else (0.2126, 0.0722))
            kg = 1 - kr - kb
            full = int(frame.color_range) == 2
            gain = 1.0 if full else 255 / 224
            self.uniform(f'coefficients[{i}]', 2*(1-kr)*gain,
                         -2*kb*(1-kb)/kg*gain, -2*kr*(1-kr)/kg*gain, 2*(1-kb)*gain)
            self.uniform(f'levels[{i}]', 0.0 if full else 16/255, 1.0 if full else 255/219)
            for channel, plane in enumerate(frame.planes):
                index = i*3 + channel
                gl.glActiveTexture(gl.GL_TEXTURE0 + index)
                gl.glBindTexture(gl.GL_TEXTURE_2D, self.textures[index])
                gl.glUniform1i(gl.glGetUniformLocation(self.program, 'yuv'[channel] + str(i)), index)
                if self.upload:
                    dimensions = (plane.width, plane.height)
                    gl.glPixelStorei(gl.GL_UNPACK_ALIGNMENT, 1)
                    gl.glPixelStorei(gl.GL_UNPACK_ROW_LENGTH, plane.line_size)
                    data = np.frombuffer(plane, dtype=np.uint8)
                    if self.sizes[index] != dimensions:
                        self.texture_parameters()
                        gl.glTexImage2D(gl.GL_TEXTURE_2D, 0, gl.GL_R8, *dimensions,
                                        0, gl.GL_RED, gl.GL_UNSIGNED_BYTE, data)
                        self.sizes[index] = dimensions
                    else:
                        gl.glTexSubImage2D(gl.GL_TEXTURE_2D, 0, 0, 0, *dimensions,
                                           gl.GL_RED, gl.GL_UNSIGNED_BYTE, data)
                    gl.glPixelStorei(gl.GL_UNPACK_ROW_LENGTH, 0)
        self.upload = False
        gl.glDrawArrays(gl.GL_TRIANGLES, 0, 3)
        gl.glBindVertexArray(0)
        gl.glUseProgram(0)
        gl.glBindFramebuffer(gl.GL_READ_FRAMEBUFFER, self.fbo)
        gl.glBindFramebuffer(gl.GL_DRAW_FRAMEBUFFER, self.defaultFramebufferObject())
        gl.glBlitFramebuffer(0, 0, *size, 0, 0, width, height,
                             gl.GL_COLOR_BUFFER_BIT, gl.GL_LINEAR)
        gl.glBindFramebuffer(gl.GL_FRAMEBUFFER, self.defaultFramebufferObject())

    def mousePressEvent(self, event):
        if self.picture is not None and self.picture.projection == 'flat':
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self.drag is not None:
            delta = event.position() - self.drag
            self.drag = event.position()
            sensitivity = self.fov / max(1, self.width())
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                angle = math.radians(delta.x() * sensitivity)
                c, s = math.cos(angle), math.sin(angle)
                self.view_rotation = self.view_rotation @ np.array(((c, -s, 0), (s, c, 0), (0, 0, 1)))
                self.update()
            else:
                self.look(-delta.x() * sensitivity, -delta.y() * sensitivity)

    def mouseReleaseEvent(self, event):
        self.drag = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, event):
        self.reset_view()

    def wheelEvent(self, event):
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            event.ignore()
            return
        if self.picture is not None and self.picture.projection == 'flat':
            event.ignore()
            return
        self.fov = max(35, min(120, self.fov - event.angleDelta().y() / 120 * 5))
        self.preview_changed.emit()
        self.update()
        event.accept()
