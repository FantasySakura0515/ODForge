"""What the renderer actually emitted, as plain facts for the design critic.

The vision critic used to receive images and a checklist and nothing else. Asked
to judge alignment and contrast from pixels alone, it did what anyone would: it
estimated, and some estimates were wrong. It reported *「三張卡片高度不一致」* on
a row of three 6.80cm cards, and *「對比不足」* on #1F2733 text measuring 12.52:1.
Worse in the other direction, a deck whose every card had been repainted on top
of its own text — three pages of blank boxes — drew four remarks about title
spacing and not one about the missing words, because nothing in its context said
those cards were supposed to contain anything.

An earlier version of this module answered that by checking the critic's claims
against measurements and overruling the ones that failed. That worked, but only
one defect at a time: a keyword list per claim family, extended the first time
the model wrote 「色塊」 instead of 「卡片」, and due for extension again at every
new layout and every new turn of phrase. The list is never finished.

So the measurements stay and the verdicts go. :func:`grounding_text` hands the
critic what the renderer worked from — boxes, colours, sizes, paint order — and
lets it reach its own conclusion. A controlled comparison on a deck whose text
was buried under its own cards showed the blind spot close: unprompted, at error
severity, it identified the hidden words.

A second comparison showed what the dump must *not* be, twice over. Given two
hex colours the critic computed 2.8:1 for a pair measuring 13:1 and cited WCAG
against its own wrong answer. Handed the ratio already computed, it stopped
miscalculating and started misjudging instead: 11.8:1 was reported as 「低於
WCAG AA 標準建議的 12:1」 — a threshold that does not exist — and the 96pt
section-number watermark, faint by design at 2.1:1, became an error. Errors per
run tripled. The failure is not the arithmetic and not the threshold wording; it
is that a colour ratio is not a judgement this model can make, computed for it
or not.

So contrast numbers are not supplied here at all. What is supplied is geometry:
boxes, sizes, paint order, and the height and gap differences already reduced —
the facts behind the one thing grounding measurably fixed, which is seeing that
text on the page is not visible on the page.

**Contract change worth knowing:** this abandons the strict independent-context
rule the critic was built with. It now sees the page's text, not just its
picture. That is the point — text that *should* be visible is the only way to
notice text that is not — but it does mean the critic is no longer judging
purely from what a viewer would see. Nothing about the *generation* reaches it:
no prompt, no outline, no model history. Only the finished page.

Never raises: an unreadable package yields no facts, and a critique with no
grounding is exactly the critique this module was added to improve.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Dict, List, Sequence

from lxml import etree

from odforge.xmlsafe import safe_fromstring

_NS = {
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
}


def _cm(value: str | None) -> float:
    """Parse an ODF length like ``"6.8cm"`` into centimetres (0.0 when absent)."""
    if not value:
        return 0.0
    match = re.match(r"^(-?[0-9.]+)\s*([a-z]*)$", value.strip())
    if not match:
        return 0.0
    number, unit = float(match.group(1)), match.group(2)
    if unit in ("cm", ""):
        return number
    if unit == "mm":
        return number / 10
    if unit == "in":
        return number * 2.54
    if unit == "pt":
        return number * 2.54 / 72
    return number


def _pt(value: str) -> float:
    """Parse an ODF font size like ``"96pt"`` into points (0.0 when unparseable)."""
    match = re.match(r"^([0-9.]+)\s*pt$", value.strip())
    return float(match.group(1)) if match else 0.0


@dataclass(frozen=True)
class Box:
    """A positioned rectangle in centimetres."""

    x: float
    y: float
    w: float
    h: float

    def contains(self, px: float, py: float) -> bool:
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h


@dataclass(frozen=True)
class Shape:
    """A filled shape. ``order`` is its position in paint order."""

    box: Box
    fill: str
    order: int = 0


@dataclass(frozen=True)
class TextRun:
    """A positioned text frame: its ink, size, and place in paint order."""

    box: Box
    color: str
    text: str
    order: int = 0
    size_pt: float = 0.0


@dataclass
class PageMeasure:
    """One page as the renderer emitted it. Facts only — no judgements."""

    number: int
    shapes: List[Shape] = field(default_factory=list)
    runs: List[TextRun] = field(default_factory=list)
    background: str = "#FFFFFF"

    def backdrop(self, run: TextRun) -> str:
        """The fill behind ``run`` — the last shape painted *under* it.

        Shapes painted after the run are covers, not backdrops; reading one as a
        background is how a page whose text is buried under an opaque card
        measures as perfectly legible.
        """
        cx, cy = run.box.x + run.box.w / 2, run.box.y + run.box.h / 2
        fill = self.background
        for shape in self.shapes:
            if shape.order < run.order and shape.box.contains(cx, cy):
                fill = shape.fill
        return fill


def _painted(element: etree._Element):
    """Walk a page's drawing elements in paint order, skipping speaker notes.

    ``<presentation:notes>`` is a child of ``<draw:page>`` but is never drawn on
    the slide, so descending into it would hand the critic the presenter script
    as though it were on the canvas.
    """
    for child in element:
        if child.tag == f"{{{_NS['presentation']}}}notes":
            continue
        yield child
        yield from _painted(child)


def _style_colours(
    roots: Sequence[etree._Element],
) -> tuple[Dict[str, str], Dict[str, str], Dict[str, float]]:
    """Map style name → fill colour, → text colour, and → font size in points."""
    fills: Dict[str, str] = {}
    inks: Dict[str, str] = {}
    sizes: Dict[str, float] = {}
    for root in roots:
        for style in root.iter(f"{{{_NS['style']}}}style"):
            name = style.get(f"{{{_NS['style']}}}name")
            if not name:
                continue
            for gp in style.iter(f"{{{_NS['style']}}}graphic-properties"):
                fill = gp.get(f"{{{_NS['draw']}}}fill-color")
                if fill:
                    fills[name] = fill
            for tp in style.iter(f"{{{_NS['style']}}}text-properties"):
                ink = tp.get(f"{{{_NS['fo']}}}color")
                if ink:
                    inks[name] = ink
                size = tp.get(f"{{{_NS['fo']}}}font-size")
                if size:
                    sizes[name] = _pt(size)
    return fills, inks, sizes


def _page_backgrounds(roots: Sequence[etree._Element]) -> Dict[str, str]:
    """Map drawing-page style name → its fill colour."""
    backgrounds: Dict[str, str] = {}
    for root in roots:
        for style in root.iter(f"{{{_NS['style']}}}style"):
            name = style.get(f"{{{_NS['style']}}}name")
            if not name:
                continue
            for dp in style.iter(f"{{{_NS['style']}}}drawing-page-properties"):
                fill = dp.get(f"{{{_NS['draw']}}}fill-color")
                if fill:
                    backgrounds[name] = fill
    return backgrounds


def _box_of(element: etree._Element) -> Box:
    return Box(
        _cm(element.get(f"{{{_NS['svg']}}}x")),
        _cm(element.get(f"{{{_NS['svg']}}}y")),
        _cm(element.get(f"{{{_NS['svg']}}}width")),
        _cm(element.get(f"{{{_NS['svg']}}}height")),
    )


def page_measurements(odp_path: Path) -> Dict[int, PageMeasure]:
    """Measure every page of ``odp_path``: shapes, text runs, colours, sizes.

    Returns ``{page_number: PageMeasure}`` (1-based, matching ``Finding.slide_no``),
    or ``{}`` when the package cannot be read.
    """
    odp_path = Path(odp_path)
    try:
        with zipfile.ZipFile(odp_path) as package:
            content = safe_fromstring(package.read("content.xml"))
            try:
                styles = safe_fromstring(package.read("styles.xml"))
            except KeyError:
                styles = content
    except (OSError, KeyError, zipfile.BadZipFile, etree.XMLSyntaxError):
        return {}

    fills, inks, sizes = _style_colours((content, styles))
    backgrounds = _page_backgrounds((content, styles))

    pages: Dict[int, PageMeasure] = {}
    for number, page_el in enumerate(content.iter(f"{{{_NS['draw']}}}page"), start=1):
        measure = PageMeasure(number=number)
        measure.background = backgrounds.get(
            page_el.get(f"{{{_NS['draw']}}}style-name"), "#FFFFFF"
        )
        # Document order is paint order in ODF: later siblings render on top.
        for order, element in enumerate(_painted(page_el)):
            tag = etree.QName(element).localname
            style_name = element.get(f"{{{_NS['draw']}}}style-name")
            if tag in ("rect", "custom-shape", "polygon", "circle", "ellipse"):
                fill = fills.get(style_name)
                if fill:
                    measure.shapes.append(Shape(_box_of(element), fill, order))
            elif tag == "line":
                # A connector has no fill; record its span so the critic can see
                # where a line actually runs rather than guess from pixels.
                measure.shapes.append(
                    Shape(
                        Box(
                            min(_cm(element.get(f"{{{_NS['svg']}}}x1")),
                                _cm(element.get(f"{{{_NS['svg']}}}x2"))),
                            min(_cm(element.get(f"{{{_NS['svg']}}}y1")),
                                _cm(element.get(f"{{{_NS['svg']}}}y2"))),
                            abs(_cm(element.get(f"{{{_NS['svg']}}}x2"))
                                - _cm(element.get(f"{{{_NS['svg']}}}x1"))),
                            abs(_cm(element.get(f"{{{_NS['svg']}}}y2"))
                                - _cm(element.get(f"{{{_NS['svg']}}}y1"))),
                        ),
                        "line",
                        order,
                    )
                )
            elif tag == "frame":
                paragraph = next(iter(element.iter(f"{{{_NS['text']}}}p")), None)
                if paragraph is None:
                    continue
                paragraph_style = paragraph.get(f"{{{_NS['text']}}}style-name")
                ink = inks.get(paragraph_style)
                if not ink:
                    continue
                measure.runs.append(
                    TextRun(
                        _box_of(element),
                        ink,
                        "".join(element.itertext()),
                        order,
                        sizes.get(paragraph_style, 0.0),
                    )
                )
        pages[number] = measure
    return pages


_GROUNDING_PREAMBLE = """\
以下是這份簡報的實際版面資料,由排版引擎輸出,不是推測值。座標單位公分,原點在
左上角;paint 為繪製順序,數字越大越後畫、越蓋在上層。請用這些數字核對你從影像
中看到的東西,不要憑目測估計位置、大小或顏色。若你的觀察與資料不符,以資料為準,
並據此修正或撤回該項判斷。

