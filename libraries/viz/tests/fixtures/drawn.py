"""What the renderer tests measure on a drawn matplotlib figure."""

import math
from typing import Any


def label_clashes(drawn: Any) -> int:
    """Neighbouring x tick labels of a matplotlib figure that overlap: level
    labels by their extents; slanted ones, which are parallel, when their
    ticks stand less than a line's height apart across the slant."""
    from matplotlib.textpath import TextToPath

    drawn.draw_without_rendering()
    measure = TextToPath()
    clashes = 0
    for ax in drawn.axes:
        labels = [t for t in ax.get_xticklabels() if t.get_visible() and t.get_text()]
        for a, b in zip(labels, labels[1:]):
            angle = a.get_rotation()
            if angle == 0:
                clashes += a.get_window_extent().x1 > b.get_window_extent().x0
                continue
            ticks = ax.transData.transform(
                [(a.get_position()[0], 0), (b.get_position()[0], 0)]
            )
            across = (ticks[1][0] - ticks[0][0]) * math.sin(math.radians(angle))
            _, line, _ = measure.get_text_width_height_descent(
                a.get_text(), a.get_fontproperties(), ismath=False
            )
            # Points, as pixels of the figure
            clashes += across < line * drawn.dpi / 72
    return clashes
