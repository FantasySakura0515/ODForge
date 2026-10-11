"""Tests for ``odforge.layoutcheck``: the rendered 跑版 check.

The matching rules are pinned on synthetic geometry (no LibreOffice): which
frame a rendered line belongs to, and when it counts as outside. The end-to-end
cases render real files through LibreOffice and skip themselves without it.
"""

import shutil
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from odforge import layoutcheck as lc
from odforge.check import check_odf
from odforge.layoutcheck import LayoutFinding, check_layout
from odforge.render.odp import render_odp
from odforge.render.odt import render_odt
from odforge.validate import find_soffice, run_soffice_convert

needs_soffice = pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")


def _frame(text, x, y, w, h):
    return lc._Frame(lc._Box(x, y, w, h), lc._key(text), lc._excerpt(text))


def _line(text, x, y, w, h=0.6):
    return lc._Line(lc._Box(x, y, w, h), text, lc._key(text))


# -- which frame a line came from ---------------------------------------------


def test_wrapped_tail_stays_with_the_frame_it_spilled_from():
    # "17 輸出順序" wraps onto three lines; the last two sit nearer the frame
    # below. Nearest-frame matching handed them to "4 輸出順序".
    frames = [_frame("17 輸出順序", 20, 5, 3, 0.6), _frame("4 輸出順序", 10, 8.2, 13, 0.6)]
    lines = [
        _line("17", 20, 5, 1),
        _line("輸出", 20, 6.2, 1.5),
        _line("順序", 20, 7.4, 1.5),
        _line("4 輸出順序", 10, 8.2, 4),
    ]
    assert lc._assign(frames, lines) == [0, 0, 0, 1]


def test_a_line_outside_every_column_is_left_unmatched():
    # The page number "2" is a substring of the agenda's "02", but sits to the
    # right of it. It came from the master page, not the agenda.
    frames = [_frame("01 樹的基本概念 02 二元樹", 3, 4.85, 22, 9.45)]
    assert lc._assign(frames, [_line("2", 26.25, 14.76, 0.25)]) == [None]


# -- when a line counts as outside ----------------------------------------------


def test_a_label_grazing_its_tight_frame_is_not_overflow():
    # A one-line badge in a frame sized to its glyphs: the line box pokes out by
    # a few millimetres, but its centre is inside.
    frame = lc._Box(1.93, 5.97, 0.86, 0.48)
    line = lc._Box(2.23, 6.09, 0.27, 0.67)
    assert lc._frame_overshoot(line, frame) == (0.0, "")


def test_a_wrapped_line_below_the_frame_is_overflow():
    frame = lc._Box(3, 7, 4.7, 0.55)
    line = lc._Box(3.2, 7.6, 1.5, 0.6)
    overshoot, edge = lc._frame_overshoot(line, frame)
    assert edge == "bottom"
    assert overshoot == pytest.approx(0.65)


def test_overflow_names_the_text_it_lands_on():
    frames = [_frame("標題很長很長", 1, 1, 10, 1), _frame("內文", 1, 2.2, 10, 1)]
    lines = [_line("標題很長", 1, 1.1, 8), _line("很長", 1, 2.3, 3), _line("內文", 4, 2.3, 2)]
    findings = lc._slide_findings(3, frames, lines, 28, 15.75)
    assert len(findings) == 1
    assert findings[0].kind == "frame"
    assert findings[0].collides_with == "內文"
    assert findings[0].describe().startswith("第 3 頁「標題很長很長」文字超出框的下緣")
    assert "壓到「內文」" in findings[0].describe()


def test_text_reaching_the_page_edge_is_reported_as_cut_off():
    findings = lc._slide_findings(1, [], [_line("超出", 26, 5, 2.0)], 28, 15.75)
    assert [f.kind for f in findings] == ["page"]
    assert "被裁掉" in findings[0].describe()


def test_text_document_past_the_right_margin():
    lines = [_line("正常段落", 2, 3, 17), _line("太寬的表格內容", 2, 5, 18.5)]
    findings = lc._text_page_findings(1, lines, 21, 29.7, 2)
    assert [(f.kind, f.excerpt) for f in findings] == [("margin", "太寬的表格內容")]
    assert findings[0].overshoot_cm == pytest.approx(1.5)


