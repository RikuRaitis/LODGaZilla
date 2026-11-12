"""Windows GPU preview: D3D11 video processing -> WGL shared RGB texture.

Only public D3D11 COM and WGL_NV_DX_interop2 APIs are used, via ctypes. PyAV
owns the decoded AVFrame; its documented plane buffer pointers expose the
D3D11 texture and array slice. No CPython/PyAV object-layout introspection.
All methods run with the owning OpenGL context current on the GUI thread.
"""
import ctypes as C
from functools import lru_cache
import uuid

from OpenGL import GL as gl

PTR = C.c_void_p
UINT = C.c_uint32
INT = C.c_int32


def com(pointer, slot, result, types, *args):
    """Call a method at its Windows SDK COM vtable slot."""
    table = C.cast(pointer, C.POINTER(C.POINTER(PTR))).contents
    return C.WINFUNCTYPE(result, PTR, *types)(table[slot])(pointer, *args)


def check(hr):
    if hr < 0:
        raise RuntimeError(f'D3D11 failed (HRESULT 0x{hr & 0xffffffff:08x})')


def query(pointer, iid):
    guid = (C.c_ubyte * 16).from_buffer_copy(uuid.UUID(iid).bytes_le)
    out = PTR()
    check(com(pointer, 0, INT, [PTR, PTR], C.byref(guid), C.byref(out)))
    return out


def release(pointer):
    if pointer:
        com(pointer, 2, UINT, [])


class TextureDesc(C.Structure):
    _fields_ = [(name, UINT) for name in (
        'width', 'height', 'mips', 'array_size', 'format', 'sample_count',
        'sample_quality', 'usage', 'bind', 'cpu_access', 'misc')]


VERTEX = b'''
struct V { float4 position : SV_Position; float2 uv : TEXCOORD0; };
V main(uint id : SV_VertexID) {
    V o; float2 p = float2((id << 1) & 2, id & 2);
    o.position = float4(p.x*2-1, 1-p.y*2, 0, 1); o.uv = p; return o;
}
'''
PIXEL = b'''
Texture2D<float> Y : register(t0);
Texture2D<float2> UV : register(t1);
SamplerState linearSampler : register(s0);
cbuffer Color : register(b0) { float4 levels; float4 coefficients; };
float4 main(float4 position : SV_Position, float2 uv : TEXCOORD0) : SV_Target {
    float y = (Y.Sample(linearSampler,uv) - levels.x) * levels.y;
    float2 c = (UV.Sample(linearSampler,uv) - levels.z) * levels.w;
    return float4(saturate(float3(y+coefficients.x*c.y,
           y+coefficients.y*c.x+coefficients.z*c.y,y+coefficients.w*c.x)),1);
}
'''


@lru_cache(maxsize=2)
def compile_shader(source, target):
    compiler = C.WinDLL('d3dcompiler_47.dll').D3DCompile
    compiler.restype = INT
    compiler.argtypes = [PTR, C.c_size_t, C.c_char_p, PTR, PTR, C.c_char_p,
                         C.c_char_p, UINT, UINT, PTR, PTR]
    code, errors = PTR(), PTR()
    hr = compiler(source, len(source), b'insv_preview', None, None, b'main', target,
                  1 << 15, 0, C.byref(code), C.byref(errors))
    try:
        if hr < 0:
            message = C.string_at(com(errors, 3, PTR, [])).decode(errors='replace') if errors else ''
            raise RuntimeError('D3D11 shader compilation failed: ' + message)
        return C.string_at(com(code, 3, PTR, []), com(code, 4, C.c_size_t, []))
    finally:
        release(code)
        release(errors)


