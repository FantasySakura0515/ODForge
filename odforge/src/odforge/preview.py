"""Page-to-PNG preview rendering.

``render_pages`` rasterises an ODF file to one PNG per page. It reuses
``validate.run_soffice_convert`` (isolated LibreOffice profile) to obtain a
PDF, then rasterises each PDF page with PyMuPDF (``fitz``).

Shared by the QA harness (Phase 16) and the future web API. When LibreOffice
is not installed, :class:`PreviewUnavailable` is raised so the caller can
decide how to degrade rather than the pipeline crashing opaquely.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

# `pymupdf`, never the legacy `fitz` alias: importing `fitz` makes PyMuPDF print
# a deprecation notice to **stdout**. For a stdio MCP server that is not cosmetic
# — every byte on stdout is protocol, so one stray line corrupts the stream and
# the host sees a broken server. (It also pollutes any CLI output piped to a
# file.) The alias is kept so the rest of the module reads unchanged.
import pymupdf as fitz

from .validate import find_soffice, run_soffice_convert

_SOFFICE_TIMEOUT = 120


class PreviewUnavailable(Exception):
    """Raised when preview rendering cannot proceed (soffice not found)."""


def render_pages(odf_path: Path, out_dir: Path, dpi: int = 150) -> list[Path]:
    """Rasterise ``odf_path`` to one PNG per page in ``out_dir``.

    Converts the ODF to PDF through an isolated-profile LibreOffice headless
    run, then renders each PDF page to a zero-padded, 1-based PNG
    (``page-01.png``, ``page-02.png``, ...) at ``dpi``. Returns the PNG paths
    in page order.

    Raises :class:`PreviewUnavailable` when LibreOffice is not found, and a
    :class:`RuntimeError` when the conversion itself fails.
    """
    odf_path = Path(odf_path)
    out_dir = Path(out_dir)

    soffice = find_soffice()
    if soffice is None:
        raise PreviewUnavailable(
            "LibreOffice (soffice) not found; cannot render a preview. "
            "Install LibreOffice or check it is on PATH."
        )

    out_dir.mkdir(parents=True, exist_ok=True)

    pdf_dir = Path(tempfile.mkdtemp(prefix="odforge-preview-"))
    try:
        try:
            proc = run_soffice_convert(
                soffice, odf_path, "pdf", pdf_dir, timeout=_SOFFICE_TIMEOUT
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"soffice timed out after {_SOFFICE_TIMEOUT}s converting "
                f"{odf_path.name} to pdf"
            ) from exc

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(
                f"soffice exited {proc.returncode} converting {odf_path.name} "
                f"to pdf: {detail[:500]}"
            )

        pdf_path = pdf_dir / (odf_path.stem + ".pdf")
        if not pdf_path.exists():
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(
                f"soffice produced no pdf for {odf_path.name}: {detail[:500]}"
            )

        pngs: list[Path] = []
        with fitz.open(pdf_path) as doc:
            for index, page in enumerate(doc, start=1):
                pix = page.get_pixmap(dpi=dpi)
                png_path = out_dir / f"page-{index:02d}.png"
                pix.save(str(png_path))
                pngs.append(png_path)
        return pngs
    finally:
        shutil.rmtree(pdf_dir, ignore_errors=True)