def test_margins_come_from_the_master_page_in_use():
    style = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"
    fo = "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"
    styles = etree.fromstring(
        f'<r xmlns:style="{style}" xmlns:fo="{fo}">'
        '<style:page-layout style:name="pm1"><style:page-layout-properties '
        'fo:page-width="21cm" fo:margin-right="2cm"/></style:page-layout>'
        '<style:page-layout style:name="pm2"><style:page-layout-properties '
        'fo:page-width="21cm" fo:margin-right="1cm"/></style:page-layout>'
        '<style:master-page style:name="Standard" style:page-layout-name="pm1"/>'
        '<style:master-page style:name="HTML" style:page-layout-name="pm2"/></r>'
    )
    plain = etree.fromstring(f'<c xmlns:style="{style}"/>')
    uses_html = etree.fromstring(
        f'<c xmlns:style="{style}"><style:style style:master-page-name="HTML"/></c>'
    )
    assert lc._text_area_right_margins(plain, styles) == {21.0: 2.0}
    assert lc._text_area_right_margins(uses_html, styles) == {21.0: 1.0}


# -- reports that are not a verdict ---------------------------------------------


def test_spreadsheets_are_not_checked(tmp_path):
    report = check_layout(tmp_path / "x.ods", Path("soffice"))
    assert report.status == "unsupported"


def test_without_libreoffice_the_check_is_unavailable_not_ok(tmp_path, sample_presentation):
    out = render_odp(sample_presentation, tmp_path / "p.odp")
    report = check_layout(out, None)
    assert report.status == "unavailable"
    assert not report.ok


def test_check_report_skips_layout_without_libreoffice(
    tmp_path, sample_presentation, monkeypatch
):
    monkeypatch.setattr("odforge.check.find_soffice", lambda: None)
    report = check_odf(render_odp(sample_presentation, tmp_path / "p.odp"))
    assert "## 跑版" in report
    assert "略過:找不到 LibreOffice" in report


def test_finding_wording():
    finding = LayoutFinding(2, "margin", "表格", "right", 1.25)
    assert finding.describe() == "第 2 頁「表格」超出右邊界 1.25 cm"


# -- end to end through LibreOffice ---------------------------------------------


def _stuff_first_text_frame(odp: Path, text: str) -> None:
    """Replace the text of slide 2's first text frame, leaving its box as is."""
    ns = {
        "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
        "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    }
    with zipfile.ZipFile(odp) as package:
        parts = {name: package.read(name) for name in package.namelist()}
    content = etree.fromstring(parts["content.xml"])
    page = content.findall(".//draw:page", ns)[1]
    box = page.find(".//draw:frame/draw:text-box", ns)
    paragraph = box.find("text:p", ns)
    for child in list(box):
        if child is not paragraph:
            box.remove(child)
    for child in list(paragraph):
        paragraph.remove(child)
    paragraph.text = text
    parts["content.xml"] = etree.tostring(content, xml_declaration=True, encoding="UTF-8")
    tmp = odp.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w") as out:
        out.writestr(
            zipfile.ZipInfo("mimetype"), parts.pop("mimetype"), compress_type=zipfile.ZIP_STORED
        )
        for name, data in parts.items():
            out.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    shutil.move(tmp, odp)


@needs_soffice
def test_a_rendered_deck_passes(tmp_path, sample_presentation):
    out = render_odp(sample_presentation, tmp_path / "p.odp")
    report = check_layout(out, find_soffice())
    assert report.status == "ok", [f.describe() for f in report.findings]


@needs_soffice
def test_overstuffed_frame_is_caught_on_its_page(tmp_path, sample_presentation):
    out = render_odp(sample_presentation, tmp_path / "p.odp")
    _stuff_first_text_frame(out, "這一段文字會被塞到放不下為止。" * 40)
    report = check_layout(out, find_soffice())
    assert report.status == "overflow"
    assert {f.page for f in report.findings} == {2}
    assert any(f.kind in ("frame", "page") for f in report.findings)


@needs_soffice
def test_a_too_wide_table_in_a_text_document(tmp_path, sample_text_doc):
    html = tmp_path / "wide.html"
    html.write_text(
        '<html><body><table border="1" style="width:1400px"><tr>'
        f"<td>{'A' * 42}</td><td>{'B' * 42}</td><td>{'C' * 30}</td>"
        "</tr></table><p>正常段落。</p></body></html>",
        encoding="utf-8",
    )
    run_soffice_convert(find_soffice(), html, "odt:writer8", tmp_path)
    report = check_layout(tmp_path / "wide.odt", find_soffice())
    assert report.status == "overflow"
    assert "AAAA" in report.findings[0].excerpt

    clean = check_layout(render_odt(sample_text_doc, tmp_path / "t.odt"), find_soffice())
    assert clean.status == "ok", [f.describe() for f in clean.findings]