色塊高度差與間距差都已經替你算好,直接引用即可,不要自行從座標重新計算。凡是資料裡
寫「完全相同」或差距標為 0.00 的,就是完全一致,不要提出等高或間距方面的 finding。"""


# A filled shape this size or larger reads as a container whose geometry a
# viewer would compare against its neighbours; below it is decoration (a rule, an
# accent bar) and belongs in no evenness summary.
_CONTAINER_MIN_W = 2.0
_CONTAINER_MIN_H = 1.0


def _containers(page: PageMeasure) -> List[Box]:
    return [
        s.box
        for s in page.shapes
        if s.fill != "line"
        and s.box.w >= _CONTAINER_MIN_W
        and s.box.h >= _CONTAINER_MIN_H
    ]


def _evenness(page: PageMeasure) -> str:
    """One line stating how even this page's containers actually are.

    The arithmetic is done here because the model demonstrably cannot do it:
    handed four x-coordinates it reported 6.11cm and 6.12cm as 「肉眼可見」
    unevenness, and handed two hex colours it computed 2.8:1 for a pair that
    measures 13:1 and cited WCAG against its own wrong answer. Raw numbers it
    must reduce itself are worse than no numbers — they lend false authority to
    a bad calculation. So the differences arrive already reduced.
    """
    boxes = _containers(page)
    if len(boxes) < 2:
        return ""
    heights = [b.h for b in boxes]
    spread = max(heights) - min(heights)
    parts = [
        f"本頁有 {len(boxes)} 個色塊,高度差 {spread:.2f}cm"
        + ("(完全相同)" if spread < 0.005 else "")
    ]
    rows: Dict[int, List[Box]] = {}
    for box in boxes:
        rows.setdefault(round(box.y * 20), []).append(box)
    for row in rows.values():
        if len(row) < 3:
            continue
        xs = sorted(b.x for b in row)
        gaps = [b - a for a, b in pairwise(xs)]
        gap_spread = max(gaps) - min(gaps)
        parts.append(
            f"同一列 {len(row)} 個色塊的水平間距差 {gap_spread:.2f}cm"
            + ("(完全相同)" if gap_spread < 0.005 else "")
        )
    return "  " + ";".join(parts)


def grounding_text(odp_path: Path) -> str:
    """Render the page facts as context for the critic (``""`` when unreadable).

    Quantities the model would otherwise have to derive — contrast ratios,
    height and spacing differences — arrive already computed; see
    :func:`_evenness`. Text is reproduced in full: a truncated string reads as
    text the *renderer* cut off, and gets reported as a defect.
    """
    pages = page_measurements(odp_path)
    if not pages:
        return ""
    lines = [_GROUNDING_PREAMBLE]
    for number, page in sorted(pages.items()):
        lines.append(f"\n[第 {number} 頁] 頁面背景 {page.background}")
        evenness = _evenness(page)
        if evenness:
            lines.append(evenness)
        for shape in page.shapes:
            kind = "連接線" if shape.fill == "line" else f"色塊 填色={shape.fill}"
            lines.append(
                f"  {kind} paint={shape.order} x={shape.box.x:.2f} y={shape.box.y:.2f} "
                f"w={shape.box.w:.2f} h={shape.box.h:.2f}"
            )
        for run in page.runs:
            text = " ".join(run.text.split())
            if not text:
                continue
            backdrop = page.backdrop(run)
            lines.append(
                f"  文字 paint={run.order} x={run.box.x:.2f} y={run.box.y:.2f} "
                f"w={run.box.w:.2f} h={run.box.h:.2f} {run.size_pt:.0f}pt "
                f"色={run.color} 其後方={backdrop} 內容={text!r}"
            )
    return "\n".join(lines)
