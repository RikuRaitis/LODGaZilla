"""Middle-elided text: shared by the data folder field and card Legends."""

from __future__ import annotations


PATH_END_WIDTH_FRACTION = 0.7
PATH_ELLIPSIS = "..."


def middle_elide_text(
    text: str,
    font_metrics,
    available_width: int,
    *,
    end_width_fraction: float = PATH_END_WIDTH_FRACTION,
    ellipsis: str = PATH_ELLIPSIS,
) -> str:
    """Return text with a middle ellipsis, reserving most space for the end."""

    if not text or available_width <= 0:
        return ""
    if font_metrics.horizontalAdvance(text) <= available_width:
        return text

    ellipsis_width = font_metrics.horizontalAdvance(ellipsis)
    if ellipsis_width >= available_width:
        return _fit_prefix(ellipsis, font_metrics, available_width)

    content_width = max(0, available_width - ellipsis_width)
    end_width_fraction = max(0.0, min(1.0, end_width_fraction))
    start_width = round(content_width * (1.0 - end_width_fraction))
    end_width = content_width - start_width

    prefix = _fit_prefix(text, font_metrics, start_width)
    suffix_source = text[len(prefix):]
    suffix = _fit_suffix(suffix_source, font_metrics, end_width)
    if not prefix and not suffix:
        suffix = _fit_suffix(text, font_metrics, content_width)
    return f"{prefix}{ellipsis}{suffix}"


def _fit_prefix(text: str, font_metrics, maximum_width: int) -> str:
    if maximum_width <= 0:
        return ""
    low = 0
    high = len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if font_metrics.horizontalAdvance(text[:middle]) <= maximum_width:
            low = middle
        else:
            high = middle - 1
    return text[:low]


def _fit_suffix(text: str, font_metrics, maximum_width: int) -> str:
    if maximum_width <= 0:
        return ""
    low = 0
    high = len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if font_metrics.horizontalAdvance(text[len(text) - middle:]) <= maximum_width:
            low = middle
        else:
            high = middle - 1
    return text[len(text) - low:]
