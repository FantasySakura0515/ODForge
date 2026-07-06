"""Renderer dispatch: Document IR -> native ODF file.

``render()`` looks up the concrete renderer by the IR's ``type`` discriminator.
Later tasks register additional renderers (``.odp``, ``.ods``) by extending
``DISPATCH``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from odforge.render.odt import render_odt

# ir.type -> renderer. Extend this table to add new document types.
DISPATCH: dict[str, Callable[..., Path]] = {
    "text": render_odt,
}


def render(ir, out_path: Path) -> Path:
    """Render ``ir`` to ``out_path`` using the renderer for its ``type``.

    Raises ``ValueError`` (with the offending type in the message) when no
    renderer is registered for ``ir.type``.
    """
    ir_type = getattr(ir, "type", None)
    renderer = DISPATCH.get(ir_type)
    if renderer is None:
        raise ValueError(f"No renderer registered for document type: {ir_type!r}")
    return renderer(ir, Path(out_path))


__all__ = ["render", "render_odt", "DISPATCH"]
