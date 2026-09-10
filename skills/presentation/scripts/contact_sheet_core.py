"""Deterministic layout calculations for presentation contact sheets."""

from __future__ import annotations

from collections.abc import Sequence


def compute_grid_layout(
    images: Sequence[tuple[str, str, int, int]], columns: int = 4
) -> dict[str, object]:
    """Return grid dimensions and placements in the supplied manifest order."""
    if not images:
        raise ValueError("at least one image is required")
    cell_w = max(width for _, _, width, _ in images)
    cell_h = max(height for _, _, _, height in images) + 28
    rows = (len(images) + columns - 1) // columns
    placements = []
    for index, (slide_id, file_name, width, height) in enumerate(images):
        x, y = (index % columns) * cell_w, (index // columns) * cell_h
        placements.append(
            {
                "file": file_name,
                "slide_id": slide_id,
                "x": x,
                "y": y,
                "width": width,
                "height": height,
            }
        )
    return {
        "columns": columns,
        "placements": placements,
        "width": cell_w * columns,
        "height": cell_h * rows,
    }
