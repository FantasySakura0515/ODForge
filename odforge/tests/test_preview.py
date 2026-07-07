"""Tests for the page-to-PNG preview pipeline (Task 16.1).

The happy-path test is soffice-gated (it round-trips an .odp through
LibreOffice → PDF → PyMuPDF PNGs). The unavailable-path test monkeypatches
``find_soffice`` and needs no LibreOffice at all.
"""

import fitz  # PyMuPDF
import pytest

from odforge import preview
from odforge.preview import PreviewUnavailable, render_pages
from odforge.render.odp import render_odp
from odforge.validate import find_soffice


@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
def test_render_pages_one_png_per_slide(tmp_path, sample_presentation):
    odp = render_odp(sample_presentation, tmp_path / "p.odp")
    out_dir = tmp_path / "preview"

    pngs = render_pages(odp, out_dir, dpi=96)

    # one PNG per slide, in page order, deterministic zero-padded names
    assert len(pngs) == len(sample_presentation.slides)
    assert [p.name for p in pngs] == [
        f"page-{i:02d}.png" for i in range(1, len(sample_presentation.slides) + 1)
    ]
    for p in pngs:
        assert p.exists(), p
        assert p.stat().st_size > 0, p
        assert p.parent == out_dir

    # the ODP page is 28cm x 15.75cm == 16:9; every rendered PNG matches.
    for p in pngs:
        pix = fitz.Pixmap(str(p))
        assert pix.width > 0 and pix.height > 0
        assert abs(pix.width / pix.height - 16 / 9) < 0.02, (
            p,
            pix.width,
            pix.height,
        )

    # the intermediate PDF is not left behind in out_dir
    assert not list(out_dir.glob("*.pdf"))


def test_preview_unavailable_without_soffice(tmp_path, monkeypatch, sample_presentation):
    # No LibreOffice on PATH → render_pages must raise PreviewUnavailable.
    monkeypatch.setattr(preview, "find_soffice", lambda: None)
    odp = render_odp(sample_presentation, tmp_path / "p.odp")
    with pytest.raises(PreviewUnavailable):
        render_pages(odp, tmp_path / "preview")