class SharedLens:
    """One reusable GPU-scaled BGRA surface shared with an OpenGL texture."""
    def __init__(self, qt_context, frame, max_side):
        self.resources = []
        self.handle = self.registration = None
        self.texture = None
        self.locked = False
        self.source_size = (frame.width, frame.height)
        self.max_side = max_side
        scale = min(1.0, max_side / max(self.source_size))
        self.size = tuple(max(2, int(v * scale) // 2 * 2) for v in self.source_size)
        self.context = qt_context
        try:
            self._create(frame)
        except Exception:
            self.close()
            raise

    def _own(self, pointer):
        self.resources.append(pointer)
        return pointer

    def _wgl(self, name, result, *types):
        address = self.context.getProcAddress(name.encode('ascii'))
        if not address:
            raise RuntimeError('D3D11/OpenGL sharing unavailable: ' + name)
        return C.WINFUNCTYPE(result, *types)(address)

    def _create(self, frame):
        self.open_device = self._wgl('wglDXOpenDeviceNV', PTR, PTR)
        self.close_device = self._wgl('wglDXCloseDeviceNV', INT, PTR)
        self.register = self._wgl('wglDXRegisterObjectNV', PTR, PTR, PTR, UINT, UINT, UINT)
        self.unregister = self._wgl('wglDXUnregisterObjectNV', INT, PTR, PTR)
        self.lock_objects = self._wgl('wglDXLockObjectsNV', INT, PTR, INT, PTR)
        self.unlock_objects = self._wgl('wglDXUnlockObjectsNV', INT, PTR, INT, PTR)
        device = PTR()
        com(frame.planes[0].buffer_ptr, 3, None, [PTR], C.byref(device))  # GetDevice
        self.device = self._own(device)
        context = PTR()
        com(device, 40, None, [PTR], C.byref(context))  # GetImmediateContext
        self.immediate = self._own(context)
        # The decoder and renderer submit on different threads. Protect D3D's
        # shared immediate context, including calls issued inside FFmpeg.
        self.multi = self._own(query(context, '9b7e4e00-342c-4106-a19f-4f2704f689f0'))
        com(self.multi, 5, INT, [INT], 1)  # SetMultithreadProtected(TRUE)
        self.handle = self.open_device(device)
        if not self.handle:
            raise RuntimeError('D3D11/OpenGL device sharing rejected by driver')
        # BGRA8, DEFAULT usage, RENDER_TARGET | SHADER_RESOURCE.
        tex_desc = TextureDesc(*self.size, 1, 1, 87, 1, 0, 0, 0x28, 0, 0)
        texture = PTR()
        check(com(device, 5, INT, [PTR, PTR, PTR], C.byref(tex_desc), None, C.byref(texture)))
        self.output = self._own(texture)
        output_view = PTR()
        check(com(device, 9, INT, [PTR, PTR, PTR], texture, None, C.byref(output_view)))
        self.output_view = self._own(output_view)
        self.texture = int(gl.glGenTextures(1))
        self.registration = self.register(self.handle, texture, self.texture, gl.GL_TEXTURE_2D, 0)
        if not self.registration:
            raise RuntimeError('D3D11/OpenGL texture registration rejected by driver')
        self.lock()
        gl.glBindTexture(gl.GL_TEXTURE_2D, self.texture)
        for parameter in (gl.GL_TEXTURE_MIN_FILTER, gl.GL_TEXTURE_MAG_FILTER):
            gl.glTexParameteri(gl.GL_TEXTURE_2D, parameter, gl.GL_LINEAR)
        for parameter in (gl.GL_TEXTURE_WRAP_S, gl.GL_TEXTURE_WRAP_T):
            gl.glTexParameteri(gl.GL_TEXTURE_2D, parameter, gl.GL_CLAMP_TO_EDGE)
        self.unlock()
        self._create_shaders(frame)

    def _create_shaders(self, frame):
        # Decoder surfaces need not have SHADER_RESOURCE binding. Copy a single
        # slice GPU-to-GPU into a sampleable planar texture, then shade only the
        # small output. This is much faster than Intel's full-frame video processor.
        ten_bit = frame.sw_format.name == 'p010le'
        if frame.sw_format.name not in ('p010le', 'nv12'):
            raise RuntimeError('D3D11 texture format unsupported: ' + frame.sw_format.name)
        desc = TextureDesc(*self.source_size, 1, 1, 104 if ten_bit else 103, 1, 0, 0, 8, 0, 0)
        copied = PTR()
        check(com(self.device, 5, INT, [PTR, PTR, PTR], C.byref(desc), None, C.byref(copied)))
        self.copied = self._own(copied)
        self.views = []
        for fmt in ((56, 35) if ten_bit else (61, 49)):
            srv_desc = (UINT * 6)(fmt, 4, 0, 1, 0, 0)  # TEXTURE2D, mip 0, one level
            view = PTR()
            check(com(self.device, 7, INT, [PTR, PTR, PTR], copied, srv_desc, C.byref(view)))
            self.views.append(self._own(view))
        shaders = []
        for source, target, slot in ((VERTEX, b'vs_4_0', 12), (PIXEL, b'ps_4_0', 15)):
            code = compile_shader(source, target)
            shader = PTR()
            check(com(self.device, slot, INT, [PTR, C.c_size_t, PTR, PTR],
                      C.c_char_p(code), len(code), None, C.byref(shader)))
            shaders.append(self._own(shader))
        self.vertex_shader, self.pixel_shader = shaders
        # D3D11_SAMPLER_DESC: linear min/mag/mip, CLAMP, no comparison.
        class Sampler(C.Structure):
            _fields_ = [('filter', UINT), ('u', UINT), ('v', UINT), ('w', UINT),
                        ('bias', C.c_float), ('anisotropy', UINT), ('comparison', UINT),
                        ('border', C.c_float * 4), ('min_lod', C.c_float), ('max_lod', C.c_float)]
        desc = Sampler(0x15, 3, 3, 3, 0, 1, 1, (C.c_float * 4)(), 0, 3.402823e38)
        sampler = PTR()
        check(com(self.device, 23, INT, [PTR, PTR], C.byref(desc), C.byref(sampler)))
        self.sampler = self._own(sampler)
        # DEFAULT 32-byte constant buffer, updated for each frame's color tags.
        buffer_desc = (UINT * 6)(32, 0, 4, 0, 0, 0)
        buffer = PTR()
        check(com(self.device, 3, INT, [PTR, PTR, PTR], buffer_desc, None, C.byref(buffer)))
        self.constants = self._own(buffer)
        self.ten_bit = ten_bit

    def process(self, frame):
        planes = frame.planes
        source = planes[0].buffer_ptr
        slice_index = planes[1].buffer_ptr if len(planes) > 1 else 0
        full = int(frame.color_range) == 2
        kr, kb = ((0.2627, 0.0593) if int(frame.colorspace) == 9 else
                  (0.299, 0.114) if int(frame.colorspace) in (5, 6) else (0.2126, 0.0722))
        kg = 1 - kr - kb
        if self.ten_bit:
            unit = 64 / 65535
            levels = (0 if full else 64*unit, 1/((1023 if full else 876)*unit),
                      512*unit, 1/((1023 if full else 896)*unit))
        else:
            levels = (0 if full else 16/255, 1 if full else 255/219,
                      128/255, 1 if full else 255/224)
        constants = (C.c_float * 8)(*levels, 2*(1-kr), -2*kb*(1-kb)/kg,
                                   -2*kr*(1-kr)/kg, 2*(1-kb))
        ctx = self.immediate
        com(self.multi, 3, None, [])  # atomic context state setup + draw
        try:
            box = (UINT * 6)(0, 0, 0, *self.source_size, 1)
            com(ctx, 46, None, [PTR, UINT, UINT, UINT, UINT, PTR, UINT, PTR],
                self.copied, 0, 0, 0, 0, source, slice_index, box)
            com(ctx, 48, None, [PTR, UINT, PTR, PTR, UINT, UINT], self.constants, 0, None, constants, 0, 0)
            com(ctx, 33, None, [UINT, PTR, PTR], 1, C.byref(self.output_view), None)
            viewport = (C.c_float * 6)(0, 0, *self.size, 0, 1)
            com(ctx, 44, None, [UINT, PTR], 1, viewport)
            com(ctx, 17, None, [PTR], None)
            com(ctx, 24, None, [UINT], 4)
            com(ctx, 11, None, [PTR, PTR, UINT], self.vertex_shader, None, 0)
            com(ctx, 9, None, [PTR, PTR, UINT], self.pixel_shader, None, 0)
            com(ctx, 8, None, [UINT, UINT, PTR], 0, 2, (PTR * 2)(*(v.value for v in self.views)))
            com(ctx, 10, None, [UINT, UINT, PTR], 0, 1, C.byref(self.sampler))
            com(ctx, 16, None, [UINT, UINT, PTR], 0, 1, C.byref(self.constants))
            com(ctx, 13, None, [UINT, UINT], 3, 0)
            # Unbind resources before the next GPU copy or WGL texture lock.
            com(ctx, 8, None, [UINT, UINT, PTR], 0, 2, (PTR * 2)())
            com(ctx, 33, None, [UINT, PTR, PTR], 0, None, None)
        finally:
            com(self.multi, 4, None, [])

    def lock(self):
        objects = (PTR * 1)(self.registration)
        if not self.lock_objects(self.handle, 1, objects):
            raise RuntimeError('D3D11/OpenGL texture lock failed')
        self.locked = True

    def unlock(self):
        if self.locked:
            objects = (PTR * 1)(self.registration)
            if not self.unlock_objects(self.handle, 1, objects):
                raise RuntimeError('D3D11/OpenGL texture unlock failed')
            self.locked = False

    def close(self):
        self.unlock()
        if self.registration:
            self.unregister(self.handle, self.registration)
            self.registration = None
        if self.texture:
            gl.glDeleteTextures([self.texture])
            self.texture = None
        if self.handle:
            self.close_device(self.handle)
            self.handle = None
        for resource in reversed(self.resources):
            release(resource)
        self.resources.clear()
