"""A 3D model's meshes in the 3D plot, drawn by OpenGL 3.3 core shaders.

pyqtgraph's mesh item cannot draw images on a surface, so ``ModelPartItem`` draws one glTF
primitive itself: its vertex colours, base colour and embedded image, lit from above. The
card places the model with one matrix (``set_model_matrix``): the model's scene frame to the
plot's box, so axis stretches and placement changes need no reload. See src/data/model3d.py.
"""
from __future__ import annotations

import logging

import numpy as np
from OpenGL import GL
from OpenGL.GL import shaders
from PySide6.QtGui import QOpenGLContext
from PySide6.QtOpenGL import QOpenGLBuffer
from pyqtgraph.opengl.GLGraphicsItem import GLGraphicsItem

from src.data.model3d import ModelPart

_log = logging.getLogger(__name__)

VERTEX_SHADER = """#version 150
uniform mat4 u_mvp;
uniform mat3 u_normal;
in vec3 a_position;
in vec3 a_normal;
in vec4 a_color;
in vec2 a_uv;
out vec3 v_normal;
out vec4 v_color;
out vec2 v_uv;
void main() {
    v_normal = u_normal * a_normal;
    v_color = a_color;
    v_uv = a_uv;
    gl_Position = u_mvp * vec4(a_position, 1.0);
}
"""

# glTF's base colour: factor x vertex colour x image. Lit from above, both faces.
FRAGMENT_SHADER = """#version 150
uniform sampler2D u_texture;
uniform float u_use_texture;
uniform vec4 u_base;
uniform float u_alpha_cutoff;
uniform float u_opacity;
in vec3 v_normal;
in vec4 v_color;
in vec2 v_uv;
out vec4 fragColor;
void main() {
    vec4 color = u_base * v_color;
    if (u_use_texture > 0.5) color *= texture(u_texture, v_uv);
    if (color.a < u_alpha_cutoff) discard;
    float light = abs(dot(normalize(v_normal), normalize(vec3(0.5, -0.5, 1.0))));
    fragColor = vec4(color.rgb * (0.5 + 0.5 * light), color.a * u_opacity);
}
"""

_POSITION, _NORMAL, _COLOR, _UV = 0, 1, 2, 3


def _program_for_current_context(programs: dict[int, object]):  # type: ignore[no-untyped-def]
    context = QOpenGLContext.currentContext()
    key = id(context)
    if key not in programs:
        try:
            program = shaders.compileProgram(
                shaders.compileShader(VERTEX_SHADER, GL.GL_VERTEX_SHADER),
                shaders.compileShader(FRAGMENT_SHADER, GL.GL_FRAGMENT_SHADER),
                validate=False,
            )
            for location, name in ((_POSITION, "a_position"), (_NORMAL, "a_normal"), (_COLOR, "a_color"),
                                   (_UV, "a_uv")):
                GL.glBindAttribLocation(program, location, name)
            GL.glLinkProgram(program)
        except Exception as error:  # noqa: BLE001 - a driver that cannot compile them draws no model.
            _log.warning("3D models cannot be drawn on this graphics driver: %s", error)
            program = None
        programs[key] = program
        if context is not None:
            context.aboutToBeDestroyed.connect(lambda key=key: programs.pop(key, None))
    return programs[key]


def normal_matrix(matrix: np.ndarray) -> np.ndarray:
    """The 3 x 3 matrix that turns model normals into plot-box normals (inverse transpose)."""
    linear = np.asarray(matrix, dtype=float)[:3, :3]
    try:
        return np.linalg.inv(linear).T
    except np.linalg.LinAlgError:
        return np.eye(3)


