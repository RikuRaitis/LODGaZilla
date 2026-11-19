"""3D lines of any width in pixels, for the 3D plot's **Use shader** option.

OpenGL's core profile, which LODGaZilla uses, draws every line 1 px wide whatever width is
asked. ``WideLinePlotItem`` draws each segment as a band instead: a geometry shader turns the
segment's two end points into a rectangle ``width`` pixels wide on screen, and the fragment
shader softens its edges. The GPU does the widening, so the camera can move freely at no
extra cost. When the shaders cannot be compiled (an old or virtual graphics driver), the
item draws a plain line, as pyqtgraph's ``GLLinePlotItem`` does.
"""
from __future__ import annotations

import logging

import numpy as np
from OpenGL import GL
from OpenGL.GL import shaders
from PySide6.QtGui import QOpenGLContext
from pyqtgraph.opengl import GLLinePlotItem

_log = logging.getLogger(__name__)

VERTEX_SHADER = """#version 150
uniform mat4 u_mvp;
in vec4 a_position;
in vec4 a_color;
out vec4 v_color;
void main() {
    v_color = a_color;
    gl_Position = u_mvp * a_position;
}
"""

# Each segment (two vertices, drawn as GL_LINES) becomes a band: offset sideways by half the
# width plus one pixel for the soft edge, and lengthened by half the width at both ends so
# consecutive segments overlap at their joins.
GEOMETRY_SHADER = """#version 150
layout(lines) in;
layout(triangle_strip, max_vertices = 4) out;
uniform vec2 u_viewport;
uniform float u_width;
in vec4 v_color[];
out vec4 g_color;
out float g_edge;
// The band sits a hair behind its trace, so points and markers on the trace stay in front of it.
// A few steps of the depth buffer: pyqtgraph's near plane is a millionth of its far one, so the
// depth scale is very uneven and a larger bias (0.0005 was a quarter of the camera distance) hid
// the band behind a 3D model's surfaces that the trace runs inside, while its points showed.
const float DEPTH_BIAS = 0.000001;
void emit_corner(vec4 position, vec2 offset, float edge, vec4 color) {
    gl_Position = vec4(position.xy + offset * position.w, position.z + DEPTH_BIAS * position.w, position.w);
    g_edge = edge;
    g_color = color;
    EmitVertex();
}
void main() {
    vec4 p0 = gl_in[0].gl_Position;
    vec4 p1 = gl_in[1].gl_Position;
    if (p0.w <= 0.0 || p1.w <= 0.0) return;  // Behind the camera.
    vec2 s0 = p0.xy / p0.w * u_viewport * 0.5;
    vec2 s1 = p1.xy / p1.w * u_viewport * 0.5;
    vec2 direction = s1 - s0;
    float len = length(direction);
    direction = len > 0.0 ? direction / len : vec2(1.0, 0.0);
    vec2 normal = vec2(-direction.y, direction.x);
    vec2 to_ndc = 2.0 / u_viewport;
    vec2 across = normal * (u_width * 0.5 + 1.0) * to_ndc;
    vec2 along = direction * (u_width * 0.5) * to_ndc;
    emit_corner(p0, -along + across, 1.0, v_color[0]);
    emit_corner(p0, -along - across, -1.0, v_color[0]);
    emit_corner(p1, along + across, 1.0, v_color[1]);
    emit_corner(p1, along - across, -1.0, v_color[1]);
    EndPrimitive();
}
"""

FRAGMENT_SHADER = """#version 150
uniform float u_width;
in vec4 g_color;
in float g_edge;
out vec4 fragColor;
void main() {
    float half_width = u_width * 0.5;
    float distance = abs(g_edge) * (half_width + 1.0);
    float coverage = clamp(half_width + 0.5 - distance, 0.0, 1.0);
    if (coverage <= 0.0) discard;
    fragColor = vec4(g_color.rgb, g_color.a * coverage);
}
"""


class WideLinePlotItem(GLLinePlotItem):
    """A GLLinePlotItem in 'lines' mode drawn ``width`` pixels wide by shaders."""

    # One program per OpenGL context (each 3D plot card has its own); None when it failed.
    _programs: dict[int, object] = {}

    @classmethod
    def program_for_current_context(cls):  # type: ignore[no-untyped-def]
        context = QOpenGLContext.currentContext()
        key = id(context)
        if key not in cls._programs:
            try:
                program = shaders.compileProgram(
                    shaders.compileShader(VERTEX_SHADER, GL.GL_VERTEX_SHADER),
                    shaders.compileShader(GEOMETRY_SHADER, GL.GL_GEOMETRY_SHADER),
                    shaders.compileShader(FRAGMENT_SHADER, GL.GL_FRAGMENT_SHADER),
                    validate=False,
                )
                GL.glBindAttribLocation(program, 0, "a_position")
                GL.glBindAttribLocation(program, 1, "a_color")
                GL.glLinkProgram(program)
            except Exception as error:  # noqa: BLE001 - any driver failure falls back to 1 px lines.
                _log.warning("Wide 3D lines are not available; drawing 1 px lines: %s", error)
                program = None
            cls._programs[key] = program
            # A context's programs go with it; a later context may reuse its address.
            context.aboutToBeDestroyed.connect(lambda key=key: cls._programs.pop(key, None))
        return cls._programs[key]

    def paint(self) -> None:  # noqa: D102 - pyqtgraph's
        if self.pos is None or not len(self.pos):
            return
        program = self.program_for_current_context()
        if program is None or self.mode != "lines":
            super().paint()
            return
        self.setupGLState()
        from pyqtgraph.opengl.items.GLLinePlotItem import DirtyFlag
        if DirtyFlag.POSITION in self.dirty_bits:
            self.upload_vbo(self.m_vbo_position, self.pos)
        if DirtyFlag.COLOR in self.dirty_bits:
            self.upload_vbo(self.m_vbo_color, self.color)
        self.dirty_bits = DirtyFlag(0)

        mvp = np.array(self.mvpMatrix().data(), dtype=np.float32)
        viewport = GL.glGetIntegerv(GL.GL_VIEWPORT)
        view = self.view()
        ratio = view.devicePixelRatioF() if view is not None else 1.0

        self.m_vbo_position.bind()
        GL.glVertexAttribPointer(0, 3, GL.GL_FLOAT, False, 0, None)
        self.m_vbo_position.release()
        enabled = [0]
        if isinstance(self.color, np.ndarray):
            self.m_vbo_color.bind()
            GL.glVertexAttribPointer(1, 4, GL.GL_FLOAT, False, 0, None)
            self.m_vbo_color.release()
            enabled.append(1)
        else:
            GL.glVertexAttrib4f(1, *self.color)
        for location in enabled:
            GL.glEnableVertexAttribArray(location)
        # The soft edges blend with what is behind them.
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFuncSeparate(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA, GL.GL_ONE, GL.GL_ONE_MINUS_SRC_ALPHA)
        try:
            with program:
                GL.glUniformMatrix4fv(GL.glGetUniformLocation(program, "u_mvp"), 1, False, mvp)
                GL.glUniform2f(GL.glGetUniformLocation(program, "u_viewport"), float(viewport[2]), float(viewport[3]))
                GL.glUniform1f(GL.glGetUniformLocation(program, "u_width"), float(self.width) * float(ratio))
                GL.glDrawArrays(GL.GL_LINES, 0, len(self.pos))
        finally:
            for location in enabled:
                GL.glDisableVertexAttribArray(location)
            GL.glDisable(GL.GL_BLEND)
