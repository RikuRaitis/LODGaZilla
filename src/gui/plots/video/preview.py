"""Preview sizing in physical pixels, shared by the GUI and renderer."""
import math


def render_size(width, height, pixel_ratio=1.0):
    width = max(1, round(width * pixel_ratio))
    height = max(1, round(height * pixel_ratio))
    scale = min(1.0, 1000 / width, 600 / height)
    return max(1, int(width * scale)), max(1, int(height * scale))


def preview_size(width, height, pixel_ratio, projection, source_size,
                 source_aspect=1.0, rotation=0.0, fov=90.0):
    width, height = render_size(width, height, pixel_ratio)
    if projection == 'flat':
        angle = math.radians(rotation)
        c, s = abs(math.cos(angle)), abs(math.sin(angle))
        # Fit the rotated display rectangle, including non-square source pixels.
        displayed_width = c * source_aspect + s
        displayed_height = s * source_aspect + c
        scale = min(width / displayed_width, height / displayed_height)
        requested = scale * max(source_aspect, 1.0)
    else:
        # Approximate a 180-degree lens image's angular sampling density.
        # Zoom shows less of each lens, so it needs a larger source texture.
        vertical_fov = math.degrees(2 * math.atan(
            math.tan(math.radians(fov / 2)) * height / width))
        requested = max(width * 180 / fov, height * 180 / vertical_fov)
    source_limit = max(2, min(8192, max(source_size)) // 2 * 2)
    return min(source_limit, max(128, math.ceil(requested / 2) * 2))