class ModelPartItem(GLGraphicsItem):
    """One glTF primitive of a 3D model, with its image if it has one."""

    _programs: dict[int, object] = {}

    def __init__(self, part: ModelPart, image: np.ndarray | None = None) -> None:
        super().__init__()
        self.part = part
        self.image = image if part.image is not None else None
        # See-through in the model itself (water), or made so by the card's Model opacity.
        self._own_translucent = part.alpha_mode == "BLEND" or part.base_color[3] < 0.999
        self.opacity = 1.0
        self.translucent = self._own_translucent
        self.alpha_cutoff = part.alpha_cutoff if part.alpha_mode == "MASK" else 0.03
        self.model_matrix = np.eye(4)
        self._normal_matrix = np.eye(3)
        self._buffers: dict[int, QOpenGLBuffer] = {}
        self._index_buffer: QOpenGLBuffer | None = None
        self._texture = None
        self._uploaded = False
        self._apply_translucency()

    def set_opacity(self, opacity: float) -> None:
        """The part's opacity, 0 to 1; below 1 the traces inside the model show through it."""
        self.opacity = min(1.0, max(0.0, float(opacity)))
        self._apply_translucency()
        self.update()

    def _apply_translucency(self) -> None:
        self.translucent = self._own_translucent or self.opacity < 0.999
        options = {GL.GL_DEPTH_TEST: True, GL.GL_CULL_FACE: False, GL.GL_BLEND: self.translucent,
                   "glBlendFunc": (GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA) if self.translucent else None}
        self.setGLOptions(options)
        # Opaque parts first; see-through ones after the traces, so the traces show through them.
        self.setDepthValue(10 if self.translucent else -10)

    def set_model_matrix(self, matrix: np.ndarray) -> None:
        self.model_matrix = np.asarray(matrix, dtype=float)
        self._normal_matrix = normal_matrix(self.model_matrix)
        self.update()

    def _upload(self) -> None:
        part = self.part
        arrays = {_POSITION: part.positions, _NORMAL: part.normals, _COLOR: part.colors, _UV: part.uv}
        for location, array in arrays.items():
            if array is None:
                continue
            data = np.ascontiguousarray(array, dtype=np.float32)
            buffer = QOpenGLBuffer(QOpenGLBuffer.Type.VertexBuffer)
            buffer.create()
            buffer.bind()
            buffer.allocate(data, data.nbytes)
            buffer.release()
            self._buffers[location] = buffer
        indices = np.ascontiguousarray(part.indices, dtype=np.uint32)
        self._index_buffer = QOpenGLBuffer(QOpenGLBuffer.Type.IndexBuffer)
        self._index_buffer.create()
        self._index_buffer.bind()
        self._index_buffer.allocate(indices, indices.nbytes)
        self._index_buffer.release()
        if self.image is not None:
            image = np.ascontiguousarray(self.image, dtype=np.uint8)
            self._texture = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self._texture)
            GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
            # Rows top first: texture v = 0 is the image's top, as glTF counts it.
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, image.shape[1], image.shape[0], 0,
                            GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, image)
            GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 4)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_LINEAR_MIPMAP_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_LINEAR)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
            GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T, GL.GL_REPEAT)
            GL.glGenerateMipmap(GL.GL_TEXTURE_2D)
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        self._uploaded = True

    def release(self) -> None:
        """Free the GPU buffers; the card makes its view's context current first."""
        for buffer in self._buffers.values():
            buffer.destroy()
        self._buffers.clear()
        if self._index_buffer is not None:
            self._index_buffer.destroy()
            self._index_buffer = None
        if self._texture is not None:
            GL.glDeleteTextures([self._texture])
            self._texture = None
        self._uploaded = False

    def paint(self) -> None:  # noqa: D102 - pyqtgraph's
        if not len(self.part.indices):
            return
        program = _program_for_current_context(self._programs)
        if program is None:
            return
        self.setupGLState()
        if not self._uploaded:
            self._upload()
        view_projection = np.array(self.mvpMatrix().data(), dtype=float).reshape(4, 4).T
        mvp = np.ascontiguousarray((view_projection @ self.model_matrix).T, dtype=np.float32)
        normal = np.ascontiguousarray(self._normal_matrix.T, dtype=np.float32)
        enabled = []
        for location, size in ((_POSITION, 3), (_NORMAL, 3), (_COLOR, 4), (_UV, 2)):
            buffer = self._buffers.get(location)
            if buffer is None:
                continue
            buffer.bind()
            GL.glVertexAttribPointer(location, size, GL.GL_FLOAT, False, 0, None)
            buffer.release()
            GL.glEnableVertexAttribArray(location)
            enabled.append(location)
        if _COLOR not in self._buffers:
            GL.glVertexAttrib4f(_COLOR, 1.0, 1.0, 1.0, 1.0)
        if _UV not in self._buffers:
            GL.glVertexAttrib2f(_UV, 0.0, 0.0)
        if self.translucent:
            GL.glDepthMask(False)
        try:
            with program:
                GL.glUniformMatrix4fv(GL.glGetUniformLocation(program, "u_mvp"), 1, False, mvp)
                GL.glUniformMatrix3fv(GL.glGetUniformLocation(program, "u_normal"), 1, False, normal)
                GL.glUniform4f(GL.glGetUniformLocation(program, "u_base"), *self.part.base_color)
                GL.glUniform1f(GL.glGetUniformLocation(program, "u_alpha_cutoff"), float(self.alpha_cutoff))
                GL.glUniform1f(GL.glGetUniformLocation(program, "u_opacity"), float(self.opacity))
                GL.glUniform1f(GL.glGetUniformLocation(program, "u_use_texture"), float(self._texture is not None))
                GL.glActiveTexture(GL.GL_TEXTURE0)
                GL.glBindTexture(GL.GL_TEXTURE_2D, self._texture or 0)
                GL.glUniform1i(GL.glGetUniformLocation(program, "u_texture"), 0)
                self._index_buffer.bind()
                GL.glDrawElements(GL.GL_TRIANGLES, int(self.part.indices.size), GL.GL_UNSIGNED_INT, None)
                self._index_buffer.release()
                GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        finally:
            for location in enabled:
                GL.glDisableVertexAttribArray(location)
            if self.translucent:
                GL.glDepthMask(True)
