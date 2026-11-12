# VR180 Silver Bullet

The INSV trailer directory/protobuf parsing, factory lens model and crop mapping,
track ordering, and measured X6 fallback constants are adapted from:

- https://github.com/silverqsy/VR180-Silver-Bullet/blob/main/crates/vr180-fisheye/src/insta360.rs
- https://github.com/silverqsy/VR180-Silver-Bullet/blob/main/crates/vr180-pipeline/src/insv_imu.rs

Sources inspected 2026-09-18. Python adaptation is limited to identity/calibration;
the IMU/stabilization pipeline and the VR180 renderer are not included.

MIT License

Copyright (c) 2026 Silver

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
