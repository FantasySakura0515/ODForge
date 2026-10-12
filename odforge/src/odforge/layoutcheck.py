"""Rendered layout check (跑版檢查): where the text actually landed.

:func:`odforge.textmetrics.check_budget` *estimates* whether text fits, from the
IR and a width table, before anything is rendered. That is the right tool while
generating, and the wrong one for everything else: it cannot see a file that
ODForge did not make, and even on its own output an estimate can be wrong
(fallback fonts, LibreOffice's line breaking, autofit that never kicks in).

This module asks LibreOffice instead. It converts the document to PDF, reads
back every rendered line of text with its position, and compares it with the
box the document declared for it:

* ``.odp`` — each rendered line is matched to the text frame it came from (by
  its text; the nearest frame wins when two frames share a phrase) and reported
  when it pokes out of that frame or off the slide. A line that lands on another
  frame's text is named as such, because that is what a reader sees as 跑版.
* ``.odt`` — text reflows, so frames are not the question; text that runs past
  the right margin (a table or an unbreakable string too wide for the page) or
  off the page is.
* ``.ods`` — no fixed page layout to overrun; reported as unsupported.

Lines that match no frame (page numbers, master-page decoration, fields) are
left alone rather than guessed at. The check never raises: a missing LibreOffice
or a failed conversion comes back as ``status="unavailable"`` with the reason,
and unavailable is never presented as a pass.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Literal, Optional

# `pymupdf`, never the `fitz` alias: the alias prints to stdout, which corrupts
# the MCP stdio stream (see preview.py).
import pymupdf
from lxml import etree

from .validate import run_soffice_convert
from .xmlsafe import safe_fromstring

_NS = {
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
}

_SOFFICE_TIMEOUT = 120
_PT_TO_CM = 2.54 / 72

# A rendered line box includes the font's ascent and descent, so a one-line
# label in a frame sized to its glyphs pokes out of the frame by a few
# millimetres while looking perfectly placed. Vertically, a line therefore only
# counts as outside once its *centre* is — more than half of it has left the
# frame, which no padding or line-height slack explains.
_MIN_TOLERANCE_CM = 0.1
# Horizontally the glyph boxes are tight, so a small fixed slack is enough.
_HORIZONTAL_TOLERANCE_CM = 0.15
# LibreOffice clips whatever lies past the page when it exports the PDF, so
# off-page text is never *seen* beyond the edge — it shows up as a line that
# runs right up to it. No real layout sets text flush against the paper edge,
# so a line within this distance of an edge counts as running off the page.
_PAGE_TOLERANCE_CM = 0.05

# Text extraction must see what lies beyond the page: PyMuPDF clips to the
# mediabox by default, which would hide exactly the text this check looks for.
_TEXT_FLAGS = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_MEDIABOX_CLIP

_EXCERPT_CHARS = 16

_EDGE_LABEL = {"bottom": "下緣", "top": "上緣", "right": "右緣", "left": "左緣"}


@dataclass(frozen=True)
class _Box:
    x: float
    y: float
    w: float
    h: float

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    def distance(self, other: _Box) -> float:
        dx = max(self.x - other.right, other.x - self.right, 0.0)
        dy = max(self.y - other.bottom, other.y - self.bottom, 0.0)
        return (dx * dx + dy * dy) ** 0.5

    def overlaps_horizontally(self, other: _Box) -> bool:
        return min(self.right, other.right) > max(self.x, other.x)

    def overlap_area(self, other: _Box) -> float:
        w = min(self.right, other.right) - max(self.x, other.x)
        h = min(self.bottom, other.bottom) - max(self.y, other.y)
        return w * h if w > 0 and h > 0 else 0.0


@dataclass(frozen=True)
class _Frame:
    box: _Box
    key: str
    excerpt: str


@dataclass(frozen=True)
class _Line:
    box: _Box
    text: str
    key: str


@dataclass(frozen=True)
class LayoutFinding:
    """One place where rendered text left the space it was given."""

    page: int
    kind: Literal["frame", "page", "margin"]
    excerpt: str
    edge: str
    overshoot_cm: float
    collides_with: str = ""

    def describe(self) -> str:
        return f"第 {self.page} 頁{self.detail()}"

    def detail(self) -> str:
        """The finding without its page number, for lists already grouped by page."""
        where = f"「{self.excerpt}」"
        if self.kind == "frame":
            text = f"{where}文字超出框的{_EDGE_LABEL[self.edge]} {self.overshoot_cm:.2f} cm"
            if self.collides_with:
                text += f",壓到「{self.collides_with}」"
            return text
        if self.kind == "margin":
            return f"{where}超出右邊界 {self.overshoot_cm:.2f} cm"
        # The clipped part is not in the PDF, so there is no honest distance.
        return f"{where}文字延伸到頁面{_EDGE_LABEL[self.edge]}外,超出的部分會被裁掉"


@dataclass
class LayoutReport:
    """``status``: ``ok`` / ``overflow`` / ``unavailable`` / ``unsupported``."""

    status: Literal["ok", "overflow", "unavailable", "unsupported"]
    message: str
    findings: List[LayoutFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"


def _key(text: str) -> str:
    """Letters, digits and CJK only — bullets, spaces and punctuation vary
    between the XML and the rendered glyph stream, the words do not."""
    return re.sub(r"[\W_]+", "", text)


def _excerpt(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _EXCERPT_CHARS else text[:_EXCERPT_CHARS] + "…"


def _cm(value: Optional[str]) -> float:
    if not value:
        return 0.0
    match = re.match(r"^(-?[0-9.]+)\s*([a-z]*)$", value.strip())
    if not match:
        return 0.0
    number, unit = float(match.group(1)), match.group(2)
    return {
        "mm": number / 10,
        "in": number * 2.54,
        "pt": number * _PT_TO_CM,
    }.get(unit, number)


def _read_parts(path: Path) -> tuple[etree._Element, etree._Element]:
    with zipfile.ZipFile(path) as package:
        content = safe_fromstring(package.read("content.xml"))
        try:
            styles = safe_fromstring(package.read("styles.xml"))
        except KeyError:
            styles = content
    return content, styles


# ---------------------------------------------------------------------------
# Declared geometry
# ---------------------------------------------------------------------------


def _hidden_page_styles(roots: tuple[etree._Element, ...]) -> set[str]:
    hidden: set[str] = set()
    for root in roots:
        for style in root.iter(f"{{{_NS['style']}}}style"):
            for props in style.iter(f"{{{_NS['style']}}}drawing-page-properties"):
                if props.get(f"{{{_NS['presentation']}}}visibility") == "hidden":
                    hidden.add(style.get(f"{{{_NS['style']}}}name", ""))
    return hidden


_PARAGRAPH = f"{{{_NS['text']}}}p"
_SPACERS = {
    f"{{{_NS['text']}}}{tag}" for tag in ("s", "tab", "line-break")
}


def _plain_text(element: etree._Element) -> str:
    """The element's text with ``<text:s>``/tabs/breaks as spaces, so excerpts
    read "17 輸出順序" rather than "17輸出順序"."""
    parts: List[str] = []
    for node in element.iter():
        if node.tag in _SPACERS or (node.tag == _PARAGRAPH and parts):
            parts.append(" ")
        if node.text:
            parts.append(node.text)
        if node is not element and node.tail:
            parts.append(node.tail)
    return "".join(parts)


def _page_frames(page: etree._Element) -> List[_Frame]:
    """Text-bearing frames and shapes on one slide, speaker notes excluded."""
    frames: List[_Frame] = []
    notes = f"{{{_NS['presentation']}}}notes"
    text_box = f"{{{_NS['draw']}}}text-box"
    shape_tags = {
        f"{{{_NS['draw']}}}{tag}"
        for tag in ("custom-shape", "rect", "ellipse", "circle")
    }

    def walk(element: etree._Element) -> None:
        for child in element:
            if child.tag == notes:
                continue
            if child.get(f"{{{_NS['draw']}}}transform"):
                # Rotated or skewed: the declared box is not where it renders.
                continue
            if child.tag == f"{{{_NS['draw']}}}frame":
                box_el = child.find(text_box)
                if box_el is not None:
                    _add(child, box_el)
                continue
            if child.tag in shape_tags:
                _add(child, child)
                continue
            walk(child)

    def _add(holder: etree._Element, text_root: etree._Element) -> None:
        text = _plain_text(text_root)
        key = _key(text)
        if not key:
            return
        frames.append(
            _Frame(
                _Box(
                    _cm(holder.get(f"{{{_NS['svg']}}}x")),
                    _cm(holder.get(f"{{{_NS['svg']}}}y")),
                    _cm(holder.get(f"{{{_NS['svg']}}}width")),
                    _cm(holder.get(f"{{{_NS['svg']}}}height")),
                ),
                key,
                _excerpt(text),
            )
        )

    walk(page)
    return frames


def _slides(content: etree._Element, styles: etree._Element) -> List[List[_Frame]]:
    """Frames per *exported* slide — hidden slides are not in the PDF."""
    hidden = _hidden_page_styles((content, styles))
    slides: List[List[_Frame]] = []
    for page in content.iter(f"{{{_NS['draw']}}}page"):
        if page.get(f"{{{_NS['draw']}}}style-name") in hidden:
            continue
        slides.append(_page_frames(page))
    return slides


def _text_area_right_margins(
    content: etree._Element, styles: etree._Element
) -> Dict[float, float]:
    """Page width (cm, rounded) → right margin (cm) of the page layouts in use.

    A document usually carries several page layouts (Standard, Endnote, HTML…)
    and only some are used. Only the master pages the text actually asks for
    count — ``Standard`` when nothing asks — and where two in-use layouts share
    a width the narrower margin wins, so text is never called out for using
    space one of its own pages grants.
    """
    style, fo = _NS["style"], _NS["fo"]
    used = {
        name
        for root in (content, styles)
        for el in root.iter()
        if (name := el.get(f"{{{style}}}master-page-name"))
    } or {"Standard"}
    layouts = {
        el.get(f"{{{style}}}name"): el
        for el in styles.iter(f"{{{style}}}page-layout")
    }
    chosen = [
        layouts[el.get(f"{{{style}}}page-layout-name")]
        for el in styles.iter(f"{{{style}}}master-page")
        if el.get(f"{{{style}}}name") in used
        and el.get(f"{{{style}}}page-layout-name") in layouts
    ] or list(layouts.values())
    margins: Dict[float, float] = {}
    for layout in chosen:
        for props in layout.iter(f"{{{style}}}page-layout-properties"):
            width = _cm(props.get(f"{{{fo}}}page-width"))
            if width <= 0:
                continue
            key = round(width, 1)
            margin = _cm(props.get(f"{{{fo}}}margin-right"))
            margins[key] = min(margin, margins.get(key, margin))
    return margins


# ---------------------------------------------------------------------------
# Rendered geometry
# ---------------------------------------------------------------------------


def _rendered_lines(page: pymupdf.Page) -> List[_Line]:
    lines: List[_Line] = []
    for block in page.get_text("dict", flags=_TEXT_FLAGS)["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"])
            key = _key(text)
            if not key:
                continue
            x0, y0, x1, y1 = (v * _PT_TO_CM for v in line["bbox"])
            lines.append(_Line(_Box(x0, y0, x1 - x0, y1 - y0), text, key))
    return lines


def _page_overrun(line: _Line, width: float, height: float) -> tuple[str, float]:
    box = line.box
    edges = {
        "bottom": box.bottom - height,
        "top": -box.y,
        "right": box.right - width,
        "left": -box.x,
    }
    edge = max(edges, key=edges.__getitem__)
    return edge, edges[edge]


def _frame_overshoot(line: _Box, frame: _Box) -> tuple[float, str]:
    """How far ``line`` sits outside ``frame`` (0 when it counts as inside)."""
    centre = line.y + line.h / 2
    vertical = {"bottom": line.bottom - frame.bottom, "top": frame.y - line.y}
    horizontal = {"right": line.right - frame.right, "left": frame.x - line.x}
    outside: Dict[str, float] = {}
    if centre > frame.bottom + _MIN_TOLERANCE_CM or centre < frame.y - _MIN_TOLERANCE_CM:
        outside.update(vertical)
    outside.update(
        {edge: over for edge, over in horizontal.items() if over > _HORIZONTAL_TOLERANCE_CM}
    )
    if not outside:
        return 0.0, ""
    edge = max(outside, key=outside.__getitem__)
    return outside[edge], edge


def _assign(frames: List[_Frame], lines: List[_Line]) -> List[Optional[int]]:
    """Which frame each rendered line came from (``None`` when none did).

    LibreOffice writes a frame's lines one after another, so the lines of one
    frame arrive as a run whose keys concatenate to the frame's key. Following
    that run keeps wrapped-out lines with the frame they spilled from: the tail
    of an overflowing label ("輸出" "順序") stays with "17 輸出順序" instead of
    being handed to whichever frame below happens to be nearer.

    A line that starts no run falls back to the nearest frame containing it.
    Either way the frame must share horizontal span with the line — overflow
    runs down or sideways, never into another column; without that, a page
    number "2" was matched to an agenda frame that says "02".
    """

    def nearest(indices: List[int], line: _Line) -> Optional[int]:
        indices = [i for i in indices if frames[i].box.overlaps_horizontally(line.box)]
        if not indices:
            return None
        return min(indices, key=lambda i: frames[i].box.distance(line.box))

    assigned: List[Optional[int]] = []
    current: Optional[int] = None
    remaining = ""
    started: set[int] = set()
    for line in lines:
        if current is not None and remaining.startswith(line.key):
            assigned.append(current)
            remaining = remaining[len(line.key):]
            if not remaining:
                current = None
            continue
        index = nearest(
            [i for i, f in enumerate(frames) if i not in started and f.key.startswith(line.key)],
            line,
        )
        if index is not None:
            started.add(index)
            current, remaining = index, frames[index].key[len(line.key):]
            if not remaining:
                current = None
        else:
            current = None
            index = nearest([i for i, f in enumerate(frames) if line.key in f.key], line)
        assigned.append(index)
    return assigned


def _slide_findings(
    number: int, frames: List[_Frame], lines: List[_Line], width: float, height: float
) -> List[LayoutFinding]:
    worst: Dict[int, tuple[float, str, str]] = {}  # frame index → overshoot, edge, hit
    off_page: Optional[tuple[float, str, str]] = None

    for line, index in zip(lines, _assign(frames, lines), strict=True):
        edge, overrun = _page_overrun(line, width, height)
        if overrun > -_PAGE_TOLERANCE_CM and (off_page is None or overrun > off_page[0]):
            off_page = (overrun, edge, _excerpt(line.text))
        if index is None:
            continue
        frame = frames[index].box
        overshoot, edge = _frame_overshoot(line.box, frame)
        if overshoot <= 0:
            continue
        hit = next(
            (
                other.excerpt
                for j, other in enumerate(frames)
                if j != index
                and other.box.overlap_area(line.box) > 0.3 * line.box.w * line.box.h
            ),
            "",
        )
        previous = worst.get(index)
        if previous is None or overshoot > previous[0]:
            worst[index] = (overshoot, edge, hit or (previous[2] if previous else ""))

    findings = [
        LayoutFinding(number, "frame", frames[i].excerpt, edge, round(over, 2), hit)
        for i, (over, edge, hit) in sorted(worst.items())
    ]
    if off_page is not None:
        over, edge, excerpt = off_page
        findings.append(LayoutFinding(number, "page", excerpt, edge, round(over, 2)))
    return findings


def _text_page_findings(
    number: int, lines: List[_Line], width: float, height: float, right_margin: float
) -> List[LayoutFinding]:
    findings: List[LayoutFinding] = []
    off_page: Optional[tuple[float, str, str]] = None
    past_margin: Optional[tuple[float, str]] = None
    text_right = width - right_margin
    for line in lines:
        edge, overrun = _page_overrun(line, width, height)
        if overrun > -_PAGE_TOLERANCE_CM:
            if off_page is None or overrun > off_page[0]:
                off_page = (overrun, edge, _excerpt(line.text))
            continue
        if right_margin <= 0:
            continue
        over = line.box.right - text_right
        if over > _HORIZONTAL_TOLERANCE_CM and (past_margin is None or over > past_margin[0]):
            past_margin = (over, _excerpt(line.text))
    if past_margin is not None:
        findings.append(
            LayoutFinding(number, "margin", past_margin[1], "right", round(past_margin[0], 2))
        )
    if off_page is not None:
        over, edge, excerpt = off_page
        findings.append(LayoutFinding(number, "page", excerpt, edge, round(over, 2)))
    return findings


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _convert_to_pdf(path: Path, soffice: Path, outdir: Path) -> Path:
    try:
        proc = run_soffice_convert(soffice, path, "pdf", outdir, timeout=_SOFFICE_TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"LibreOffice 轉 PDF 逾時({_SOFFICE_TIMEOUT} 秒)") from exc
    pdf = outdir / (path.stem + ".pdf")
    if proc.returncode != 0 or not pdf.exists():
        detail = (proc.stderr or proc.stdout or "").strip()[:300]
        raise RuntimeError(f"LibreOffice 轉 PDF 失敗:{detail or proc.returncode}")
    return pdf


def check_layout(path: Path, soffice: Optional[Path]) -> LayoutReport:
    """Render ``path`` with LibreOffice and report text that left its space.

    ``soffice`` is passed in (usually :func:`~odforge.validate.find_soffice`)
    rather than looked up here, so the caller's choice — including "none" — is
    the one that holds. Never raises.
    """
    path = Path(path)
    unsupported = _unsupported(path)
    if unsupported is not None:
        return unsupported
    if soffice is None:
        return LayoutReport("unavailable", "找不到 LibreOffice(soffice),無法檢查跑版")

    workdir = Path(tempfile.mkdtemp(prefix="odforge-layout-"))
    try:
        pdf = _convert_to_pdf(path, soffice, workdir)
    except Exception as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        return LayoutReport("unavailable", f"跑版檢查無法執行:{type(exc).__name__}: {exc}")
    try:
        return check_layout_pdf(path, pdf)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _unsupported(path: Path) -> Optional[LayoutReport]:
    ext = path.suffix.lower()
    if ext == ".ods":
        return LayoutReport("unsupported", "試算表沒有固定版面,不做跑版檢查")
    if ext not in (".odp", ".odt"):
        return LayoutReport("unsupported", f"不支援 {ext or '(無副檔名)'} 的跑版檢查")
    return None


def check_layout_pdf(path: Path, pdf: Path) -> LayoutReport:
    """Like :func:`check_layout`, against a PDF LibreOffice already made of ``path``.

    For callers that convert anyway (the live preview rasterises the same PDF),
    so one document is not laid out twice. Never raises.
    """
    path = Path(path)
    unsupported = _unsupported(path)
    if unsupported is not None:
        return unsupported
    try:
        content, styles = _read_parts(path)
        findings: List[LayoutFinding] = []
        with pymupdf.open(pdf) as doc:
            if path.suffix.lower() == ".odp":
                slides = _slides(content, styles)
                if len(slides) != doc.page_count:
                    return LayoutReport(
                        "unavailable",
                        f"投影片數({len(slides)})與轉出的頁數({doc.page_count})對不上,"
                        "無法逐頁比對",
                    )
                for number, (frames, page) in enumerate(
                    zip(slides, doc, strict=True), start=1
                ):
                    findings.extend(
                        _slide_findings(
                            number,
                            frames,
                            _rendered_lines(page),
                            page.rect.width * _PT_TO_CM,
                            page.rect.height * _PT_TO_CM,
                        )
                    )
            else:
                margins = _text_area_right_margins(content, styles)
                for number, page in enumerate(doc, start=1):
                    width = page.rect.width * _PT_TO_CM
                    findings.extend(
                        _text_page_findings(
                            number,
                            _rendered_lines(page),
                            width,
                            page.rect.height * _PT_TO_CM,
                            margins.get(round(width, 1), 0.0),
                        )
                    )
    except Exception as exc:
        return LayoutReport("unavailable", f"跑版檢查無法執行:{type(exc).__name__}: {exc}")

    if not findings:
        return LayoutReport("ok", "沒有文字超出框或頁面")
    return LayoutReport("overflow", f"發現 {len(findings)} 處跑版", findings)
