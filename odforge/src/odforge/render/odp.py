"""Render a :class:`~odforge.ir.Presentation` into a native ``.odp`` file.

This renderer deliberately hand-writes OpenDocument presentation XML rather
than delegating to ``odfdo`` (whose ``.odp`` support is weak). Every piece of
user-supplied text is passed through :func:`xml.sax.saxutils.escape` — including
attribute values — so that malformed content can never break well-formedness.

Layout is data-driven: :data:`odforge.themes.LAYOUTS` maps each slide layout to
a list of :class:`~odforge.themes.Frame` placeholders, and each frame's ``role``
selects which slide field supplies its content. Empty content frames are
skipped; speaker notes are emitted only when non-empty.
"""

from __future__ import annotations

import functools
import hashlib
import itertools
import math
import os
import re
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass, replace
from pathlib import Path
from xml.sax.saxutils import escape as _xml_escape

from odforge.fontembed import embed_fonts, font_face_decls
from odforge.ir import (
    Branding,
    BulletItem,
    ChartSpec,
    DiagramSpec,
    MetricSpec,
    Presentation,
    ProcessStep,
    Slide,
    TableSpec,
    TimelineEvent,
)
from odforge.media import (
    AssetBlob,
    AssetInput,
    ImageProvider,
    MediaError,
    configured_image_provider,
    normalize_assets,
    resolve_image,
)
from odforge.package import ODP_MIMETYPE, write_odf_package
from odforge.textmetrics import (
    PT_TO_CM,
    estimate_height_cm,
    fact_font_size_pt,
    frame_content_height,
    heading_pt,
    text_width_cm,
)
from odforge.themes import (
    COVER_FRAMES,
    LAYOUTS,
    LEDE_PT,
    LIST_ROLES,
    PAGE_H,
    PAGE_W,
    PANEL_X,
    PLAIN_LAYOUTS,
    SECTION_FRAMES,
    Frame,
    Style,
    Theme,
    get_style,
    resolve_design,
    resolve_style,
    slide_frames,
)

# ---------------------------------------------------------------------------
# Namespace declarations
# ---------------------------------------------------------------------------

_CONTENT_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    # Linked images (Task 13.4 title-page SVG) reference their package part via
    # xlink:href, so the prefix must be declared on office:document-content.
    "xlink": "http://www.w3.org/1999/xlink",
    # Embedded-font weight/style attributes on svg:font-face-uri (fontembed).
    "loext": "urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0",
}

_STYLES_NS = dict(_CONTENT_NS)

_META_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "meta": "urn:oasis:names:tc:opendocument:xmlns:meta:1.0",
    "dc": "http://purl.org/dc/elements/1.1/",
}

_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>'
_MASTER_PAGE_NAME = "Standard"
_PLAIN_MASTER_NAME = "Plain"
_DRAWING_PAGE_STYLE = "dp1"
_SECTION_DRAWING_PAGE_STYLE = "dpsec"
_GRAPHIC_STYLE = "gr1"
_GRADIENT_NAME = "grad-bg"
_NOTES_SIZE_PT = 14

# Task 13.3: master pages, inverted section pages, accent system.
#
# PLAIN_LAYOUTS (layouts rendered "bare": no page-number/footer/kicker furniture)
# is imported from odforge.themes — the single source shared with textmetrics.
# Layouts painted with a full-bleed accent background (inverted pages): the
# section divider and the closing page. The giant chapter-number watermark is
# gated on layout=="section" specifically (closing pages must not display or
# consume a section ordinal — see _page_xml / build_content_xml).
_ACCENT_BG_LAYOUTS = frozenset({"section", "closing"})

# Vertical accent bar flush with a content-page title's left edge.
_ACCENT_BAR_W = 0.18  # cm
# Giant watermark chapter number on section pages.
_SECTION_NUMBER_PT = 96
# The number is drawn as text tinted 15% from accent toward the theme bg —
# ODF 1.2 has no reliable per-run text opacity, so this precomputed blend
# emulates a bg-coloured number at ~15% opacity sitting on the accent fill.
_SECTION_NUMBER_BLEND = 0.15
# (x, y, w, h) cm — upper-right, right edge aligned with the footer line.
_SECTION_NUMBER_BOX = (18.5, 1.0, 8.0, 4.5)

# Task 14.1: quote layout's decorative quotation-mark watermark. Drawn as a big
# glyph tinted 75% from accent toward the bg (i.e. ~25% "opacity") — the same
# reliable precomputed-blend trick the section number uses, since ODF 1.2 has no
# dependable per-run text opacity. Deterministic (a pure function of the theme).
_QUOTE_MARK_GLYPH = "“"  # left double quotation mark
_QUOTE_MARK_PT = 120
_QUOTE_MARK_BLEND = 0.75  # _blend(accent, bg, this) → faint accent tint
_QUOTE_MARK_BOX = (1.8, 1.2, 6.0, 6.0)  # (x, y, w, h) cm — ends above the quote text

# Master-page furniture geometry + style names.
_FOOTER_LINE_Y = 14.72
_FOOTER_LINE_X1 = 1.5
_FOOTER_LINE_X2 = 26.5
_FOOTER_LINE_WIDTH_PT = 0.75
_FURNITURE_Y = 14.76
_FURNITURE_H = 0.7
_PAGENUM_X = 24.0
_PAGENUM_W = 2.5
_KICKER_W = 18.0
_MP_LINE_STYLE = "MPline"
_MP_TRACK_STYLE = "MPtrack"
_MP_FRAME_STYLE = "MPframe"
_MP_PAGENUM_STYLE = "MPpage"
_MP_KICKER_STYLE = "MPkicker"
# LibreOffice Impress only paints master-page shapes when the document declares
# a <draw:layer-set> AND each shape sits on a named layer — without this, plain
# draw:line/draw:frame on the master silently vanish (only recognised
# presentation placeholders render). Furniture goes on "backgroundobjects".
_FURNITURE_LAYER = "backgroundobjects"
_LAYER_SET_XML = (
    "<draw:layer-set>"
    '<draw:layer draw:name="layout"/>'
    '<draw:layer draw:name="background"/>'
    '<draw:layer draw:name="backgroundobjects"/>'
    '<draw:layer draw:name="controls"/>'
    '<draw:layer draw:name="measurelines"/>'
    "</draw:layer-set>"
)

# Semantic-list typography (Task 13.2).
_LIST_STYLE_NAME = "L1"
_BULLET_LINE_HEIGHT = "145%"
_BULLET_MARGIN_BOTTOM = "0.35cm"
_KICKER_LETTER_SPACING = "0.08cm"
_FOOTER_LETTER_SPACING = "0.03cm"
# LIST_ROLES (frame roles whose left-aligned multi-item content renders as a
# semantic bullet list) is imported from odforge.themes — the single source
# shared with textmetrics. Centred frames stay bare centred paragraphs.
# Per bullet level: (text:level, space-before cm, min-label-width cm). Level 2
# indents deeper so nested items read as a sub-list.
_LIST_LEVELS: tuple[tuple[int, float, float], ...] = (
    (1, 0.6, 0.6),
    (2, 1.4, 0.6),
)

# Task 13.4: title-page SVG decoration + card-backed two-col columns.
#
# The decoration is a single engine-generated (never LLM) SVG shared by every
# title page in a deck — one theme per deck means one accent, so one file. It is
# linked (not per-page embedded) from Pictures/ via xlink:href.
_DECO_HREF = "Pictures/deco.svg"
_DECO_LAYOUTS = frozenset({"title"})
# Low-key placement box (x, y, w, h cm) hugging the bottom-right page corner; the
# 4:3 box matches the SVG viewBox so dots stay circular.
_DECO_BOX = (21.0, 10.75, 6.0, 4.5)
# SVG dot-grid geometry (viewBox units): a cols×rows lattice fading out toward
# the top-left, densest (most opaque) at the bottom-right corner.
_DECO_VIEWBOX = (80.0, 60.0)
_DECO_GRID = (6, 4)  # cols, rows
_DECO_MARGIN = 8.0
_DECO_DOT_R = 2.6
_DECO_OPACITY_MIN = 0.06
_DECO_OPACITY_SPAN = 0.30

# Rounded "surface" card sitting behind each two-col column's text. Padded out
# from the text frame so the card reads as a container; emitted before the text
# frame so document order puts the text on top (ODF z-order == document order).
_CARD_ROLES = frozenset({"left", "right"})
_COLUMN_MIN_H = 2.6
# Text sits this far inside a visible column box — headings flush to the
# frame's edge were the "unfinished" tell on comparison pages.
_COLUMN_TEXT_INSET = 0.2
_CARD_PAD = 0.3  # cm the card overhangs its text frame on every side

# Interior metrics for the idea-card grid (cm). Kept as named constants because
# both the card's height and its children's baseline are derived from them —
# a literal in one place and not the other is how the box and its contents
# drifted apart in the first place.
_CARD_PAD_X = 0.45
_CARD_PAD_TOP = 0.32
_CARD_PAD_BOTTOM = 0.38
_CARD_BADGE_H = 0.5
_CARD_BADGE_GAP = 0.15  # badge baseline → title top
_CARD_TITLE_GAP = 0.2  # title → children
_CARD_TITLE_LINE_HEIGHT = 1.2  # matches the title's line-height="120%"
_CARD_CHILD_LINE_HEIGHT = 1.35  # matches the children's line-height="135%"
_CARD_MIN_H = 3.2  # below this a card stops reading as a container
# Visual bodies start just under the heading/lede: the reference decks hang
# their panels from the header rather than floating a short strip mid-page.
# Cards stay sized to their content — an inflated box around one line reads
# as empty, which is the other half of "sparse".
_BODY_TOP_GAP = 0.25
# Share of the leftover height that goes *above* a short body. Dead centre
# floated the block between two bands; flush top left the lower half empty.
# A third above / two thirds below is the optical centre print uses.
_BODY_OPTICAL = 0.45


_PANEL_CARDS = frozenset({"filled", "outlined", "framed"})
_PANEL_FILL = 0.5  # share of the body height a row of visible panels spans
# Two-column text boxes hold running bullets, not a badge/title/detail stack:
# stretched as far, their lower half reads as unfinished.
_COLUMN_FILL = 0.45


# Set per page by _page_xml: when a takeaway band follows the body, the body
# sits at the top of its area and the band closes up under it; centring it
# too left a gap above the cards as well as the one the band just closed.
_BODY_ANCHOR_TOP = False


def _body_y(area: Frame, block_h: float) -> float:
    if _BODY_ANCHOR_TOP:
        return area.y + _BODY_TOP_GAP
    return area.y + max(_BODY_TOP_GAP, (area.h - block_h) * _BODY_OPTICAL)
_CARD_MAX_H = 6.8

# Timeline event cards. The stem is the fixed distance from the axis to a card,
# so a taller row grows away from the line instead of into it.
_TIMELINE_STEM = 0.85
_TIMELINE_TITLE_TOP = 1.05  # label sits above, at +0.30
_TIMELINE_DETAIL_GAP = 0.3
_TIMELINE_DETAIL_LINE_HEIGHT = 1.3  # matches the detail's line-height="130%"
_TIMELINE_PAD_BOTTOM = 0.4
_TIMELINE_MIN_H = 3.0
_TIMELINE_MAX_H = 5.2
_TIMELINE_SINGLE_ROW_BELOW = 11.0  # cm of area height (alternating rows starved details)

# Process step cards, same badge → title → detail stack.
_PROCESS_TITLE_TOP = 1.65
_PROCESS_TITLE_LINE_HEIGHT = 1.15  # matches the title's line-height="115%"
_PROCESS_DETAIL_GAP = 0.35
_PROCESS_DETAIL_LINE_HEIGHT = 1.35
_PROCESS_PAD_BOTTOM = 0.5
_PROCESS_MIN_H = 3.4
_PROCESS_MAX_H = 7.0

# Closing action cards — the same badge → title → detail stack, and the last
# layout still drawing it at a flat height. A three-line action ran past the card
# and printed onto the inverted background in near-black ink: illegible, on the
# one page the audience is left looking at. Measured like the others now, and
# fitted through the shared _fit_stacked_texts loop when even the full area
# cannot hold it, because a smaller word beats an invisible one.
_CLOSING_TITLE_TOP = 1.15
_CLOSING_TITLE_LINE_HEIGHT = 1.2  # matches the title's line-height="120%"
_CLOSING_DETAIL_GAP = 0.3
_CLOSING_DETAIL_LINE_HEIGHT = 1.25
_CLOSING_PAD_BOTTOM = 0.45
_CLOSING_MIN_H = 3.35  # short actions keep the compact card they have today
_CLOSING_MAX_H = 6.6
# Closing bullets carry no IR count cap. Six-plus cards squeezed card_w until
# text_w crossed zero at count ≥ 20 (svg:width="-0.03cm" — invalid geometry);
# the row was unreadable long before that. Five is where a card still holds a
# legible action; anything past it is rendered off.
_CLOSING_MAX_ACTIONS = 5

# Metric cards stack value → label → detail. The offsets used to be constants
# (0.75 / 2.65 / 3.65) that assumed one line each: a 14+-char label (IR allows
# 24) wrapped and printed straight through the detail beneath it. The offsets
# derive from the same measurements the boxes are drawn at now; the gaps are
# small on purpose because _measured_text_h already carries the box's own
# 0.15cm vertical inset (0.1 + 0.15 ≈ the 0.25cm the constants implied).
_METRIC_VALUE_TOP = 0.75
# The dashboard strip is 3.4cm: at 0.75 the label printed on its bottom edge.
_METRIC_COMPACT_VALUE_TOP = 0.4
_METRIC_VALUE_MIN_PT = 18
_METRIC_VALUE_LINE_HEIGHT = 1.0  # pinned on the value style so measure == draw
_METRIC_VALUE_GAP = 0.1
_METRIC_LABEL_LINE_HEIGHT = 1.2  # matches the label's line-height="120%"
_METRIC_LABEL_GAP = 0.05
_METRIC_DETAIL_LINE_HEIGHT = 1.3  # matches the detail's line-height="130%"
_METRIC_PAD_BOTTOM = 0.45

# Font floors shared by every stacked-card layout (_fit_stacked_texts): below
# 12pt a card title stops reading as a title, below 10pt a caption is squint
# territory. Reaching both floors hands over to truncation.
_TITLE_MIN_PT = 14
_DETAIL_MIN_PT = 12

# Diagram edge labels. The chip is sized to its text; the clearance is the
# run of connector that must stay visible either side of it, and decides
# whether the chip can sit on the line at all.
_EDGE_LABEL_PAD = 0.22
_EDGE_LABEL_CLEARANCE = 0.35
_EDGE_LABEL_SIDE_GAP = 0.12
# A label buried under a node (「識蒸」 between two boxes) or across a line is
# worse than none: past this much overlap it is left out and reported.
_LABEL_MAX_OVERLAP = 0.05  # cm²
_LABEL_DROP_MARK = "<!--odforge:label-dropped:{}-->"
_HUB_MIN_EDGE = 0.9  # cm of connector between hub rows

# LibreOffice insets a draw text-box by this much on each side unless the
# graphic style says otherwise, and `gr1` does not. Measuring a wrap against
# the full frame width is therefore optimistic by half a centimetre — enough
# to under-count a line and size a box too short. Every box sized to its own
# text measures against `_wrap_width` instead.
_FRAME_INSET_X = 0.25


def _wrap_width(frame_w: float) -> float:
    """Usable text width inside a frame of ``frame_w`` (cm)."""
    return max(0.1, frame_w - 2 * _FRAME_INSET_X)


# ``estimate_height_cm`` models a line as ``size_pt * line_height``, but ODF's
# ``fo:line-height`` percentage is relative to the *font's own* line height —
# itself ~1.1em for the fonts here — so LibreOffice renders every line taller
# than the model says. Measured off a real render: a 17pt line at
# ``line-height="120%"`` came out 0.79cm, not the modelled 0.72cm. Boxes sized to
# their own text carry the ratio plus the box's top inset, or the last line of a
# three-line card prints past the card edge (on the closing page, onto the
# inverted background in near-black ink — the defect this exists to prevent).
_LO_LINE_SPACING_SLACK = 1.12
_TEXT_BOX_INSET_Y = 0.15
# ``estimate_height_cm``'s own default, named here so a caller that wants it can
# pass it explicitly instead of relying on the argument order.
_LINE_HEIGHT_DEFAULT = 1.45


# ---------------------------------------------------------------------------
# Line layout for shape-drawn text
#
# Card, step, metric, timeline and panel text used to be handed to LibreOffice
# to wrap wherever a glyph ran out of room: "Top-1 ≥ | 90%", "1,200 / 元", a
# lone 「站」 on the last line, and a height the renderer could only guess at.
# The engine now decides the lines itself and writes explicit line breaks, so
# the drawn lines are the measured lines. Each line is laid to a capacity a
# little under the frame's measure, so LibreOffice never wraps one again.
# ---------------------------------------------------------------------------

_LINE_FILL = 0.94  # share of the measure a laid-out line may use
_PARA_GAP = "0.12cm"
_PARA_GAP_CM = 0.12
_NO_LINE_START = set("，。、；：！？）」』】》〉,.;:!?)%％…·→⇒")
_NO_LINE_END = set("（「『【《〈(｜・")
_GLUE_NEXT = set("≥≤<>±~≈＋+−-$＄→⇒×÷–～")


@functools.lru_cache(maxsize=1)
def _segmenter():
    """jieba's word segmenter when installed (``odforge[cjk]``), else None.

    Without words, a CJK line can only break between any two characters —
    「分/鐘」「知/道」 were the most common thing reviewers circled. With it,
    breaks fall between words and a word only splits when it alone is wider
    than the line."""
    try:
        import logging
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            import jieba
        jieba.setLogLevel(logging.WARNING)
        jieba.initialize()
        return jieba
    except Exception:  # optional dependency: fall back to per-character breaks
        return None


@functools.lru_cache(maxsize=1)
def _to_simplified():
    """zhconv's converter, when installed (``odforge[cjk]``)."""
    try:
        import zhconv

        return functools.partial(zhconv.convert, locale="zh-cn")
    except Exception:  # optional dependency
        return None


def _cjk_words(run: str) -> list[str]:
    seg = _segmenter()
    if seg is None:
        return list(run)
    # jieba's dictionary is simplified: 臺灣 came out as 「臺」「灣物種」 and
    # 資料集全數 as 「集全」「數公開」 — the mid-word breaks reviewers circled.
    # Segment a simplified copy and cut the original at the same offsets
    # (only when the conversion is length-preserving, which it is for text).
    convert = _to_simplified()
    probe = convert(run) if convert is not None else run
    if len(probe) != len(run):
        probe = run
    words, i = [], 0
    # HMM on: it is what recognises words the dictionary lacks.
    for word in seg.cut(probe, HMM=True):
        words.append(run[i : i + len(word)])
        i += len(word)
    return _merge_singles(words)


def _merge_singles(words: list[str]) -> list[str]:
    """Join runs of one-character words into chunks of two or three.

    Where the dictionary gives up it emits single characters (「送」「出」,
    「派」「不」「上」), and a break between two of them is almost always
    inside a word — 「同步送 / 出」. A lone single stays alone."""
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        while len(run) > 3:
            take = 2 if len(run) == 4 else 3
            out.append("".join(run[:take]))
            del run[:take]
        if run:
            out.append("".join(run))
        run.clear()

    for word in words:
        if len(word) == 1:
            run.append(word)
        else:
            flush()
            out.append(word)
    flush()
    return out


def _is_cjk_char(ch: str) -> bool:
    # ○/〇 stand in for a name's hidden characters (「劉○○」); counted outside
    # the run, a line could break between the two circles.
    return "\u3400" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff" or ch in "○〇"


_DATE_PART = re.compile(r"\d\s?[年月]\s?$")


def _tokens(text: str) -> list[str]:
    """Break opportunities: CJK words (see :func:`_segmenter`), each
    Latin/number run (kept whole), spaces; closing punctuation rides on the
    token before it, opening punctuation and operators on the token after."""
    raw: list[str] = []
    buf = ""
    cjk = ""
    for ch in text:
        if _is_cjk_char(ch) and ch not in _NO_LINE_START and ch not in _NO_LINE_END:
            if buf:
                raw.append(buf)
                buf = ""
            cjk += ch
            continue
        if cjk:
            raw.extend(_cjk_words(cjk))
            cjk = ""
        if ch.isascii() and not ch.isspace() and ch not in "()":
            buf += ch
            continue
        if buf:
            raw.append(buf)
            buf = ""
        raw.append(ch)
    if cjk:
        raw.extend(_cjk_words(cjk))
    if buf:
        raw.append(buf)
    out: list[str] = []
    for tok in raw:
        if out and (tok in _NO_LINE_START or tok in ("\u00a0",)):
            out[-1] += tok
        elif out and tok.isspace() and out[-1][-1:].isdigit():
            out[-1] += tok  # "1,200 元": a number keeps its unit
        elif out and len(tok) == 1 and not tok.isascii() and out[-1][-1:].isdigit():
            out[-1] += tok  # "41.8分": and so does an unspaced one
        elif out and out[-1].rstrip().endswith("第") and (tok.isspace() or tok[:1].isdigit()):
            out[-1] += tok  # 「第 2 名」: an ordinal keeps its number
        elif out and _DATE_PART.search(out[-1]) and (tok.isspace() or tok[:1].isdigit()):
            out[-1] += tok  # "115 年 3 月 18 日" reads as one date
        elif out and (
            out[-1][-1] in _NO_LINE_END
            or out[-1].rstrip()[-1:] in _GLUE_NEXT
            or out[-1].endswith("\u00a0")
            or (out[-1][-1:].isspace() and out[-1].rstrip()[-1:].isdigit())
        ):
            out[-1] += tok
        else:
            out.append(tok)
    return out


_SOFT_BREAK_AFTER = set("，、；：。！？,;:")
_SOFT_BREAK_LOOKBACK = 4  # tokens


def _split_long_run(tok: str) -> list[str]:
    """A Latin run wider than a line breaks at its hyphens / slashes first."""
    parts, buf = [], ""
    for ch in tok:
        buf += ch
        if ch in "-/_":
            parts.append(buf)
            buf = ""
    if buf:
        parts.append(buf)
    return parts


_PRETTY_LAST_LINE = 0.4  # a last line this full is left alone


def _layout_lines(text: str, size_pt: int, wrap_w: float) -> list[str]:
    """Lay ``text`` out in lines (see :func:`_greedy_lines`), CSS
    ``text-wrap: pretty`` style.

    Greedy fill uses the full measure but can leave a stub last line
    (「用。」「與行動。」). Only then are the last two lines evened out;
    balancing the whole paragraph instead halved every two-line text into a
    six-character column and pulled a first line back to 「通用 App」."""
    lines = _greedy_lines(text, size_pt, wrap_w)
    if len(lines) < 2:
        return lines
    if text_width_cm(lines[-1], size_pt) >= _PRETTY_LAST_LINE * wrap_w * _LINE_FILL:
        return lines
    # Where the last two lines start in the original text (greedy drops the
    # spaces it breaks at, so the lines cannot simply be re-joined).
    pos = 0
    for line in lines[:-2]:
        pos = text.index(line, pos) + len(line)
    tail = text[pos:].strip()
    # Never narrower than the widest unbreakable run: below it greedy has to
    # split the run, and the same line count then passed as "balanced" —
    # 「核定 73, / 000 元」.
    widest = max(
        (text_width_cm(t, size_pt) for t in _tokens(tail) if not t.isspace()),
        default=0.0,
    )
    lo, hi = max(wrap_w * 0.5, min(wrap_w, widest / _LINE_FILL)), wrap_w
    best = lines[-2:]
    for _ in range(8):
        mid = (lo + hi) / 2
        trial = _greedy_lines(tail, size_pt, mid)
        if len(trial) <= 2:
            best, hi = trial, mid
        else:
            lo = mid
    return lines[:-2] + best


def _greedy_lines(text: str, size_pt: int, wrap_w: float) -> list[str]:
    """Greedy line fill over :func:`_tokens`, preferring to break just after
    nearby punctuation rather than inside a word, then an orphan fix: a last
    line of one or two characters pulls tokens down from the line above."""
    if not text:
        return [""]
    cap = max(wrap_w * _LINE_FILL, size_pt * PT_TO_CM * 2)
    lines: list[list[str]] = [[]]

    def width(line: list[str]) -> float:
        return text_width_cm("".join(line), size_pt)

    def new_line() -> None:
        cur = lines[-1]
        # Back up to a soft break (after 「，」 or a space) a few tokens back,
        # so the line ends on punctuation instead of mid-word.
        for k in range(len(cur) - 1, max(0, len(cur) - 1 - _SOFT_BREAK_LOOKBACK), -1):
            # A space is a soft break only between two Latin words: in CJK
            # text the spaces are the auto-inserted ones around 「163.6 萬元」
            # and 「App」, and backing up to them broke 「總成本為 / 163.6…」.
            soft = cur[k - 1][-1] in _SOFT_BREAK_AFTER or (
                cur[k - 1].isspace()
                and k >= 2
                and cur[k - 2][-1:].isascii()
                and cur[k][:1].isascii()
            )
            # Only worth it if the line keeps most of its measure; backing up
            # to the space after "Top-1" left a one-word line.
            if soft and width(cur[:k]) >= 0.6 * cap:
                carry = cur[k:]
                del cur[k:]
                break
        else:
            carry = []
        while cur and cur[-1].isspace():
            cur.pop()
        lines.append([t for t in carry if not (t.isspace() and not lines[-1])])

    queue = _tokens(text)
    while queue:
        tok = queue.pop(0)
        if tok.isspace():
            if lines[-1]:
                lines[-1].append(tok)
            continue
        tw = text_width_cm(tok, size_pt)
        if tw > cap:
            parts = _split_long_run(tok) if tok.isascii() else [tok]
            if len(parts) == 1:
                parts = list(tok)  # last resort: by character
            queue[:0] = parts
            continue
        if lines[-1] and width(lines[-1]) + tw > cap:
            new_line()
            if width(lines[-1]) + tw > cap:
                lines.append([])
        lines[-1].append(tok)
    if len(lines) >= 2:
        last, prev = lines[-1], lines[-2]
        while (
            len("".join(last).strip()) <= 2
            and len(prev) > 2
            and width([prev[-1], *last]) <= cap
        ):
            last.insert(0, prev.pop())
            while prev and prev[-1].isspace():
                prev.pop()
    return ["".join(line).strip() for line in lines if "".join(line).strip()] or [""]


def _line_count(text: str, size_pt: int, wrap_w: float) -> int:
    return sum(len(_layout_lines(p, size_pt, wrap_w)) for p in text.split("\n"))


def _measured_text_h(
    text: str, size_pt: int, wrap_w: float, line_height: float
) -> float:
    """Height (cm) a box must have to actually contain ``text`` in LibreOffice.

    A line is never shorter than the font's own box (see :func:`_lo_line_height`,
    which floors written spacing at 100%)."""
    lines = _line_count(text, size_pt, wrap_w)
    return (
        lines * size_pt * PT_TO_CM * max(line_height, _CJK_LINE_BOX)
        * _LO_LINE_SPACING_SLACK
        + _TEXT_BOX_INSET_Y
        + text.count("\n") * _PARA_GAP_CM
    )
_CARD_CORNER = 0.3  # cm corner radius

# Task 13.5: shape-drawn horizontal bar charts. The "chart" layout that drops
# one onto a page arrives in Task 14.1 — here the renderer only learns to draw a
# ChartSpec into an arbitrary area. Each value becomes one horizontal draw:rect
# whose svg:width is proportional to the value (the largest value fills the
# track); the highlighted bar is filled accent, the rest a muted tone blended
# toward the bg so the one bar that matters reads first. Labels sit in a left
# gutter, value+unit text just past each bar's end.
_CHART_LABEL_W_FRAC = 0.22  # left gutter (fraction of area width) holding labels
_CHART_VALUE_W_FRAC = 0.14  # right gutter reserved so value text fits past bars
_CHART_GAP = 0.2  # cm — breathing space before a bar and before its value text
_CHART_BAR_H_FRAC = 0.42  # bar thickness as a fraction of its row's height
# Floor for the one-line label/value gutter texts. Below this a chart annotation
# stops being readable from the back of a room; whatever still wraps here is
# truncated to its single line instead (the gutter rows sit ~1.4cm apart at 8
# bars — a second line prints straight over the neighbouring row's text).
_CHART_TEXT_MIN_PT = 11
_CHART_BAR_CORNER = 0.08  # cm — bars are barely rounded
_CHART_OTHER_BLEND = 0.55  # non-highlight bars: _blend(muted, bg, this amount)

# Shape-rendered visual layouts.
_VISUAL_CARD_CORNER = 0.28
_VISUAL_GAP = 0.55
_VISUAL_LINE_WIDTH_PT = 1.5
_VISUAL_LINE_BLEND = 0.55
_PROCESS_BADGE_SIZE = 0.76
_TIMELINE_NODE_SIZE = 0.58
_METRIC_ACCENT_H = 0.14
_DIAGRAM_NODE_H = 2.45
_DIAGRAM_NODE_W = 5.2
_DIAGRAM_MIN_NODE_H = 1.8
_DIAGRAM_LINE_BLEND = 0.62
_SOURCE_FRAME = Frame("sources", 1.5, 14.0, 21.8, 0.7, 9)


_OPERATOR_SPACE = re.compile(r"([≥≤<>±~≈])[ ]")


_FULLWIDTH_SPACE = re.compile(
    r"\s+(?=[，。、；：！？）」』】])|(?<=[，。、；：！？（「『【）」』】])\s+"
)


def escape(text: str) -> str:
    """XML-escape text content. The space after a comparison operator is made
    non-breaking (「≥ | 90%」 split across lines reads as two statements), and
    spaces around full-width punctuation are dropped — the glyph carries its
    own spacing, so 「（p75） ，」 set a visible double gap."""
    text = _FULLWIDTH_SPACE.sub("", text)
    return _xml_escape(_OPERATOR_SPACE.sub("\\1\u00a0", text))


def _attr(value: str) -> str:
    """Escape a string for safe use inside a double-quoted XML attribute."""
    return _xml_escape(value, {'"': "&quot;"})


def _cm(value: float) -> str:
    """Format a centimetre measure, dropping a trailing ``.0`` (28.0 -> "28cm")."""
    if value == int(value):
        return f"{int(value)}cm"
    return f"{value}cm"


def _pt(value: float) -> str:
    """Format a point measure, dropping a trailing ``.0`` (2.0 -> "2pt")."""
    if value == int(value):
        return f"{int(value)}pt"
    return f"{value}pt"


def _lighten(hex_color: str, amount: float) -> str:
    """Return ``#RRGGBB`` moved ``amount`` (0..1) of the way toward white."""

    def _mix(channel: int) -> int:
        return round(channel + (255 - channel) * amount)

    r = _mix(int(hex_color[1:3], 16))
    g = _mix(int(hex_color[3:5], 16))
    b = _mix(int(hex_color[5:7], 16))
    return f"#{r:02X}{g:02X}{b:02X}"


def _blend(from_hex: str, to_hex: str, amount: float) -> str:
    """Return ``from_hex`` moved ``amount`` (0..1) of the way toward ``to_hex``.

    Generalises :func:`_lighten` (which only blends toward white) to any target
    colour — used for the section-page watermark number (accent → bg).
    """

    def _mix(a: int, b: int) -> int:
        return round(a + (b - a) * amount)

    r = _mix(int(from_hex[1:3], 16), int(to_hex[1:3], 16))
    g = _mix(int(from_hex[3:5], 16), int(to_hex[3:5], 16))
    b = _mix(int(from_hex[5:7], 16), int(to_hex[5:7], 16))
    return f"#{r:02X}{g:02X}{b:02X}"


def _is_light(hex_color: str) -> bool:
    """True when a colour's perceived luma sits in the upper half (a light ground)."""
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return (0.299 * r + 0.587 * g + 0.114 * b) > 140.0


# How far a full-bleed section/closing fill is deepened toward the ink. A raw
# mid-saturation accent flooded edge-to-edge reads cheap; blending it well toward
# the text colour turns ANY accent into a deep, premium panel (and keeps the
# inverted bg-coloured title high-contrast). Only applied to light-ground decks.
_SECTION_FILL_DEEPEN = 0.62


def _section_fill(theme: Theme) -> str:
    """Fill colour for full-bleed section/closing pages (:data:`_ACCENT_BG_LAYOUTS`).

    Deepens the accent toward the ink on light-ground decks so a mid-saturation
    per-deck accent becomes a rich, editorial panel instead of a flat, cheap
    wash. A dark theme's ground is already deep, so its bright accent is used
    as-is (the intended pop). The inverted title/number colours are unchanged —
    they key off ``theme.bg``, which still contrasts against the deepened fill.
    """
    if _is_light(theme.bg):
        return _blend(theme.accent, theme.text, _SECTION_FILL_DEEPEN)
    return theme.accent


def _uses_gradient_bg(theme: Theme) -> bool:
    """True when this theme paints pages with the background gradient.

    None does any more. The dark preset's ramp spanned ~20 levels of 8-bit
    colour across the page, and every export drew it as visible vertical
    stripes — reviewers called it the plainest sign of an unfinished deck.
    The machinery stays for a future theme with a ramp wide enough to dither.
    """
    return False


def _page_fill_attrs(theme: Theme) -> str:
    """Fill attributes for a drawing-page style (gradient for dark, else solid).

    Used by *both* ``build_styles_xml`` (master page) and ``build_content_xml``
    (per-page ``dp1``): per ODF style resolution a page's own drawing-page style
    overrides the master's, so the gradient must be referenced from content.xml
    too or it would never paint a slide. The ``<draw:gradient>`` definition
    lives in styles.xml ``office:styles`` and is document-scoped, hence
    referenceable from content.xml automatic styles.
    """
    if _uses_gradient_bg(theme):
        return (
            'draw:fill="gradient"'
            f' draw:fill-gradient-name="{_attr(_GRADIENT_NAME)}"'
        )
    return f'draw:fill="solid" draw:fill-color="{_attr(theme.bg)}"'


def _ns_decls(ns: dict[str, str]) -> str:
    """Render ``xmlns:`` declarations for the given namespace mapping."""
    return " ".join(f'xmlns:{prefix}="{uri}"' for prefix, uri in ns.items())


# ---------------------------------------------------------------------------
# Paragraph style cache (de-duplicated on the visual property tuple)
# ---------------------------------------------------------------------------


class _ParagraphStyles:
    """Collects unique paragraph styles, assigning names P1, P2, … on demand.

    The de-duplication key is the visual property tuple. Beyond the original
    ``(size_pt, bold, center, color)`` dimensions it now also carries the
    paragraph-typography knobs Task 13.2 introduced — ``line_height``,
    ``margin_bottom`` (paragraph-properties) and ``letter_spacing``
    (text-properties). They default to ``None`` so existing call sites keep
    producing byte-identical styles (no line-height / margin / spacing emitted).
    """

    def __init__(self, font: str) -> None:
        self._font = font
        self._names: dict[tuple, str] = {}

    def name_for(
        self,
        size_pt: int,
        bold: bool,
        center: bool,
        color: str,
        *,
        line_height: str | None = None,
        margin_bottom: str | None = None,
        letter_spacing: str | None = None,
        font: str | None = None,
    ) -> str:
        key = (
            font or self._font,
            size_pt,
            bold,
            center,
            color,
            line_height,
            margin_bottom,
            letter_spacing,
        )
        name = self._names.get(key)
        if name is None:
            name = f"P{len(self._names) + 1}"
            self._names[key] = name
        return name

    def xml(self) -> str:
        return "".join(
            _paragraph_style_xml(name, *key)
            for key, name in self._names.items()
        )


def _font_size_attrs(size_pt: int) -> str:
    """Three-track font-size attributes (Western + CJK ``*-asian`` + CTL ``*-complex``).

    LibreOffice applies ``fo:font-size`` to Western script only; CJK glyphs need
    the ``*-asian`` variant and complex scripts the ``*-complex`` one, so every
    text style must carry all three or CJK text renders at the wrong size.
    """
    return (
        f' fo:font-size="{size_pt}pt"'
        f' style:font-size-asian="{size_pt}pt"'
        f' style:font-size-complex="{size_pt}pt"'
    )


def _font_weight_attrs(bold: bool) -> str:
    """Three-track ``font-weight`` attributes (Western + ``*-asian`` + ``*-complex``)."""
    if not bold:
        return ""
    return (
        ' fo:font-weight="bold"'
        ' style:font-weight-asian="bold"'
        ' style:font-weight-complex="bold"'
    )


# Noto CJK's own line box (ascent + descent) is 1.448em, and LibreOffice's
# proportional spacing multiplies *that*: "135%" set lines 1.96em apart, not
# the 1.35em the value was chosen for. Invisible while the fonts were missing
# (the fallback face had a ~1.2em box); once the deck carried Noto itself,
# card text ran a line past its card and bullet lists spread out. Values are
# written as intended-em ÷ box, never below 100% (below it LibreOffice clips
# ascenders).
_CJK_LINE_BOX = 1.448


def _lo_line_height(value: str) -> str:
    if not value.endswith("%"):
        return value
    pct = float(value[:-1])
    return f"{max(100, round(pct / _CJK_LINE_BOX))}%"


def _paragraph_style_xml(
    name: str,
    font: str,
    size_pt: int,
    bold: bool,
    center: bool,
    color: str,
    line_height: str | None = None,
    margin_bottom: str | None = None,
    letter_spacing: str | None = None,
) -> str:
    """Build one ``style:family="paragraph"`` automatic style element."""
    align = "center" if center else "start"
    size = _font_size_attrs(size_pt)
    weight = (
        ' fo:font-weight="bold"'
        ' style:font-weight-asian="bold"'
        ' style:font-weight-complex="bold"'
        if bold
        else ""
    )
    para_extra = ""
    if line_height is not None:
        para_extra += f' fo:line-height="{_attr(_lo_line_height(line_height))}"'
    if margin_bottom is not None:
        para_extra += f' fo:margin-bottom="{_attr(margin_bottom)}"'
    spacing = (
        f' fo:letter-spacing="{_attr(letter_spacing)}"'
        if letter_spacing is not None
        else ""
    )
    return (
        f'<style:style style:name="{_attr(name)}" style:family="paragraph">'
        f'<style:paragraph-properties fo:text-align="{align}"{para_extra}'
        # No automatic CJK/Latin gap: it put half-width spaces inside
        # full-width brackets 「（ 1NF 至 3NF ）」 and before 「：」. Authors
        # (and the model) already space mixed runs the Taiwanese way.
        f' style:text-autospace="none"/>'
        f"<style:text-properties{size}{weight}"
        f' fo:color="{_attr(color)}"{spacing} style:font-name="{_attr(font)}"'
        f' style:font-name-asian="{_attr(font)}"/>'
        f"</style:style>"
    )


# ---------------------------------------------------------------------------
# Graphic style cache (de-duplicated shape fills and strokes)
# ---------------------------------------------------------------------------


class _GraphicStyles:
    """Collects unique graphic styles for shapes, naming them G1, G2, … .

    Two kinds of style are de-duplicated, mirroring :class:`_ParagraphStyles`:

    * **fills** keyed on ``(fill_color, opacity)`` — emitted as a solid fill with
      no stroke; ``draw:opacity`` appears only when ``opacity < 1``. Corner radius
      is deliberately *not* part of the key: in ODF it is a ``draw:rect`` element
      attribute (see :func:`_rect_xml`), not a graphic-property, so it never
      varies the style body.
    * **strokes** keyed on ``(color, width_pt)`` — emitted as a solid stroke with
      no fill, for :func:`_line_xml`.
    """

    def __init__(self) -> None:
        self._names: dict[tuple, str] = {}

    def name_for_fill(self, fill: str, opacity: float = 1.0) -> str:
        return self._name(("fill", fill, opacity))

    def name_for_stroke(self, color: str, width_pt: float) -> str:
        return self._name(("stroke", color, width_pt))

    def name_for_fill_stroke(self, fill: str, stroke: str, width_pt: float) -> str:
        return self._name(("fillstroke", fill, stroke, width_pt))

    def _name(self, key: tuple) -> str:
        name = self._names.get(key)
        if name is None:
            name = f"G{len(self._names) + 1}"
            self._names[key] = name
        return name

    def xml(self) -> str:
        return "".join(
            _graphic_style_xml(name, key) for key, name in self._names.items()
        )


def _graphic_style_xml(name: str, key: tuple) -> str:
    """Build one ``style:family="graphic"`` automatic style from a registry key."""
    if key[0] == "fill":
        _, fill, opacity = key
        opacity_attr = "" if opacity >= 1.0 else f' draw:opacity="{opacity * 100:g}%"'
        props = (
            f'draw:fill="solid" draw:fill-color="{_attr(fill)}"'
            f'{opacity_attr} draw:stroke="none"'
        )
    elif key[0] == "fillstroke":
        _, fill, color, width_pt = key
        props = (
            f'draw:fill="solid" draw:fill-color="{_attr(fill)}"'
            ' draw:stroke="solid"'
            f' svg:stroke-color="{_attr(color)}"'
            f' svg:stroke-width="{_pt(width_pt)}"'
        )
    else:  # "stroke"
        _, color, width_pt = key
        props = (
            'draw:fill="none" draw:stroke="solid"'
            f' svg:stroke-color="{_attr(color)}"'
            f' svg:stroke-width="{_pt(width_pt)}"'
        )
    return (
        f'<style:style style:name="{_attr(name)}" style:family="graphic">'
        f"<style:graphic-properties {props}/>"
        f"</style:style>"
    )


# ---------------------------------------------------------------------------
# Text-span style cache (inline runs whose colour/weight differs from the
# enclosing paragraph — e.g. agenda's accent-tinted 01/02 numbers)
# ---------------------------------------------------------------------------


class _SpanStyles:
    """Collects unique ``style:family="text"`` styles, naming them T1, T2, … .

    De-duplicated on ``(size_pt, bold, color)``. Mirrors :class:`_ParagraphStyles`
    but emits ``family="text"`` so the style can dress a ``<text:span>`` inside a
    paragraph without disturbing the surrounding text's colour.
    """

    def __init__(self, font: str) -> None:
        self._font = font
        self._names: dict[tuple, str] = {}

    def name_for(self, size_pt: int, bold: bool, color: str) -> str:
        key = (size_pt, bold, color)
        name = self._names.get(key)
        if name is None:
            name = f"T{len(self._names) + 1}"
            self._names[key] = name
        return name

    def xml(self) -> str:
        return "".join(
            _text_span_style_xml(name, self._font, *key)
            for key, name in self._names.items()
        )


def _text_span_style_xml(name: str, font: str, size_pt: int, bold: bool, color: str) -> str:
    """Build one ``style:family="text"`` automatic style (three-track CJK sizing)."""
    return (
        f'<style:style style:name="{_attr(name)}" style:family="text">'
        f"<style:text-properties{_font_size_attrs(size_pt)}{_font_weight_attrs(bold)}"
        f' fo:color="{_attr(color)}" style:font-name="{_attr(font)}"'
        f' style:font-name-asian="{_attr(font)}"/>'
        f"</style:style>"
    )


# ---------------------------------------------------------------------------
# Shape element builders (draw:rect / draw:line)
# ---------------------------------------------------------------------------


def _rect_xml(
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    fill: str,
    opacity: float = 1.0,
    corner_radius_cm: float = 0.0,
    style_name: str,
) -> str:
    """Build a ``draw:rect`` element referencing the graphic style ``style_name``.

    ``fill`` and ``opacity`` describe the fill that ``style_name`` must provide
    (register it via :class:`_GraphicStyles`); they are accepted so a call site
    reads as a complete rectangle spec and are intentionally *not* repeated on
    the element, because in ODF fill properties live in the referenced graphic
    style. ``draw:corner-radius`` is written only when ``corner_radius_cm > 0``.
    """
    radius = (
        f' draw:corner-radius="{_cm(corner_radius_cm)}"'
        if corner_radius_cm > 0
        else ""
    )
    return (
        f'<draw:rect draw:style-name="{_attr(style_name)}"'
        f' svg:x="{_cm(x)}" svg:y="{_cm(y)}"'
        f' svg:width="{_cm(w)}" svg:height="{_cm(h)}"{radius}/>'
    )


def _line_xml(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    color: str,
    width_pt: float,
    style_name: str,
) -> str:
    """Build a ``draw:line`` element referencing the graphic style ``style_name``.

    ``color`` and ``width_pt`` describe the stroke that ``style_name`` must
    provide (register it via :meth:`_GraphicStyles.name_for_stroke`); like
    :func:`_rect_xml` the visual attributes are carried by the referenced graphic
    style rather than repeated on the element.
    """
    return (
        f'<draw:line draw:style-name="{_attr(style_name)}"'
        f' svg:x1="{_cm(x1)}" svg:y1="{_cm(y1)}"'
        f' svg:x2="{_cm(x2)}" svg:y2="{_cm(y2)}"/>'
    )


def _ellipse_xml(
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    fill: str,
    style_name: str,
) -> str:
    """Build a filled ellipse for process badges and timeline nodes."""
    return (
        f'<draw:ellipse draw:style-name="{_attr(style_name)}"'
        f' svg:x="{_cm(x)}" svg:y="{_cm(y)}"'
        f' svg:width="{_cm(w)}" svg:height="{_cm(h)}"/>'
    )


# ---------------------------------------------------------------------------
# Title-page SVG decoration (engine-generated, deterministic)
# ---------------------------------------------------------------------------


def _svg_decoration(theme: Theme) -> bytes:
    """Return a small, deterministic decorative SVG tinted with ``theme.accent``.

    A ``cols×rows`` lattice of dots that fades out toward the top-left: each dot's
    ``fill-opacity`` grows with its Manhattan distance from the origin so the
    cluster reads densest at the bottom-right — the page corner it is placed in.
    Purely a function of the theme (no randomness / time) for reproducible
    builds. Returns UTF-8 bytes so it drops straight into the ODF package as a
    ``Pictures/*.svg`` part (media-type inferred by :mod:`odforge.package`).
    """
    cols, rows = _DECO_GRID
    vb_w, vb_h = _DECO_VIEWBOX
    step_x = (vb_w - 2 * _DECO_MARGIN) / (cols - 1)
    step_y = (vb_h - 2 * _DECO_MARGIN) / (rows - 1)
    max_dist = (cols - 1) + (rows - 1)
    circles: list[str] = []
    for row in range(rows):
        for col in range(cols):
            cx = _DECO_MARGIN + col * step_x
            cy = _DECO_MARGIN + row * step_y
            opacity = _DECO_OPACITY_MIN + _DECO_OPACITY_SPAN * (
                (col + row) / max_dist
            )
            circles.append(
                f'<circle cx="{cx:g}" cy="{cy:g}" r="{_DECO_DOT_R:g}"'
                f' fill="{_attr(theme.accent)}"'
                f' fill-opacity="{round(opacity, 3):g}"/>'
            )
    svg = (
        f"{_XML_DECL}"
        f'<svg xmlns="http://www.w3.org/2000/svg"'
        f' viewBox="0 0 {vb_w:g} {vb_h:g}"'
        f' width="{vb_w:g}" height="{vb_h:g}">'
        f"{''.join(circles)}"
        f"</svg>"
    )
    return svg.encode("utf-8")


# ---------------------------------------------------------------------------
# Style-driven page furniture (themes.Style)
#
# Each helper below owns exactly one of the Style switches. They are pure
# functions of (style, theme) so a new style stays a data edit.
# ---------------------------------------------------------------------------

# Hairline weights. The consulting rule and metropolis' separator are both
# hairlines (0.4–0.6pt ≈ 0.015–0.02cm); anything heavier reads as a divider bar
# and is the first thing that makes a deck look like a 2009 PowerPoint.
_HAIRLINE_H = 0.045
_SEPARATOR_H = 0.02
# Swiss kicker rule: short, heavier, sits above the heading.
_KICKER_RULE_W = 2.2
_KICKER_RULE_H = 0.12
# Cover rules.
_COVER_RULE_W = 3.2
_COVER_RULE_H = 0.14
_COVER_RULE_GAP = 0.85
_COVER_UNDERRULE_H = 0.03
_COVER_UNDERRULE_GAP = 0.75
_MASTHEAD_Y = 2.2
# Section numerals.
_SECTION_NUMERAL_PT = 120
_SECTION_HUGE_PT = 190
_SECTION_RULE_H = 0.03
_SECTION_DOT = 0.24
# metropolis' progress bar: full-width hairline track at the very bottom.
_PROGRESS_Y = 15.52
_PROGRESS_H = 0.09
# A light-ground numeral has no accent fill to sit on, so it is blended toward
# the page bg far harder than one printed on the accent ground.
_SECTION_NUMBER_LIGHT_BLEND = 0.80
_SECTION_NUMBER_GHOST_BLEND = 0.90


def cover_frames(style: Style) -> list[Frame]:
    """The opening page's frames for this style."""
    return COVER_FRAMES.get(style.cover, COVER_FRAMES["centered"])


# Where each cover composition pins its text block. "top" keeps the style's
# title y; "centre" centres the measured block on the optical centre (the
# report cover anchored its title at 8.6cm and left the top half empty);
# "bottom" keeps the block's foot where the style put it (keynote's lower third).
_COVER_ANCHOR = {"report": "centre", "academic": "centre", "keynote": "bottom"}
_COVER_OPTICAL_Y = 7.1
_COVER_TOP_MIN = 3.6
_COVER_SUB_GAP = 0.45


def _heading_h(text: str, size_pt: int, frame_w: float) -> float:
    """Height of a natively wrapped heading. Headings are not engine-laid-out;
    LibreOffice fills the full measure, which is what :func:`heading_pt` sizes
    against — the card model's line fill would count a one-line title as two."""
    if text_width_cm(text, size_pt) <= frame_w - 0.5:
        lines = 1
    else:
        lines = _line_count(text, size_pt, _wrap_width(frame_w))
    return (
        lines * size_pt * PT_TO_CM * _CJK_LINE_BOX * _LO_LINE_SPACING_SLACK
        + _TEXT_BOX_INSET_Y
    )


def _cover_layout(
    slide: Slide, style: Style, theme: Theme, meta: bool
) -> list[Frame]:
    """The cover's frames sized to what they hold.

    Fixed frames reserved room for a two-line title: a one-line title then left
    a gap above a subtitle parked where line two would have ended, and the
    whole block hung wherever the style had put it. Here the title (and its
    kicker) is measured, the subtitle follows it, and the block is anchored
    per :data:`_COVER_ANCHOR`.
    """
    frames = list(cover_frames(style))
    if meta and not frames[0].center:
        # The title block narrows to make room for the meta card on the right.
        frames = [replace(f, w=min(f.w, _COVER_META_X - f.x - 0.8)) for f in frames]
    title, rest = frames[0], frames[1:]
    title_h = _heading_h(
        slide.title, heading_pt(slide.title, title.w, title.size_pt), title.w
    )
    if slide.kicker:
        title_h += _heading_h(slide.kicker, theme.caption_pt, title.w) - _TEXT_BOX_INSET_Y
    subs = [f for f in rest if f.role == "subtitle"]
    sub_h = (
        _heading_h(slide.subtitle, subs[0].size_pt, subs[0].w)
        if subs and slide.subtitle
        else 0.0
    )
    block_h = title_h + (_COVER_SUB_GAP + sub_h if sub_h else 0.0)
    anchor = _COVER_ANCHOR.get(style.cover, "top")
    top = title.y
    if anchor == "centre":
        top = min(title.y, max(_COVER_TOP_MIN, _COVER_OPTICAL_Y - block_h / 2))
    elif anchor == "bottom":
        foot = max(f.y + f.h for f in frames)
        top = min(title.y, foot - block_h)
    out = [replace(title, y=top, h=max(title_h, 1.0))]
    for f in rest:
        if f.role == "subtitle":
            f = replace(f, y=top + title_h + _COVER_SUB_GAP, h=max(sub_h, 0.9))
        out.append(f)
    return out


def section_frames(style: Style) -> list[Frame]:
    """The divider page's frames for this style (numerals move the title)."""
    return SECTION_FRAMES.get(style.section, LAYOUTS["section"])


def inverted_layouts(style: Style) -> frozenset[str]:
    """Which layouts paint a full-bleed accent ground under this style."""
    inverted = set()
    if style.section == "bleed-numeral":
        inverted.add("section")
    if style.invert_closing:
        inverted.add("closing")
    return frozenset(inverted)


def _cover_is_bleed(style: Style) -> bool:
    """Whether the opening page itself is a full-bleed accent ground."""
    return style.cover_deco == "bleed"


def _fill(x, y, w, h, color, graphics, radius=0.0):
    return _rect_xml(
        x, y, w, h,
        fill=color,
        corner_radius_cm=radius,
        style_name=graphics.name_for_fill(color),
    )


def _cover_deco_xml(
    style: Style,
    theme: Theme,
    graphics: _GraphicStyles,
    frames: list[Frame] | None = None,
) -> str:
    """The opening page's decoration, per ``Style.cover_deco``."""
    frames = frames or cover_frames(style)
    title = frames[0]
    if style.cover_deco == "bleed":
        # Bleed to every edge: the ground itself is the decoration.
        return _fill(0, 0, PAGE_W, PAGE_H, _section_fill(theme), graphics)
    if style.cover_deco == "rule-above":
        return _fill(
            title.x, title.y - _COVER_RULE_GAP,
            _COVER_RULE_W, _COVER_RULE_H, theme.accent, graphics,
        )
    if style.cover_deco == "masthead":
        # Swiss report head: one hairline across the full measure near the top.
        return _fill(
            title.x, _MASTHEAD_Y, PAGE_W - 2 * title.x, _COVER_UNDERRULE_H,
            theme.accent, graphics,
        )
    if style.cover_deco == "rule-under":
        # metropolis draws its rule under the whole title block, full measure.
        bottom = max(frame.y + frame.h for frame in frames)
        # Beside a meta card the rule spans the (narrowed) title block only;
        # full measure it ran straight under the card.
        narrowed = title.w < cover_frames(style)[0].w
        return _fill(
            title.x, bottom + _COVER_UNDERRULE_GAP,
            title.w if narrowed else PAGE_W - 2 * title.x,
            _COVER_UNDERRULE_H, theme.accent, graphics,
        )
    return ""


def _cover_text_bottom(style: Style, frames: list[Frame] | None = None) -> float:
    """The lowest y any cover element occupies, decoration included.

    The byline is placed under this. Taking only the text frames into account
    printed metropolis' full-measure rule straight through the byline.
    """
    bottom = max(frame.y + frame.h for frame in frames or cover_frames(style))
    if style.cover_deco == "rule-under":
        bottom += _COVER_UNDERRULE_GAP + _COVER_UNDERRULE_H
    return bottom


def _title_mark_xml(
    style: Style, frame: Frame, theme: Theme, graphics: _GraphicStyles
) -> str:
    """The mark that identifies a content heading, per ``Style.title_mark``."""
    if style.title_mark == "hairline":
        # Consulting: a hairline under the action title, full content measure.
        return _fill(
            frame.x, frame.y + frame.h + 0.18, frame.w, _HAIRLINE_H,
            theme.accent, graphics,
        )
    if style.title_mark == "separator":
        # metropolis: a 0.4pt separator directly under the frame title, in the
        # muted ink rather than the accent — it separates, it does not shout.
        return _fill(
            frame.x, frame.y + frame.h + 0.12, frame.w, _SEPARATOR_H,
            _blend(theme.muted, theme.bg, 0.45), graphics,
        )
    if style.title_mark == "kicker-rule":
        # 0.42cm above a 0.55cm frame put the rule 0.13cm from the page edge —
        # read as a bar glued to the top, not a mark on the heading.
        return _fill(
            frame.x, max(0.34, frame.y - 0.42), _KICKER_RULE_W, _KICKER_RULE_H,
            theme.accent, graphics,
        )
    if style.title_mark == "bar":
        return _fill(
            frame.x, frame.y, _ACCENT_BAR_W, frame.h, theme.accent, graphics,
        )
    return ""


def _section_mark_xml(
    style: Style,
    ordinal: int | None,
    theme: Theme,
    styles: _ParagraphStyles,
    graphics: _GraphicStyles,
) -> str:
    """The divider page's composition, per ``Style.section``."""
    title = section_frames(style)[0]
    if style.section == "bleed-numeral":
        if ordinal is None:
            return ""
        return _section_number_xml(ordinal, theme, styles, _SECTION_NUMBER_BLEND)
    if style.section == "numeral-left":
        # Consulting divider: a big quiet numeral on the left, title beside it.
        if ordinal is None:
            return ""
        color = _blend(theme.accent, theme.bg, _SECTION_NUMBER_LIGHT_BLEND)
        return _numeral_xml(
            ordinal, 2.0, 5.5, 7.0, 3.8, _SECTION_NUMERAL_PT, color, styles
        )
    if style.section == "numeral-huge":
        # Swiss: an oversized ghosted numeral behind the title.
        if ordinal is None:
            return ""
        color = _blend(theme.accent, theme.bg, _SECTION_NUMBER_GHOST_BLEND)
        return _numeral_xml(
            ordinal, 1.8, 2.3, 13.0, 5.6, _SECTION_HUGE_PT, color, styles
        )
    if style.section == "centered-rules":
        # metropolis section page: the title sits between two full-measure rules.
        rule_w = PAGE_W - 2 * title.x
        color = _blend(theme.muted, theme.bg, 0.4)
        return (
            _fill(title.x, title.y - 0.85, rule_w, _SECTION_RULE_H, color, graphics)
            + _fill(
                title.x, title.y + title.h + 0.55, rule_w, _SECTION_RULE_H,
                color, graphics,
            )
        )
    if style.section == "dot":
        return _fill(
            (PAGE_W - _SECTION_DOT) / 2, title.y - 1.1,
            _SECTION_DOT, _SECTION_DOT, theme.accent, graphics,
            radius=_SECTION_DOT / 2,
        )
    return ""


def _numeral_xml(
    ordinal: int,
    x: float,
    y: float,
    w: float,
    h: float,
    size_pt: int,
    color: str,
    styles: _ParagraphStyles,
) -> str:
    """A large zero-padded section numeral drawn at an explicit box."""
    style_name = styles.name_for(size_pt, True, False, color)
    frame = Frame("section-number", x, y, w, h, size_pt, bold=True)
    return _frame_box_xml(
        frame,
        f'<text:p text:style-name="{_attr(style_name)}">'
        f"{escape(f'{ordinal:02d}')}</text:p>",
    )


# ---------------------------------------------------------------------------
# Cover branding: byline + organisation logo (ir.Branding)
#
# App-owned furniture, drawn outside the LAYOUTS frame loop: the byline and the
# logo belong to the deck, not to any one slide's content model, and neither is
# charged to the layout budget (which measures what the model wrote).
# ---------------------------------------------------------------------------

# Cover: one muted line under the subtitle. The title-page decoration lives at
# (21.0, 10.75) and its dots are faint there, so a centred line at this y clears
# the dense corner of the lattice.
_BYLINE_BOX = (2.0, 0.0, 24.0, 1.0)
# How far under the lowest cover frame the byline sits.
_BYLINE_GAP = 0.55

# (x, y, max_w, max_h) per placement. The cover mark is generous; the in-deck
# marks are deliberately small — a logo repeated on 12 pages is furniture, not a
# statement, and it must never crowd the content frames.
_LOGO_COVER_BOX = (1.8, 1.15, 5.0, 1.6)
# Closing pages carry no footer furniture, so the strip under the action row is
# free. Right-aligned to the same 26.5 the footer line ends at.
_LOGO_CLOSING_RIGHT = 26.5
_LOGO_CLOSING_Y = 14.35
_LOGO_CLOSING_MAX = (3.4, 0.95)
# Section dividers: same corner, but they have no action row above.
_LOGO_SECTION_Y = 14.35
# Content pages: inside the master's footer band, to the LEFT of the page number
# (which starts at _PAGENUM_X = 24.0). Nothing else is drawn in that strip, so a
# mark here can never collide with a content frame.
_LOGO_FOOTER_RIGHT = 23.6
_LOGO_FOOTER_Y = 14.8
_LOGO_FOOTER_MAX = (3.0, 0.6)
# Padding around a logo sitting on its own plate (inverted pages only).
_LOGO_PLATE_PAD = 0.16
_LOGO_PLATE_CORNER = 0.12


@dataclass(frozen=True)
class _LogoAsset:
    """A resolved cover mark: its package part plus its natural pixel size."""

    href: str
    width: int
    height: int

    def fit(self, max_w: float, max_h: float) -> tuple[float, float]:
        """Scale to fit ``max_w`` × ``max_h`` cm, preserving aspect ratio."""
        if self.width <= 0 or self.height <= 0:
            return max_w, max_h
        ratio = self.width / self.height
        w, h = max_w, max_w / ratio
        if h > max_h:
            w, h = max_h * ratio, max_h
        return w, h


def _logo_image_xml(href: str, x: float, y: float, w: float, h: float) -> str:
    """One positioned ``draw:frame`` holding the packaged logo image."""
    return (
        f'<draw:frame draw:style-name="{_GRAPHIC_STYLE}"'
        f' svg:x="{_cm(x)}" svg:y="{_cm(y)}"'
        f' svg:width="{_cm(w)}" svg:height="{_cm(h)}">'
        f'<draw:image xlink:href="{_attr(href)}" xlink:type="simple"'
        f' xlink:show="embed" xlink:actuate="onLoad"/>'
        f"</draw:frame>"
    )


def _logo_xml(
    logo: _LogoAsset,
    layout: str,
    theme: Theme,
    graphics: _GraphicStyles,
) -> str:
    """Draw the logo for one page, or "" when this layout carries no mark.

    Inverted pages (section / closing) paint the accent fill edge to edge, and a
    raster mark is usually dark ink on transparency — on that ground it would
    disappear. Those two get a small ``theme.bg`` plate behind them, which is how
    a real logo lockup is handled on a dark ground anyway.
    """
    if layout == "title":
        x, y, max_w, max_h = _LOGO_COVER_BOX
        w, h = logo.fit(max_w, max_h)
        return _logo_image_xml(logo.href, x, y, w, h)

    if layout in ("closing", "section"):
        max_w, max_h = _LOGO_CLOSING_MAX
        w, h = logo.fit(max_w, max_h)
        y = _LOGO_CLOSING_Y if layout == "closing" else _LOGO_SECTION_Y
        x = _LOGO_CLOSING_RIGHT - w
        plate = _rect_xml(
            x - _LOGO_PLATE_PAD,
            y - _LOGO_PLATE_PAD,
            w + 2 * _LOGO_PLATE_PAD,
            h + 2 * _LOGO_PLATE_PAD,
            fill=theme.bg,
            corner_radius_cm=_LOGO_PLATE_CORNER,
            style_name=graphics.name_for_fill(theme.bg),
        )
        return plate + _logo_image_xml(logo.href, x, y, w, h)

    max_w, max_h = _LOGO_FOOTER_MAX
    w, h = logo.fit(max_w, max_h)
    return _logo_image_xml(logo.href, _LOGO_FOOTER_RIGHT - w, _LOGO_FOOTER_Y, w, h)


def _page_shows_logo(layout: str, placement: str) -> bool:
    """Whether ``layout`` carries the mark under the user's placement choice."""
    if placement == "cover":
        return layout == "title"
    if placement == "cover-closing":
        return layout in ("title", "closing")
    return True


def _byline_xml(
    byline: str,
    theme: Theme,
    styles: _ParagraphStyles,
    style: Style,
    frames: list[Frame] | None = None,
) -> str:
    """The cover's muted署名 line (單位／講者／日期), set under the cover text.

    Its position follows the style's cover frames rather than a fixed y: a
    left-aligned cover wants a left-aligned byline, and the airy ``quiet`` cover
    pushes its subtitle far enough down that a fixed line would have printed on
    top of it.
    """
    title = (frames or cover_frames(style))[0]
    y = _cover_text_bottom(style, frames) + _BYLINE_GAP
    centered = title.center
    x = title.x if not centered else _BYLINE_BOX[0]
    w = title.w if not centered else _BYLINE_BOX[2]
    # On a bleed cover the muted ink is dark-on-dark; the byline reads in the
    # page colour, dimmed toward the ground so it stays secondary to the title.
    colour = (
        _blend(theme.bg, _section_fill(theme), 0.42)
        if _cover_is_bleed(style)
        else theme.muted
    )
    frame = Frame("byline", x, y, w, 1.0, theme.caption_pt, center=centered)
    para_style = styles.name_for(theme.caption_pt, False, centered, colour)
    return _frame_box_xml(
        frame,
        f'<text:p text:style-name="{_attr(para_style)}">{escape(byline)}</text:p>',
    )


_COVER_META_X = 18.0
_COVER_META_W = 8.2


def _cover_meta(slide: Slide, branding: Branding | None) -> list[str]:
    """What the cover's info card lists: the title slide's own bullets
    (單位、講者、日期、期間). The app-owned byline stays verbatim on its own
    line — it is the user's words, not material to re-layout. A nested item
    is a label with its value, so it reads as one ``標題：內容`` row."""
    rows = []
    for item in _coerce_bullet_items(slide.bullets):
        if isinstance(item, tuple):
            text, children = item
            item = f"{text}：{'、'.join(children)}"
        if item.strip():
            rows.append(item)
    return rows[:5]


def _cover_meta_xml(
    rows: list[str],
    style: Style,
    theme: Theme,
    graphics: _GraphicStyles,
    styles: _ParagraphStyles,
    frames: list[Frame] | None = None,
) -> str:
    """The reference cover's right-hand info card: a framed panel of the
    deck's facts, so the opening page is not a title floating in a field."""
    bleed = _cover_is_bleed(style)
    size = min(theme.caption_pt + 1, 14)
    wrap = _wrap_width(_COVER_META_W - 0.7)
    heights = [_measured_text_h(r, size, wrap, 1.2) for r in rows]
    h = 0.35 + sum(heights) + 0.18 * (len(rows) - 1) + 0.3
    # Top-aligned with the cover's title block, so the card and the title
    # read as one composition rather than two things on the page.
    y = min((frames or cover_frames(style))[0].y - 0.3, PAGE_H - 1.6 - h)
    fill = _blend(theme.bg, _section_fill(theme), 0.12) if bleed else _framed_fill(theme)
    ink = theme.text
    rule = _blend(theme.muted, fill, 0.55)
    parts = [
        _rect_xml(
            _COVER_META_X, y, _COVER_META_W, h, fill=fill, corner_radius_cm=0.12,
            style_name=graphics.name_for_fill_stroke(fill, rule, 0.5),
        ),
    ]
    if not bleed:
        # On a bleed ground the accent strip matched the page, not the card,
        # and read as a stray line floating above it.
        parts.append(_fill(_COVER_META_X, y, _COVER_META_W, 0.08, theme.accent, graphics))
    ry = y + 0.35
    for i, (row, row_h) in enumerate(zip(rows, heights, strict=True)):
        if i:
            ry += 0.18
            parts.append(
                _fill(
                    _COVER_META_X + 0.4, ry - 0.08, _COVER_META_W - 0.8, 0.02,
                    rule, graphics,
                )
            )
        parts.append(
            _visual_text_xml(
                row, _COVER_META_X + 0.35, ry, _COVER_META_W - 0.7, row_h, styles,
                size_pt=size, color=ink,
            )
        )
        ry += row_h
    return "".join(parts)


def _cover_meta_line_xml(
    rows: list[str],
    style: Style,
    theme: Theme,
    styles: _ParagraphStyles,
    frames: list[Frame] | None = None,
) -> str:
    """Centred covers carry the same facts as one muted line under the text."""
    y = _cover_text_bottom(style, frames) + 1.3
    frame = Frame("cover-meta", 2.0, y, 24.0, 0.9, theme.caption_pt, center=True)
    ink = _blend(theme.bg, _section_fill(theme), 0.42) if _cover_is_bleed(style) else theme.muted
    pstyle = styles.name_for(theme.caption_pt, False, True, ink)
    return _frame_box_xml(
        frame,
        f'<text:p text:style-name="{_attr(pstyle)}">{escape("　·　".join(rows))}</text:p>',
    )


def _deco_frame_xml() -> str:
    """Build the ``draw:frame`` linking the title-page decoration SVG.

    The frame carries the no-fill/no-stroke ``gr1`` graphic style and holds a
    single ``<draw:image>`` whose ``xlink:href`` points at the ``Pictures/*.svg``
    package part built by :func:`_svg_decoration`.
    """
    x, y, w, h = _DECO_BOX
    return (
        f'<draw:frame draw:style-name="{_GRAPHIC_STYLE}"'
        f' svg:x="{_cm(x)}" svg:y="{_cm(y)}"'
        f' svg:width="{_cm(w)}" svg:height="{_cm(h)}">'
        f'<draw:image xlink:href="{_attr(_DECO_HREF)}"/>'
        f"</draw:frame>"
    )


# ---------------------------------------------------------------------------
# Frame content resolution (role -> slide field)
# ---------------------------------------------------------------------------


def _coerce_bullet_items(items) -> list:
    """Lower ``Slide.bullets`` (``str | BulletItem``) onto the renderer's list
    contract (``str | (text, children)`` — Task 13.2). A plain ``str`` passes
    through unchanged (v1 decks stay byte-identical); a ``BulletItem`` with
    children becomes a ``(text, children)`` tuple; a childless ``BulletItem``
    collapses to its bare text so it renders as a leaf bullet.
    """
    out: list = []
    for it in items:
        if isinstance(it, BulletItem):
            out.append((it.text, list(it.children)) if it.children else it.text)
        else:
            out.append(it)
    return out


def _line_text(item) -> str:
    """The display string for a list/bare item (``str`` or ``(text, children)``)."""
    return item[0] if isinstance(item, tuple) else item


def _role_lines(slide: Slide, role: str) -> list:
    """Return the content items a frame's ``role`` contributes, or [] if empty.

    Bullet-family roles (``bullets``/``items``/``insights``) all draw from
    ``slide.bullets`` and are coerced onto the semantic-list contract; the rest
    are plain single-line text fields.
    """
    if role == "title":
        return [slide.title] if slide.title else []
    if role == "subtitle":
        return [slide.subtitle] if slide.subtitle else []
    if role == "fact":
        return [slide.fact] if slide.fact else []
    if role == "quote":
        return [slide.quote] if slide.quote else []
    if role == "attribution":
        return [slide.attribution] if slide.attribution else []
    if role == "message":
        # closing's message is the slide title (subtitle handled in the branch).
        return [slide.title] if slide.title else []
    if role in ("bullets", "items", "insights"):
        return _coerce_bullet_items(slide.bullets)
    if role == "left":
        return list(slide.left)
    if role == "right":
        return list(slide.right)
    return []


_COLUMN_MIN_PT = 14


def _column_h(
    lines: list, layout: str, theme: Theme, frame: Frame, size: int | None = None
) -> float:
    """Measured height of a two-col / comparison column as LibreOffice lays
    it out: optional bold header line, then list items at the list's line
    height and paragraph gap, wrapped at the indented measure."""
    if not lines:
        return 0.0
    if size is None:
        size = theme.body_pt if layout == "comparison" else frame.size_pt
    h = 0.0
    if layout == "comparison":
        h += _measured_text_h(_line_text(lines[0]), size, _wrap_width(frame.w), 1.2)
        lines = lines[1:]
    item_w = _wrap_width(frame.w - _LIST_LEVELS[0][1] - _LIST_LEVELS[0][2])
    for line in lines:
        h += (
            _measured_text_h(_line_text(line), size, item_w, _LINE_HEIGHT_DEFAULT)
            + 0.35
        )
    return h


def _col_layout(slide: Slide) -> str:
    """A two-col page whose columns both open with a short label is drawn as a
    comparison (label = column header). Models write 「翻修前／翻修後」 as the
    first item of each side and expected a header; set as a bullet it read as
    one more point."""
    if slide.layout != "two-col":
        return slide.layout
    left, right = slide.left, slide.right
    if (
        len(left) >= 2 and len(right) >= 2
        and len(left[0]) <= 8 and len(right[0]) <= 8
        and not left[0].rstrip().endswith(("。", "，", ".", ","))
        and not right[0].rstrip().endswith(("。", "，", ".", ","))
    ):
        return "comparison"
    return slide.layout


def _column_pt(slide: Slide, theme: Theme, frames: list[Frame]) -> int:
    """One text size for both columns: the largest at which the taller column
    still fits its frame (floor :data:`_COLUMN_MIN_PT`). A lede and a takeaway
    band leave the columns ~7.6cm, where four two-line items at 18pt ran
    straight into the band."""
    cols = [f for f in frames if f.role in _CARD_ROLES]
    col_layout = _col_layout(slide)
    start = theme.body_pt if col_layout == "comparison" else cols[0].size_pt
    size = start
    while size > _COLUMN_MIN_PT and any(
        _column_h(_role_lines(slide, f.role), col_layout, theme, f, size) > f.h
        for f in cols
    ):
        size -= 1
    return size


def _frame_box_xml(frame: Frame, inner: str) -> str:
    """Wrap pre-built text-box ``inner`` XML in a positioned ``draw:frame``."""
    return (
        f'<draw:frame draw:style-name="{_GRAPHIC_STYLE}"'
        f' svg:x="{_cm(frame.x)}" svg:y="{_cm(frame.y)}"'
        f' svg:width="{_cm(frame.w)}" svg:height="{_cm(frame.h)}">'
        f"<draw:text-box>{inner}</draw:text-box>"
        f"</draw:frame>"
    )


_HEADING_FILL = 0.97


def _broken_xml(text: str, size_pt: int, frame_w: float) -> str:
    """Escaped heading text, with the engine's own line breaks when it needs
    more than one line. Left to LibreOffice a CJK heading breaks between any
    two characters — 「…年度執 / 行報告」「李老 / 師」 — and leaves a lone
    「秒」 on line two; laid out here it breaks between words, evenly."""
    if text_width_cm(text, size_pt) <= frame_w - 0.5:
        return escape(text)
    # A heading owns the full measure (LibreOffice's 0.5cm inset aside), less
    # a sliver for the width model's error — the card body's line fill would
    # turn a two-line title into three.
    lines = _layout_lines(text, size_pt, (frame_w - 0.5) * _HEADING_FILL / _LINE_FILL)
    return "<text:line-break/>".join(escape(line) for line in lines)


def _frame_xml(
    frame: Frame, lines: list[str], style_name: str, size_pt: int | None = None
) -> str:
    """Build a ``draw:frame`` of bare ``text:p`` paragraphs (non-list content).
    With ``size_pt`` each paragraph is laid out by :func:`_broken_xml`."""
    paragraphs = "".join(
        f'<text:p text:style-name="{_attr(style_name)}">'
        + (
            _broken_xml(_line_text(line), size_pt, frame.w)
            if size_pt is not None
            else escape(_line_text(line))
        )
        + "</text:p>"
        for line in lines
    )
    return _frame_box_xml(frame, paragraphs)


def _image_area_xml(
    slide: Slide,
    frame: Frame,
    theme: Theme,
    graphics: _GraphicStyles,
    styles: _ParagraphStyles,
    resolved: tuple[str, AssetBlob] | None,
) -> str:
    """Render a packaged raster image, or an honest editable placeholder."""

    if slide.image is None:
        return ""

    caption_parts = [part for part in (slide.image.caption, slide.image.credit) if part]
    caption = " · ".join(caption_parts)
    caption_h = 0.72 if caption else 0.0
    image_h = max(0.5, frame.h - caption_h)
    inset = 0.08
    target_x = frame.x + inset
    target_y = frame.y + inset
    target_w = frame.w - inset * 2
    target_h = image_h - inset * 2

    surface_style = graphics.name_for_fill(theme.surface)
    parts = [
        _rect_xml(
            frame.x,
            frame.y,
            frame.w,
            image_h,
            fill=theme.surface,
            corner_radius_cm=0.18,
            style_name=surface_style,
        )
    ]

    if resolved is not None:
        href, blob = resolved
        draw_x, draw_y, draw_w, draw_h = target_x, target_y, target_w, target_h
        if slide.image.fit == "contain":
            image_ratio = blob.width / blob.height
            frame_ratio = target_w / target_h
            if image_ratio > frame_ratio:
                draw_h = target_w / image_ratio
                draw_y += (target_h - draw_h) / 2
            else:
                draw_w = target_h * image_ratio
                draw_x += (target_w - draw_w) / 2
        parts.append(
            f'<draw:frame draw:style-name="{_GRAPHIC_STYLE}"'
            f' svg:x="{_cm(draw_x)}" svg:y="{_cm(draw_y)}"'
            f' svg:width="{_cm(draw_w)}" svg:height="{_cm(draw_h)}">'
            f'<draw:image xlink:href="{_attr(href)}" xlink:type="simple"'
            f' xlink:show="embed" xlink:actuate="onLoad"/>'
            f"</draw:frame>"
        )
    else:
        placeholder_style = styles.name_for(
            theme.caption_pt,
            False,
            True,
            theme.muted,
        )
        placeholder = Frame(
            "image-placeholder",
            target_x + 0.6,
            target_y + target_h / 2 - 0.75,
            max(0.5, target_w - 1.2),
            1.5,
            theme.caption_pt,
            center=True,
        )
        placeholder_text = f"圖片待補\n{slide.image.alt}"
        parts.append(
            _frame_box_xml(
                placeholder,
                "".join(
                    f'<text:p text:style-name="{_attr(placeholder_style)}">'
                    f"{escape(line)}</text:p>"
                    for line in placeholder_text.splitlines()
                ),
            )
        )

    if caption:
        caption_style = styles.name_for(
            theme.caption_pt,
            False,
            False,
            theme.muted,
        )
        caption_frame = Frame(
            "image-caption",
            frame.x,
            frame.y + image_h + 0.12,
            frame.w,
            max(0.45, caption_h - 0.12),
            theme.caption_pt,
        )
        parts.append(_frame_xml(caption_frame, [caption], caption_style))
    return "".join(parts)


# ---------------------------------------------------------------------------
# Task 13.5: shape-drawn horizontal bar charts
# ---------------------------------------------------------------------------


def _fmt_number(value: float) -> str:
    """Format a chart value for display: whole numbers drop the ``.0``
    (42.0 -> "42"), fractions stay compact (3.5 -> "3.5")."""
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,g}"


def _text_line_h_cm(size_pt: int) -> float:
    """Approximate one text line's height in cm for a point size (1.2 leading).

    Lets a bar's label/value be positioned to sit vertically centred on the bar
    without a dedicated vertical-align graphic style — and stays deterministic
    (a pure function of the point size).
    """
    return size_pt / 72.0 * 2.54 * 1.2


# LibreOffice's default text-box top padding plus Noto CJK's 1.45em line box:
# centring on the 1.2-leading estimate above left chart labels and values
# sitting visibly below their bars' centre line.
_TEXT_BOX_PAD_TOP = 0.125


def _centred_text_y(centre_y: float, size_pt: int) -> float:
    """Frame y that puts one line of ``size_pt`` text's ink centre on
    ``centre_y`` as LibreOffice actually lays it out."""
    return centre_y - size_pt * PT_TO_CM * 1.45 / 2 - _TEXT_BOX_PAD_TOP


def _chart_xml(
    chart: ChartSpec,
    area_x: float,
    area_y: float,
    area_w: float,
    area_h: float,
    theme: Theme,
    graphics: _GraphicStyles,
    para_styles: _ParagraphStyles,
) -> str:
    """Render a :class:`~odforge.ir.ChartSpec` as horizontal bars filling an area.

    Each value becomes one ``draw:rect`` whose ``svg:width`` is proportional to
    the value — the largest value fills the available track width, so bar-width
    ratios equal value ratios exactly. The bar named by ``chart.highlight`` is
    filled ``theme.accent``; every other bar takes a muted tone blended toward
    the background (:data:`_CHART_OTHER_BLEND`) so the key bar reads first. Each
    bar carries its label (left gutter) and value+unit text (just past the bar's
    end), both at ``theme.body_pt`` with three-track CJK sizing via
    ``para_styles``. Bars are barely rounded (:data:`_CHART_BAR_CORNER`).

    ``graphics`` / ``para_styles`` are the shared style registries the caller
    later serialises; this builder only *registers* styles and returns the shape
    + text XML (no ``<style:style>`` of its own). Deterministic.

    Zero-value edge: when every value is 0 the bars render at zero width (guarded
    against division by zero) — no data, no bar — while labels/values still show.
    """
    n = len(chart.values)
    max_v = max(chart.values)
    # The label gutter grows to the longest label (to 35% of the area): a
    # fixed 22% held about four CJK characters and cut 「研究助理費」.
    label_w = min(
        area_w * 0.35,
        max(
            area_w * _CHART_LABEL_W_FRAC,
            max(text_width_cm(lb, theme.body_pt) for lb in chart.labels)
            + 2 * _FRAME_INSET_X + _CHART_GAP + theme.body_pt * PT_TO_CM,
        ),
    )
    # One precision for the column ("1.0 秒" beside "1.1 秒", not "1秒"), and a
    # space before a word unit the way the rest of the deck writes it.
    decimals = max(
        (len(f"{v:g}".split(".")[1]) if "." in f"{v:g}" else 0 for v in chart.values),
        default=0,
    )
    if max((abs(v) for v in chart.values), default=0) >= 100:
        decimals = 0  # "108 萬" beside "11.8 萬", not "108.0 萬"
    unit = chart.unit
    if unit and not unit.startswith((" ", "%", "％")):
        unit = " " + unit
    value_texts = [
        (f"{v:,.{decimals}f}" if decimals else _fmt_number(v)) + unit
        for v in chart.values
    ]
    # The value gutter grows to the widest "48000 元" it must hold (up to 30%
    # of the area): a fixed 14% wrapped a five-digit value plus its unit.
    value_w = min(
        area_w * 0.3,
        max(
            area_w * _CHART_VALUE_W_FRAC,
            max(text_width_cm(t, theme.body_pt) for t in value_texts)
            + 2 * _FRAME_INSET_X
            + _CHART_GAP
            + theme.body_pt * PT_TO_CM,  # one em of slack: bold digits run wide
        ),
    )
    track_w = area_w - label_w - value_w
    row_h = area_h / n
    bar_x = area_x + label_w
    # Non-highlight bars share one muted-toned fill (deduped in ``graphics``).
    other_fill = _blend(theme.muted, theme.bg, _CHART_OTHER_BLEND)

    # The gutters hold exactly one text line per row — a wrapping label prints
    # its extra lines straight over the neighbouring rows (6–8 bars leave
    # ~1.4cm per row) and labels/values carry no IR length cap. One size per
    # column, set by the row that needs the smallest, so the column reads as a
    # column; whatever still wraps at the floor is cut to its one line.
    # Fit and cut against the *inset* width (LibreOffice takes 0.25cm each side
    # off the frame) — against the full frame width a "one-line" text still
    # wraps in the real render.
    label_wrap = _wrap_width(label_w - _CHART_GAP)
    label_pt = min(
        (
            fact_font_size_pt(label, label_wrap, theme.body_pt, _CHART_TEXT_MIN_PT)
            for label in chart.labels
        ),
        default=theme.body_pt,
    )
    value_widths = [
        max(
            (area_x + area_w)
            - (bar_x + (track_w * (v / max_v) if max_v > 0 else 0.0) + _CHART_GAP),
            _CHART_GAP,
        )
        for v in chart.values
    ]
    value_pt = min(
        (
            fact_font_size_pt(
                text, _wrap_width(w), theme.body_pt, _CHART_TEXT_MIN_PT
            )
            for text, w in zip(value_texts, value_widths, strict=True)
        ),
        default=theme.body_pt,
    )
    label_line_h = _text_line_h_cm(label_pt)
    value_line_h = _text_line_h_cm(value_pt)

    parts: list[str] = []
    for i, (label, value) in enumerate(zip(chart.labels, chart.values, strict=True)):
        highlighted = i == chart.highlight
        bar_w = track_w * (value / max_v) if max_v > 0 else 0.0
        row_y = area_y + i * row_h
        bar_h = row_h * _CHART_BAR_H_FRAC
        bar_y = row_y + (row_h - bar_h) / 2.0

        fill = theme.accent if highlighted else other_fill
        bar_style = graphics.name_for_fill(fill)
        parts.append(
            _rect_xml(
                bar_x,
                bar_y,
                bar_w,
                bar_h,
                fill=fill,
                corner_radius_cm=_CHART_BAR_CORNER,
                style_name=bar_style,
            )
        )

        # Label in the left gutter (the highlighted row's label is bolded too).
        # Vertically centre the column's one text line on the bar.
        label_style = para_styles.name_for(
            label_pt, highlighted, False, theme.text_color
        )
        label_text = _truncate_to_h(label, label_pt, label_wrap, 1.0, 0.3)
        parts.append(
            _frame_box_xml(
                Frame(
                    "chart-label",
                    area_x,
                    _centred_text_y(bar_y + bar_h / 2.0, label_pt),
                    label_w - _CHART_GAP,
                    label_line_h,
                    label_pt,
                ),
                f'<text:p text:style-name="{_attr(label_style)}">'
                f"{escape(label_text)}</text:p>",
            )
        )

        # Value + unit just past the bar's end; the frame runs to the area edge.
        value_x = bar_x + bar_w + _CHART_GAP
        value_frame_w = value_widths[i]
        value_color = theme.accent if highlighted else theme.muted
        value_style = para_styles.name_for(
            value_pt, highlighted, False, value_color
        )
        # allow_h=0.3 forces the one-line budget regardless of point size.
        value_text = _truncate_to_h(
            value_texts[i], value_pt, _wrap_width(value_frame_w), 1.0, 0.3
        )
        parts.append(
            _frame_box_xml(
                Frame(
                    "chart-value",
                    value_x,
                    _centred_text_y(bar_y + bar_h / 2.0, value_pt),
                    value_frame_w,
                    value_line_h,
                    value_pt,
                ),
                f'<text:p text:style-name="{_attr(value_style)}">'
                f"{escape(value_text)}</text:p>",
            )
        )

    return "".join(parts)


# ---------------------------------------------------------------------------
# Shape-rendered process, timeline, metric and idea-card layouts
# ---------------------------------------------------------------------------


def _balanced_width(
    text: str, size_pt: int, w: float, x: float, center: bool
) -> tuple[float, float]:
    """Narrow a wrapping text box so its lines come out even.

    LibreOffice fills each line greedily, so a card title one character over
    its measure left 「站」 or 「率」 alone on a second line — the defect
    reviewers flagged most. Given the line count the text needs anyway, the
    box is narrowed to that many even lines (plus a one-em allowance for
    glyph-width error). The line count, and so every height measured
    elsewhere, is unchanged; the text itself is untouched.
    """
    em_cm = size_pt * PT_TO_CM
    wrap = _wrap_width(w)
    run = text_width_cm(text, size_pt)
    if run <= wrap or wrap <= 0:
        return w, x
    lines = math.ceil(run / wrap)
    last = run - (lines - 1) * wrap
    # Only an orphan-ish last line is worth fixing, and narrowing is where the
    # estimate's error bites (a line the model thinks fits, LibreOffice may
    # not): so act only on a clearly short tail, with a 1.5-em allowance.
    if last > wrap * 0.3:
        return w, x
    even = run / lines + 1.5 * em_cm
    if even >= wrap:
        return w, x
    new_w = even + (w - wrap)
    return new_w, (x + (w - new_w) / 2 if center else x)


def _visual_text_xml(
    text: str,
    x: float,
    y: float,
    w: float,
    h: float,
    styles: _ParagraphStyles,
    *,
    size_pt: int,
    color: str,
    bold: bool = False,
    center: bool = False,
    font: str | None = None,
    line_height: str | None = None,
) -> str:
    """Render one styled paragraph in a positioned text frame."""
    style_name = styles.name_for(
        size_pt,
        bold,
        center,
        color,
        line_height=line_height,
        font=font,
    )
    if "\n" in text:
        # Separate statements get a little air, or two children read as one
        # run-on paragraph.
        style_name = styles.name_for(
            size_pt, bold, center, color, line_height=line_height, font=font,
            margin_bottom=_PARA_GAP,
        )
    frame = Frame("visual-text", x, y, w, h, size_pt, bold=bold, center=center)
    # One paragraph per "\n" (each card child is its own statement), and
    # inside it the engine's own line breaks (see _layout_lines).
    wrap = _wrap_width(w)
    inner = "".join(
        f'<text:p text:style-name="{_attr(style_name)}">'
        + "<text:line-break/>".join(
            escape(line) for line in _layout_lines(para, size_pt, wrap)
        )
        + "</text:p>"
        for para in text.split("\n")
    )
    return _frame_box_xml(frame, inner)


def _rect_overlap(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    """Area two ``(x, y, w, h)`` rectangles share; ``0.0`` when they miss."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    dx = min(ax + aw, bx + bw) - max(ax, bx)
    dy = min(ay + ah, by + bh) - max(ay, by)
    return dx * dy if dx > 0 and dy > 0 else 0.0


def _edge_label_candidates(
    x1: float, y1: float, x2: float, y2: float, w: float, h: float
) -> list[tuple[float, float, bool]]:
    """Where an edge label could go, best first.

    Two independent freedoms, and the order below spends the cheap one first:
    slide *along* the connector (the label stays with its own line, which is
    what a reader needs to pair them) before stepping further *away* from it.
    The first entry is the placement the renderer has always used, so an
    uncrowded diagram is laid out exactly as before.

    ``True`` marks an on-connector spot — the caller draws the masking chip
    there and nowhere else, so a label that stepped aside leaves the line whole.
    """
    dx, dy = x2 - x1, y2 - y1
    span = (dx * dx + dy * dy) ** 0.5
    norm = span or 1.0
    # Perpendicular unit vector; the two signs are the two sides of the line.
    nx, ny = -dy / norm, dx / norm
    # Beside the line, the chip sits just clear of it — any further and a
    # reader no longer pairs the label with its connector. The clearance is
    # the chip's half-extent *along the normal*: half its height beside a
    # horizontal line, half its width beside a vertical one. (Half the width
    # in every direction lifted a label 1.35cm off a horizontal connector.)
    base_push = abs(nx) * w / 2 + abs(ny) * h / 2 + _EDGE_LABEL_SIDE_GAP

    def at(t: float) -> tuple[float, float]:
        return x1 + dx * t, y1 + dy * t

    # The chip's extent *along* the connector: its width on a horizontal
    # line, its height on a vertical one. Charging the width everywhere
    # refused every label on a hierarchy's short vertical edges.
    along = abs(dx / norm) * w + abs(dy / norm) * h

    def fits_on_line(t: float) -> bool:
        """Does a chip centred at ``t`` still leave a readable run either side?"""
        centre = span * t
        return (
            centre - along / 2 >= _EDGE_LABEL_CLEARANCE
            and centre + along / 2 <= span - _EDGE_LABEL_CLEARANCE
        )

    slots = (0.5, 0.32, 0.68)
    candidates: list[tuple[float, float, bool]] = []
    for t in slots[:1]:
        if fits_on_line(t):
            cx, cy = at(t)
            candidates.append((cx - w / 2, cy - h / 2, True))
    for t in slots[:1]:
        cx, cy = at(t)
        for side in (1.0, -1.0):
            push = base_push * side
            candidates.append((cx + nx * push - w / 2, cy + ny * push - h / 2, False))
    for t in slots[1:]:
        if fits_on_line(t):
            cx, cy = at(t)
            candidates.append((cx - w / 2, cy - h / 2, True))
    # Past 1.7× the chip no longer reads as its line's label — reviewers
    # called those 「floating」; dropping (and reporting) it is better.
    for factor in (1.0, 1.7):
        for t in slots[1:] if factor == 1.0 else slots:
            cx, cy = at(t)
            for side in (1.0, -1.0):
                push = base_push * factor * side
                candidates.append(
                    (cx + nx * push - w / 2, cy + ny * push - h / 2, False)
                )
    return candidates


def _place_edge_label(
    candidates: list[tuple[float, float, bool]],
    w: float,
    h: float,
    taken: list[tuple[float, float, float, float]],
    area: Frame,
) -> tuple[float, float, bool]:
    """Choose where an edge label goes: the first candidate that hits nothing.

    Each edge used to place its label from its own geometry alone, which is
    fine until two connectors run close: a hub's neighbouring spokes put their
    midpoints within a couple of millimetres of each other and the two chips
    printed on top of one another (the design gate caught exactly that — 「左下
    連線標籤『驗證格式』與『回饋修正』彼此重疊」).

    ``taken`` therefore carries the node cards as well as every label already
    placed. The cards matter because they are drawn *after* the labels: a label
    that lands on one is not crowded, it is painted over and gone.

    Candidates are tried in preference order, so an uncrowded diagram keeps the
    placement it has always had. When every candidate collides — a diagram too
    dense for any free spot — the least-overlapping one wins rather than the
    label being dropped: a label nobody can read is still recoverable by
    editing the deck, a label that was never drawn is not.
    """
    best: tuple[float, float, float, bool] | None = None
    for x, y, on_line in candidates:
        # Never leave the diagram's own area, however far the push wanted to go.
        x = min(max(x, area.x), area.x + area.w - w)
        y = min(max(y, area.y), area.y + area.h - h)
        overlap = sum(_rect_overlap((x, y, w, h), other) for other in taken)
        if overlap == 0:
            return x, y, on_line
        if best is None or overlap < best[0]:
            best = (overlap, x, y, on_line)
    assert best is not None  # candidates is never empty (see _diagram_xml)
    if best[0] > _LABEL_MAX_OVERLAP:
        return None
    return best[1], best[2], best[3]


def _trim_to_boxes(
    source: tuple[float, float, float, float],
    target: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    """Clip the centre-to-centre segment between two boxes to their edges.

    Returns the visible span ``(x1, y1, x2, y2)`` — where the line leaves the
    source card to where it meets the target card. Degenerate cases (concentric
    or overlapping boxes) fall back to the centres, which is what the renderer
    drew before and is never worse.
    """
    sx, sy, sw, sh = source
    tx, ty, tw, th = target
    cx1, cy1 = sx + sw / 2, sy + sh / 2
    cx2, cy2 = tx + tw / 2, ty + th / 2
    dx, dy = cx2 - cx1, cy2 - cy1
    span = (dx * dx + dy * dy) ** 0.5
    if span == 0:
        return cx1, cy1, cx2, cy2

    def _exit(w: float, h: float) -> float:
        """Distance from a box's centre to its edge along (dx, dy)."""
        limits = []
        if dx:
            limits.append((w / 2) / abs(dx / span))
        if dy:
            limits.append((h / 2) / abs(dy / span))
        return min(limits) if limits else 0.0

    start, end = _exit(sw, sh), _exit(tw, th)
    if start + end >= span:  # cards touch or overlap: nothing to draw between
        return cx1, cy1, cx2, cy2
    ux, uy = dx / span, dy / span
    return (
        cx1 + ux * start,
        cy1 + uy * start,
        cx2 - ux * end,
        cy2 - uy * end,
    )


def _stacked_card_h(
    title_h: float,
    detail_h: float,
    *,
    title_top: float,
    detail_gap: float,
    pad_bottom: float,
    min_h: float,
    max_h: float,
    available: float,
) -> tuple[float, float]:
    """Height of a badge → title → detail card, and where its detail starts.

    Both numbers derive from the same measured ``title_h``, so the title's box
    can never end above the text it was drawn to hold and the detail can never
    be pinned at an offset the title has already grown past. That single shared
    derivation is the fix for the defect where a two-line timeline heading
    printed straight through the caption beneath it.

    Returns ``(card_h, detail_y)``, both offsets from the card's top edge.
    """
    detail_y = title_top + title_h + (detail_gap if detail_h else 0.0)
    needed = detail_y + detail_h + pad_bottom
    return max(min_h, min(needed, max_h, available)), detail_y


def _truncate_to_h(
    text: str,
    size_pt: int,
    wrap_w: float,
    line_height: float,
    allow_h: float,
) -> str:
    """Cut ``text`` (trailing "…") to the wrapped lines an ``allow_h`` box holds.

    LibreOffice does not clip text to its frame: whatever outgrows the box
    prints straight past it — over the neighbouring card (later card rects
    paint OVER earlier text, ODF z-order being document order) or off the page.
    Once the font floors are reached, a visible ellipsis is the only honest
    option left; ink outside the card is never one.

    Uses the same em-width model as :func:`estimate_height_cm`: ``n`` lines
    hold an em-run of ``n * wrap_w`` cm, so the cut is the longest prefix whose
    run plus the ellipsis still fits that budget. Always keeps at least one
    line — a lone "…" tells the reader nothing.
    """
    # Same line box and the same line layout the drawing uses, so a cut text
    # measures inside ``allow_h`` by construction.
    line_cm = size_pt * PT_TO_CM * max(line_height, _CJK_LINE_BOX)
    max_lines = max(
        1,
        math.floor(
            (allow_h - _TEXT_BOX_INSET_Y) / (_LO_LINE_SPACING_SLACK * line_cm) + 1e-6
        ),
    )
    if _line_count(text, size_pt, wrap_w) <= max_lines:
        return text
    paras = text.split("\n")
    while len(paras) > 1 and _line_count("\n".join(paras), size_pt, wrap_w) > max_lines:
        # Separate statements (card children) go whole: 「…。…」 after a
        # finished sentence read as a glitch. What was dropped is still
        # reported — the fitted text no longer matches the source.
        paras.pop()
        if _line_count("\n".join(paras), size_pt, wrap_w) <= max_lines:
            return "\n".join(paras)
    kept = "\n".join(paras)
    while kept and _line_count(kept + "…", size_pt, wrap_w) > max_lines:
        kept = kept[:-1].rstrip()
    return (kept or text[:1]) + "…"


_GROW_STEPS = 4
_GROW_TITLE_MAX = 24
_GROW_DETAIL_MAX = 17
_GROW_FILL = 0.72
_SPILL_MARK = "<!--odforge:spill-->"
_SPILL_TOLERANCE = 0.35  # cm


@dataclass(frozen=True)
class _FittedStack:
    """What :func:`_fit_stacked_texts` settled on: the (possibly truncated)
    texts, the sizes they fit at, and the shared card geometry they fit into."""

    titles: list
    details: list
    title_pt: int
    detail_pt: int
    title_h: float
    detail_h: float
    card_h: float
    detail_offset: float
    spills: bool = False


def _fit_stacked_texts(
    titles: list,
    details: list,
    *,
    title_pt: int,
    detail_pt: int,
    wrap_w: float,
    title_line_height: float,
    detail_line_height: float,
    title_top: float,
    detail_gap: float,
    pad_bottom: float,
    min_h: float,
    max_h: float,
    available: float,
) -> _FittedStack:
    """Fit a row of badge → title → detail cards so no text outgrows its card.

    The systemic defect this closes: :func:`_stacked_card_h` clamps the card at
    ``max_h`` / ``available`` while the text boxes inside kept their full
    measured heights and offsets — at IR-max CJK lengths the detail printed
    past the card, over the neighbouring card, or off the page. One shared loop
    now guarantees ``detail_offset + detail_h + pad_bottom <= card_h`` through
    three escalating concessions, in order:

    1. step ``title_pt`` down 1pt at a time to :data:`_TITLE_MIN_PT`,
    2. then ``detail_pt`` down to :data:`_DETAIL_MIN_PT`,
    3. then truncate the offending texts with a trailing "…" to the lines that
       fit (:func:`_truncate_to_h` — LibreOffice does not clip, so once the
       fonts have bottomed out truncation is the only honest option left). The
       titles keep as many lines as leave room for one detail line; the
       details keep whatever the titles then leave them.

    One degenerate exception: when even a single title line plus a single
    detail line cannot share the clamped card, both keep their one line and the
    bottom padding absorbs the residue — the ink still stays on the card.
    Text that already fits comes back untouched at its starting sizes, so
    short content keeps today's exact geometry (compact closing cards, even
    card rows).

    Short text in a roomy area goes the other way first: sizes step *up*
    (to :data:`_GROW_TITLE_MAX` / :data:`_GROW_DETAIL_MAX`) while the stack
    stays within :data:`_GROW_FILL` of the space — a few words at 18pt in a
    10cm area was the "dead lower half" every reviewer marked down.
    """
    for _ in range(_GROW_STEPS):
        t_pt, d_pt = title_pt + 1, detail_pt + 1
        if t_pt > _GROW_TITLE_MAX or d_pt > _GROW_DETAIL_MAX:
            break
        th = max(_measured_text_h(t, t_pt, wrap_w, title_line_height) for t in titles)
        dh = max(
            (_measured_text_h(d, d_pt, wrap_w, detail_line_height) for d in details if d),
            default=0.0,
        )
        need = title_top + th + (detail_gap if dh else 0.0) + dh + pad_bottom
        if need > available * _GROW_FILL:
            break
        # Growing is for filling height, never for adding lines: 「串聯 2 個
        # 保育團體」 grown from one line into two read as a six-character measure.
        texts = [(t, title_pt, t_pt) for t in titles] + [
            (d, detail_pt, d_pt) for d in details if d
        ]
        if any(
            _line_count(t, new, wrap_w) > _line_count(t, old, wrap_w)
            for t, old, new in texts
        ):
            break
        title_pt, detail_pt = t_pt, d_pt
    while True:
        title_h = max(
            _measured_text_h(t, title_pt, wrap_w, title_line_height) for t in titles
        )
        detail_h = max(
            (
                _measured_text_h(d, detail_pt, wrap_w, detail_line_height)
                for d in details
                if d
            ),
            default=0.0,
        )
        card_h, detail_offset = _stacked_card_h(
            title_h,
            detail_h,
            title_top=title_top,
            detail_gap=detail_gap,
            pad_bottom=pad_bottom,
            min_h=min_h,
            max_h=max_h,
            available=available,
        )
        if detail_offset + detail_h + pad_bottom <= card_h + 0.01:
            break
        if title_pt > _TITLE_MIN_PT:
            title_pt -= 1
            continue
        if detail_pt > _DETAIL_MIN_PT:
            detail_pt -= 1
            continue
        # Both floors reached: cut the text itself against the clamped card.
        has_detail = any(details)
        reserve = (
            detail_gap
            + _measured_text_h("字", detail_pt, wrap_w, detail_line_height)
            if has_detail
            else 0.0
        )
        title_allow = card_h - title_top - pad_bottom - reserve
        titles = [
            _truncate_to_h(t, title_pt, wrap_w, title_line_height, title_allow)
            for t in titles
        ]
        title_h = max(
            _measured_text_h(t, title_pt, wrap_w, title_line_height) for t in titles
        )
        # Mirrors _stacked_card_h's detail_y with the truncated title height.
        detail_offset = title_top + title_h + (detail_gap if has_detail else 0.0)
        detail_allow = card_h - detail_offset - pad_bottom
        details = [
            _truncate_to_h(d, detail_pt, wrap_w, detail_line_height, detail_allow)
            if d
            else d
            for d in details
        ]
        detail_h = max(
            (
                _measured_text_h(d, detail_pt, wrap_w, detail_line_height)
                for d in details
                if d
            ),
            default=0.0,
        )
        # Re-derive the card from the truncated text: needed can only have
        # shrunk, so the clamp cannot re-trigger, and the min-height floor
        # keeps a short row reading as cards.
        card_h, detail_offset = _stacked_card_h(
            title_h,
            detail_h,
            title_top=title_top,
            detail_gap=detail_gap,
            pad_bottom=pad_bottom,
            min_h=min_h,
            max_h=max_h,
            available=available,
        )
        break
    return _FittedStack(
        titles,
        details,
        title_pt,
        detail_pt,
        title_h,
        detail_h,
        card_h,
        detail_offset,
        # Even one title line and one detail line did not fit the clamped
        # card: the text will run past it. Said out loud (see _SPILL_MARK),
        # because nothing was cut for the ellipsis check to find.
        # The measure is deliberately generous (line-spacing slack + box
        # inset on every text), so only a clear overrun counts — a 0.05cm
        # tolerance flagged every dashboard strip that visibly fit.
        spills=detail_offset + detail_h + pad_bottom > card_h + _SPILL_TOLERANCE,
    )


_RULED_H = 0.09
_RULE_INK_H = 0.035
_OUTLINE_PT = 0.75


def _framed_fill(theme: Theme) -> str:
    return _lighten(theme.bg, 0.7) if _is_light(theme.bg) else theme.surface


def _visual_card_xml(
    x: float,
    y: float,
    w: float,
    h: float,
    theme: Theme,
    graphics: _GraphicStyles,
    card: str = "filled",
) -> str:
    """Render the container behind one visual-layout item, per ``Style.card``.

    ``filled`` is the rounded surface every style used to share; the others are
    what make two styles two documents rather than two colourways: a consulting
    deck marks items with a top accent rule, a Beamer deck frames them, a Swiss
    deck uses an ink hairline, and a minimal deck draws no container at all.
    """
    if card == "ruled":
        return _fill(x, y, w, _RULED_H, theme.accent, graphics)
    if card == "rule-ink":
        return _fill(x, y, w, _RULE_INK_H, theme.text, graphics)
    if card == "outlined":
        color = _blend(theme.muted, theme.bg, 0.35)
        return _rect_xml(
            x, y, w, h,
            fill="none",
            corner_radius_cm=0.12,
            style_name=graphics.name_for_stroke(color, _OUTLINE_PT),
        )
    if card == "plain":
        return ""
    if card == "framed":
        # White card, hairline frame: the reference deck's container. On a
        # dark ground "white" is the surface tone.
        fill = _framed_fill(theme)
        return _rect_xml(
            x, y, w, h,
            fill=fill,
            corner_radius_cm=0.1,
            style_name=graphics.name_for_fill_stroke(
                fill, _blend(theme.muted, theme.bg, 0.62), 0.5
            ),
        )
    style_name = graphics.name_for_fill(theme.surface)
    return _rect_xml(
        x,
        y,
        w,
        h,
        fill=theme.surface,
        corner_radius_cm=_VISUAL_CARD_CORNER,
        style_name=style_name,
    )


def _process_xml(
    steps: list[ProcessStep],
    area: Frame,
    theme: Theme,
    graphics: _GraphicStyles,
    styles: _ParagraphStyles,
    card: str = "filled",
) -> str:
    """Render 2–5 connected process cards with numbered accent badges."""
    n = len(steps)
    gap = _VISUAL_GAP
    card_w = (area.w - gap * (n - 1)) / n

    # Containers need their inset; a rule or bare column does not, and the
    # 0.48cm each side it kept wrapped a step detail every ~9 characters.
    inset = 0.48 if card in ("filled", "outlined") else 0.12
    text_w = card_w - 2 * inset
    # Five 24-char CJK titles with 56-char details need ≈13cm of stack against
    # a 7cm card clamp — the shared fit loop shrinks, then truncates, so the
    # detail can no longer print past the page bottom.
    fitted = _fit_stacked_texts(
        [s.title for s in steps],
        [s.detail for s in steps],
        title_pt=min(theme.body_pt, 17),
        detail_pt=min(theme.caption_pt, 13),
        wrap_w=_wrap_width(text_w),
        title_line_height=_PROCESS_TITLE_LINE_HEIGHT,
        detail_line_height=_PROCESS_DETAIL_LINE_HEIGHT,
        title_top=_PROCESS_TITLE_TOP,
        detail_gap=_PROCESS_DETAIL_GAP,
        pad_bottom=_PROCESS_PAD_BOTTOM,
        min_h=_PROCESS_MIN_H,
        max_h=_PROCESS_MAX_H,
        available=area.h - 0.8,
    )
    card_h, detail_offset = fitted.card_h, fitted.detail_offset
    if card in _PANEL_CARDS:
        card_h = max(card_h, (area.h - 0.8) * _PANEL_FILL)
        card_y = area.y + _BODY_TOP_GAP
    else:
        card_y = _body_y(area, card_h)
    badge_y = card_y + 0.55
    badge_center_y = badge_y + _PROCESS_BADGE_SIZE / 2

    parts: list[str] = []
    line_color = _blend(theme.accent, theme.bg, _VISUAL_LINE_BLEND)
    line_style = graphics.name_for_stroke(line_color, _VISUAL_LINE_WIDTH_PT)
    # The connector runs only across the gaps between cards. One line from
    # the first badge to the last was hidden by filled cards but printed
    # straight through outlined / framed / ruled ones and their borders.
    for i in range(n - 1):
        right = area.x + i * (card_w + gap) + card_w
        parts.append(
            _line_xml(
                right,
                badge_center_y,
                right + gap,
                badge_center_y,
                color=line_color,
                width_pt=_VISUAL_LINE_WIDTH_PT,
                style_name=line_style,
            )
        )

    badge_style = graphics.name_for_fill(theme.accent)
    for i, _step in enumerate(steps):
        card_x = area.x + i * (card_w + gap)
        parts.append(_visual_card_xml(card_x, card_y, card_w, card_h, theme, graphics, card))
        parts.append(
            _ellipse_xml(
                card_x + 0.48,
                badge_y,
                _PROCESS_BADGE_SIZE,
                _PROCESS_BADGE_SIZE,
                fill=theme.accent,
                style_name=badge_style,
            )
        )
        parts.append(
            _visual_text_xml(
                str(i + 1),
                card_x + 0.43,
                _centred_text_y(badge_y + _PROCESS_BADGE_SIZE / 2, theme.caption_pt),
                _PROCESS_BADGE_SIZE + 0.1,
                0.48,
                styles,
                size_pt=theme.caption_pt,
                color=theme.bg,
                bold=True,
                center=True,
            )
        )
        parts.append(
            _visual_text_xml(
                fitted.titles[i],
                card_x + inset,
                card_y + _PROCESS_TITLE_TOP,
                text_w,
                fitted.title_h,
                styles,
                size_pt=fitted.title_pt,
                color=theme.text,
                bold=True,
                font=theme.font_display,
                line_height="115%",
            )
        )
        if fitted.details[i]:
            parts.append(
                _visual_text_xml(
                    fitted.details[i],
                    card_x + inset,
                    card_y + detail_offset,
                    text_w,
                    max(0.4, card_h - detail_offset - _PROCESS_PAD_BOTTOM),
                    styles,
                    size_pt=fitted.detail_pt,
                    color=theme.muted,
                    line_height="135%",
                )
            )
    return (_SPILL_MARK if fitted.spills else "") + "".join(parts)


def _timeline_xml(
    events: list[TimelineEvent],
    area: Frame,
    theme: Theme,
    graphics: _GraphicStyles,
    styles: _ParagraphStyles,
    card: str = "filled",
) -> str:
    """Render a horizontal timeline with alternating event cards."""
    n = len(events)
    # Alternating cards need two rows of room around the axis. Once a lede and
    # a takeaway band have taken their share of the page, each row got < 3cm and
    # the fit loop was cutting every detail to an ellipsis — so a short area
    # hangs every card below an axis drawn near its top instead.
    single_row = area.h < _TIMELINE_SINGLE_ROW_BELOW
    line_y = area.y + 0.45 if single_row else area.y + area.h / 2
    slot_w = area.w / n
    card_w = min(slot_w - 0.35, 5.4)

    # Size the row to the tallest event, then hang the cards off the axis by a
    # fixed stem: cards above it grow *upward* so a taller row can never reach
    # down and collide with the line it is supposed to hang from.
    text_w = card_w - 0.7
    below = area.y + area.h - (line_y + _TIMELINE_STEM)
    room = (below if single_row else min(line_y - _TIMELINE_STEM - area.y, below)) - 0.1
    # The row's `room` clamp used to cap the card while detail_offset kept the
    # unclamped title height — a top-row detail then landed exactly on the
    # bottom-row cards and was painted over by them. The shared fit loop keeps
    # the stack inside the clamp instead.
    fitted = _fit_stacked_texts(
        [e.title for e in events],
        [e.detail for e in events],
        title_pt=min(theme.body_pt, 17),
        detail_pt=min(theme.caption_pt, 12),
        wrap_w=_wrap_width(text_w),
        title_line_height=_LINE_HEIGHT_DEFAULT,
        detail_line_height=_TIMELINE_DETAIL_LINE_HEIGHT,
        title_top=_TIMELINE_TITLE_TOP,
        detail_gap=_TIMELINE_DETAIL_GAP,
        pad_bottom=_TIMELINE_PAD_BOTTOM,
        min_h=_TIMELINE_MIN_H,
        max_h=_TIMELINE_MAX_H,
        available=max(_TIMELINE_MIN_H, room),
    )
    card_h, detail_offset = fitted.card_h, fitted.detail_offset
    if single_row and card in _PANEL_CARDS:
        card_h = max(card_h, (area.y + area.h - (line_y + _TIMELINE_STEM)) * _PANEL_FILL)
    top_y = max(area.y + 0.1, line_y - _TIMELINE_STEM - card_h)
    bottom_y = line_y + _TIMELINE_STEM
    line_color = _blend(theme.accent, theme.bg, _VISUAL_LINE_BLEND)
    line_style = graphics.name_for_stroke(line_color, _VISUAL_LINE_WIDTH_PT)
    node_style = graphics.name_for_fill(theme.accent)

    first_x = area.x + slot_w / 2
    last_x = area.x + area.w - slot_w / 2
    parts: list[str] = [
        _line_xml(
            first_x,
            line_y,
            last_x,
            line_y,
            color=line_color,
            width_pt=_VISUAL_LINE_WIDTH_PT,
            style_name=line_style,
        )
    ]
    for i, event in enumerate(events):
        center_x = area.x + slot_w * (i + 0.5)
        card_x = center_x - card_w / 2
        above = i % 2 == 0 and not single_row
        card_y = top_y if above else bottom_y
        stem_y = card_y + card_h if above else card_y
        parts.append(
            _line_xml(
                center_x,
                line_y,
                center_x,
                stem_y,
                color=line_color,
                width_pt=_VISUAL_LINE_WIDTH_PT,
                style_name=line_style,
            )
        )
        parts.append(_visual_card_xml(card_x, card_y, card_w, card_h, theme, graphics, card))
        parts.append(
            _ellipse_xml(
                center_x - _TIMELINE_NODE_SIZE / 2,
                line_y - _TIMELINE_NODE_SIZE / 2,
                _TIMELINE_NODE_SIZE,
                _TIMELINE_NODE_SIZE,
                fill=theme.accent,
                style_name=node_style,
            )
        )
        parts.append(
            _visual_text_xml(
                # The label box is one line tall and the title starts right
                # under it — a wrapping label prints through the title, so it
                # keeps one line and truncates (dates/quarter tags, not prose).
                _truncate_to_h(
                    event.label, theme.caption_pt, _wrap_width(card_w - 0.7),
                    1.0, 0.55,
                ),
                card_x + 0.35,
                card_y + 0.3,
                card_w - 0.7,
                0.55,
                styles,
                size_pt=theme.caption_pt,
                color=theme.accent,
                bold=True,
            )
        )
        parts.append(
            _visual_text_xml(
                fitted.titles[i],
                card_x + 0.35,
                card_y + _TIMELINE_TITLE_TOP,
                text_w,
                fitted.title_h,
                styles,
                size_pt=fitted.title_pt,
                color=theme.text,
                bold=True,
                font=theme.font_display,
            )
        )
        if fitted.details[i]:
            parts.append(
                _visual_text_xml(
                    fitted.details[i],
                    card_x + 0.35,
                    card_y + detail_offset,
                    text_w,
                    max(0.4, card_h - detail_offset - _TIMELINE_PAD_BOTTOM),
                    styles,
                    size_pt=fitted.detail_pt,
                    color=theme.muted,
                    line_height="130%",
                )
            )
    return (_SPILL_MARK if fitted.spills else "") + "".join(parts)


def _metrics_xml(
    metrics: list[MetricSpec],
    area: Frame,
    theme: Theme,
    graphics: _GraphicStyles,
    styles: _ParagraphStyles,
    card: str = "filled",
    compact: bool = False,
    *,
    flush: float | None = None,
) -> str:
    """Render 2–4 metrics as a balanced one- or two-row card grid.

    The value → label → detail y-offsets used to be constants (0.75/2.65/3.65)
    that assumed one line each: a 14+-char label (IR allows 24) wrapped and
    printed straight through the detail. The offsets derive from the same
    measurements the boxes are drawn at now, and the label/detail pair fits
    through the shared loop inside the fixed card the grid assigns.
    """
    # One row, always. A 2×2 grid stacked two rows of big numbers into the
    # dashboard's 3.6cm strip — they printed over each other — and on the
    # metrics page it split four peers into a hierarchy they do not have.
    n = len(metrics)
    cols = n
    gap = _VISUAL_GAP
    card_w = (area.w - gap * (cols - 1)) / cols
    accent_style = graphics.name_for_fill(theme.accent)

    text_w = card_w - 0.9
    # Fit the value against the *inset* width — fed the raw frame width, an
    # 18-char value kept a size at which LibreOffice actually wrapped it.
    wrap_w = _wrap_width(text_w)
    value_cap = min(theme.display_pt, 28 if compact else 44 if n <= 3 else 36)
    # Floor below h1: "12,000 張" in a quarter-width card wraps at 28pt, and a
    # two-line number reads as two numbers. One size for the row, so the
    # values read as peers rather than as a big one and a small one.
    row_pt = min(
        # Sized to the share of the measure a laid-out line may use, or the
        # line layout breaks the "one-line" value it was just sized for.
        fact_font_size_pt(m.value, wrap_w * _LINE_FILL, value_cap, _METRIC_VALUE_MIN_PT)
        for m in metrics
    )
    # A value never wraps: "<300ms" with its "s" on the label line read as a
    # broken number. Below the floor the row keeps shrinking, to 12pt.
    while row_pt > 12 and any(_line_count(m.value, row_pt, wrap_w) > 1 for m in metrics):
        row_pt -= 1
    value_sizes = [row_pt for _ in metrics]
    # Row-shared offsets from the tallest value, so labels align across cards.
    value_h = max(
        _measured_text_h(m.value, size, wrap_w, _METRIC_VALUE_LINE_HEIGHT)
        for m, size in zip(metrics, value_sizes, strict=True)
    )
    value_top = _METRIC_COMPACT_VALUE_TOP if compact else _METRIC_VALUE_TOP
    label_top = value_top + value_h + _METRIC_VALUE_GAP
    # Cards are sized to what they hold (value + label + detail), not stretched
    # to the area: a 7cm box around one number and a label was most of what
    # read as "empty". The fit loop concedes fonts, then text, never the page.
    # The dashboard's stat strip is a row of headline numbers over a card
    # grid: a detail line there had 0.3cm to live in and printed onto the
    # cards below, so the strip draws value + label only.
    fitted = _fit_stacked_texts(
        [m.label for m in metrics],
        ["" if compact else m.detail for m in metrics],
        title_pt=min(theme.body_pt, 17),
        detail_pt=min(theme.caption_pt, 13),
        wrap_w=wrap_w,
        title_line_height=_METRIC_LABEL_LINE_HEIGHT,
        detail_line_height=_METRIC_DETAIL_LINE_HEIGHT,
        title_top=label_top,
        detail_gap=_METRIC_LABEL_GAP + 0.1,
        pad_bottom=_METRIC_PAD_BOTTOM,
        min_h=min(area.h, 3.4),
        # The ceiling is the area, not a design cap: an IR-max value wraps to
        # three lines in a quarter-width card, and a lower cap pushed the label
        # below the card it belongs to.
        max_h=area.h,
        available=area.h,
    )
    card_h = fitted.card_h
    if flush is not None:
        card_h = max(card_h, min(area.h, flush))  # level with the panel beside it
        start_y = area.y
    elif card in _PANEL_CARDS and not compact:
        card_h = max(card_h, (area.h - _BODY_TOP_GAP) * 0.5)
        start_y = area.y + _BODY_TOP_GAP
    else:
        start_y = area.y if compact else _body_y(area, card_h)

    parts: list[str] = []
    for i, metric in enumerate(metrics):
        card_x = area.x + i * (card_w + gap)
        card_y = start_y
        parts.append(_visual_card_xml(card_x, card_y, card_w, card_h, theme, graphics, card))
        if card == "filled":
            parts.append(
                _rect_xml(
                    card_x,
                    card_y,
                    card_w,
                    _METRIC_ACCENT_H,
                    fill=theme.accent,
                    style_name=accent_style,
                )
            )
        parts.append(
            _visual_text_xml(
                metric.value,
                card_x + 0.45,
                card_y + value_top,
                text_w,
                value_h,
                styles,
                size_pt=value_sizes[i],
                color=theme.accent,
                bold=True,
                font=theme.font_display,
                line_height="100%",
            )
        )
        parts.append(
            _visual_text_xml(
                fitted.titles[i],
                card_x + 0.45,
                card_y + label_top,
                text_w,
                fitted.title_h,
                styles,
                size_pt=fitted.title_pt,
                color=theme.text,
                bold=True,
                line_height="120%",
            )
        )
        if fitted.details[i]:
            parts.append(
                _visual_text_xml(
                    fitted.details[i],
                    card_x + 0.45,
                    card_y + fitted.detail_offset,
                    text_w,
                    max(0.4, card_h - fitted.detail_offset - _METRIC_PAD_BOTTOM),
                    styles,
                    size_pt=fitted.detail_pt,
                    color=theme.muted,
                    line_height="130%",
                )
            )
    return (_SPILL_MARK if fitted.spills and not compact else "") + "".join(parts)


_TABLE_PAD_Y = 0.12
_TABLE_PAD_X = 0.3
_TABLE_LINE_HEIGHT = 1.3
_TABLE_HAIRLINE = 0.02


def _table_geometry(table: TableSpec, area: Frame, theme: Theme):
    """Column widths, the settled body size, and every row's height.

    Widths follow each column's longest text (floored so a one-word column
    never collapses); the body steps down 1pt at a time until the whole table
    fits the area. Shared by the renderer and the budget warning so both
    reason about one fit.
    """
    cols = len(table.columns)
    probe_pt = 14
    weights = [
        max(
            3.0,
            max(
                text_width_cm(t, probe_pt)
                for t in [table.columns[c], *(row[c] for row in table.rows)]
            ),
        )
        for c in range(cols)
    ]
    # Cap any single column at half the table so a long cell wraps instead of
    # starving its neighbours.
    weights = [min(w, sum(weights) * 0.5) for w in weights]
    total = sum(weights)
    widths = [area.w * w / total for w in weights]
    header_pt = min(theme.caption_pt, 13)
    # Every column is at least wide enough for its header on one line: a
    # proportional split gave a short first column "評估軌道" in three lines.
    pad = 2 * (_TABLE_PAD_X + _FRAME_INSET_X) + 0.15
    floors = [text_width_cm(label, header_pt) + pad for label in table.columns]
    # Better still, every cell on one line (CSS auto table layout's
    # min-content): 「溫控＋品項紀錄」 wrapped mid-phrase in a proportional
    # column while its neighbour had centimetres to spare. Body text is bold
    # in a highlighted row, so the floor charges a bold margin.
    body_probe = min(theme.body_pt, 17)
    cell_floors = [
        max(
            floors[c],
            max(
                (text_width_cm(row[c], body_probe) * 1.04 + pad for row in table.rows),
                default=0.0,
            ),
        )
        for c in range(cols)
    ]
    if sum(cell_floors) <= area.w:
        floors = cell_floors
    if sum(floors) <= area.w:
        for _ in range(cols):
            short = [i for i in range(cols) if widths[i] < floors[i] - 1e-6]
            if not short:
                break
            need = sum(floors[i] - widths[i] for i in short)
            spare = [i for i in range(cols) if i not in short]
            spare_w = sum(widths[i] - floors[i] for i in spare)
            if spare_w <= 0:
                break
            for i in short:
                widths[i] = floors[i]
            for i in spare:
                widths[i] -= need * (widths[i] - floors[i]) / spare_w
    body_pt = min(theme.body_pt, 17)

    def heights(pt: int) -> tuple[float, list[float]]:
        head = max(
            _measured_text_h(h, header_pt, _wrap_width(w - 2 * _TABLE_PAD_X), 1.2)
            for h, w in zip(table.columns, widths, strict=True)
        ) + 2 * _TABLE_PAD_Y
        rows = [
            max(
                _measured_text_h(
                    cell, pt, _wrap_width(w - 2 * _TABLE_PAD_X), _TABLE_LINE_HEIGHT
                )
                for cell, w in zip(row, widths, strict=True)
            )
            + 2 * _TABLE_PAD_Y
            for row in table.rows
        ]
        return head, rows

    head_h, row_hs = heights(body_pt)
    while head_h + sum(row_hs) > area.h and body_pt > _DETAIL_MIN_PT:
        body_pt -= 1
        head_h, row_hs = heights(body_pt)
    return widths, header_pt, body_pt, head_h, row_hs


def _table_xml(
    table: TableSpec,
    area: Frame,
    theme: Theme,
    graphics: _GraphicStyles,
    styles: _ParagraphStyles,
    card: str = "filled",
) -> str:
    """Render a compact comparison table: header row, hairline-separated rows,
    and an optional highlighted row (e.g. one's own plan in a matrix).

    Editable text in positioned frames rather than a ``table:table`` — Impress
    tables ignore most of the styling ODF lets us put on them, and the rest of
    the page is built the same way.
    """
    widths, header_pt, body_pt, head_h, row_hs = _table_geometry(table, area, theme)
    # A short table breathes to about three quarters of its area (up to 0.5cm
    # more per row) rather than stopping a third of the way down the page.
    short = area.h * 0.75 - (head_h + sum(row_hs))
    if short > 0:
        extra = min(0.85, short / len(row_hs))
        row_hs = [h + extra for h in row_hs]
    total_h = head_h + sum(row_hs)
    y = area.y if _BODY_ANCHOR_TOP else area.y + max(0.0, (area.h - total_h) / 2 * 0.5)
    rule_color = theme.text if card == "rule-ink" else theme.accent
    hair_color = _blend(theme.muted, theme.bg, 0.72)
    parts: list[str] = []

    # Header row.
    if card == "filled":
        parts.append(_fill(area.x, y, area.w, head_h, theme.surface, graphics, 0.12))
    x = area.x
    for label, w in zip(table.columns, widths, strict=True):
        parts.append(
            _visual_text_xml(
                label, x + _TABLE_PAD_X, y + _TABLE_PAD_Y, w - 2 * _TABLE_PAD_X,
                head_h - 2 * _TABLE_PAD_Y, styles,
                size_pt=header_pt, color=theme.accent if card != "filled" else theme.muted,
                bold=True, line_height="120%",
            )
        )
        x += w
    y += head_h
    parts.append(_fill(area.x, y - 0.03, area.w, 0.05, rule_color, graphics))

    for r, (row, row_h) in enumerate(zip(table.rows, row_hs, strict=True)):
        highlighted = table.highlight == r
        if highlighted:
            parts.append(
                _fill(area.x, y, area.w, row_h, _blend(theme.accent, theme.bg, 0.86), graphics)
            )
            parts.append(_fill(area.x, y, 0.12, row_h, theme.accent, graphics))
        x = area.x
        for c, (cell, w) in enumerate(zip(row, widths, strict=True)):
            # Each cell is centred in its row: top-aligned cells next to a
            # three-line neighbour read as a ragged row. One font for every
            # column — the display face on column one sat on its own baseline.
            cell_h = _measured_text_h(
                cell, body_pt, _wrap_width(w - 2 * _TABLE_PAD_X), _TABLE_LINE_HEIGHT
            )
            parts.append(
                _visual_text_xml(
                    cell, x + _TABLE_PAD_X, y + (row_h - cell_h) / 2,
                    w - 2 * _TABLE_PAD_X, cell_h, styles,
                    size_pt=body_pt, color=theme.text,
                    bold=highlighted or c == 0,
                    line_height="130%",
                )
            )
            x += w
        y += row_h
        if r < len(table.rows) - 1:
            parts.append(
                _fill(
                    area.x, y - _TABLE_HAIRLINE / 2, area.w, _TABLE_HAIRLINE,
                    hair_color, graphics,
                )
            )
    return "".join(parts)


_TAKEAWAY_PAD_X = 0.35


_PANEL_PAD = 0.55


def _takeaway_panel_xml(
    text: str,
    frame: Frame,
    theme: Theme,
    graphics: _GraphicStyles,
    styles: _ParagraphStyles,
) -> str:
    """The conclusion as a dark panel down the right third: a small accent
    label, a short rule, and the sentence set large. Dark ink on a light deck,
    the accent-deepened fill on a dark one — always the page's heaviest block,
    which is the point: it is what the page concludes."""
    fill = theme.text if _is_light(theme.bg) else _section_fill(theme)
    ink = theme.bg if _is_light(theme.bg) else theme.text
    # A deep accent (forest, navy) on the near-black panel was unreadable;
    # lifted toward the page colour it still reads as the accent.
    label_color = _blend(theme.accent, theme.bg, 0.45) if _is_light(theme.bg) else theme.bg
    parts = [_fill(frame.x, frame.y, frame.w, frame.h, fill, graphics, 0.1)]
    inner_x = frame.x + _PANEL_PAD
    inner_w = frame.w - 2 * _PANEL_PAD
    label_y = frame.y + _PANEL_PAD
    parts.append(_fill(inner_x, label_y, 1.2, 0.08, theme.accent, graphics))
    parts.append(
        _visual_text_xml(
            "重點" if _is_cjk_text(text) else "KEY POINT",
            inner_x, label_y + 0.2, inner_w, 0.7, styles,
            size_pt=min(theme.caption_pt, 12), color=label_color, bold=True,
        )
    )
    body_top = label_y + 1.1
    room = frame.y + frame.h - _PANEL_PAD - body_top
    wrap = _wrap_width(inner_w)
    size = frame.size_pt
    while size > 13 and _measured_text_h(text, size, wrap, 1.35) > room:
        size -= 1
    text_h = min(room, _measured_text_h(text, size, wrap, 1.35))
    parts.append(
        _visual_text_xml(
            text, inner_x, body_top, inner_w, text_h, styles,
            size_pt=size, color=ink, bold=True, font=theme.font_display,
            line_height="135%",
        )
    )
    return "".join(parts)


def _is_cjk_text(text: str) -> bool:
    return any("\u3000" <= ch <= "\u9fff" for ch in text)


_TAKEAWAY_FOLLOW_GAP = 0.55
# Body roles whose drawn height is their content's: the takeaway follows them.
_SIZED_BODY_ROLES = frozenset(
    {"process-area", "timeline-area", "metrics-area", "cards-area", "table-area"}
)
_XML_BOTTOM = re.compile(r'svg:y="(-?[\d.]+)cm"[^>]*?svg:height="([\d.]+)cm"')


_BLOCK_OPTICAL = 0.4
_XML_Y = re.compile(r'(svg:y[12]?=")(-?[\d.]+)cm"')


def _shift_y(xml: str, dy: float) -> str:
    """Move every positioned shape in ``xml`` down by ``dy`` cm."""
    return _XML_Y.sub(lambda m: f'{m.group(1)}{float(m.group(2)) + dy:.4f}cm"', xml)


def _xml_top(xml: str) -> float | None:
    """Highest edge of any positioned shape in ``xml``."""
    ys = [float(y) for y, _h in _XML_BOTTOM.findall(xml)]
    return min(ys) if ys else None


def _xml_bottom(xml: str) -> float | None:
    """Lowest edge of any positioned shape in ``xml``."""
    ends = [float(y) + float(h) for y, h in _XML_BOTTOM.findall(xml)]
    return max(ends) if ends else None


def _panel_need_h(text: str, frame: Frame, theme: Theme) -> float:
    """Height the takeaway panel needs for its label and sentence."""
    wrap = _wrap_width(frame.w - 2 * _PANEL_PAD)
    return (
        2 * _PANEL_PAD + 1.1 + _measured_text_h(text, frame.size_pt, wrap, 1.35)
    )


def _takeaway_xml(
    text: str,
    frame: Frame,
    theme: Theme,
    graphics: _GraphicStyles,
    styles: _ParagraphStyles,
    treatment: str,
) -> str:
    """The page's "so what", drawn at the foot per ``Style.takeaway``.

    One sentence, visually distinct from the evidence above it — the element
    every hand-built deck ends a page with and no generated page had.
    """
    if treatment == "panel" and frame.h > 3:
        return _takeaway_panel_xml(text, frame, theme, graphics, styles)
    if treatment == "panel":
        treatment = "bar"
    size = frame.size_pt
    text_w = frame.w - 2 * _TAKEAWAY_PAD_X
    wrap_w = _wrap_width(text_w)
    while (
        size > _TITLE_MIN_PT
        and _measured_text_h(text, size, wrap_w, 1.25) > frame.h - 0.05
    ):
        size -= 1
    text_h = _measured_text_h(text, size, wrap_w, 1.25)
    # LibreOffice sets the first line's ascent below the box top (inset plus
    # the CJK line-height lead); centring the measured box alone left the text
    # visibly low in the band.
    ty = frame.y + (frame.h - text_h) / 2 - 0.05
    parts: list[str] = []
    color = theme.text
    font = theme.font_display
    center = False
    tx = frame.x + _TAKEAWAY_PAD_X
    if treatment == "band":
        parts.append(_fill(frame.x, frame.y, frame.w, frame.h, theme.accent, graphics, 0.2))
        color = theme.bg
    elif treatment == "bleed":
        parts.append(_fill(0, frame.y, PAGE_W, frame.h, theme.accent, graphics))
        color = theme.bg
    elif treatment == "bar":
        parts.append(_fill(frame.x, frame.y, frame.w, frame.h, theme.surface, graphics))
        parts.append(_fill(frame.x, frame.y, 0.2, frame.h, theme.accent, graphics))
    elif treatment == "box":
        parts.append(
            _rect_xml(
                frame.x, frame.y, frame.w, frame.h,
                fill="none", corner_radius_cm=0.12,
                style_name=graphics.name_for_stroke(theme.accent, 1.0),
            )
        )
        parts.append(_fill(frame.x, frame.y, 0.2, frame.h, theme.accent, graphics))
    elif treatment == "rule":
        parts.append(
            _fill(frame.x, frame.y, _KICKER_RULE_W, _KICKER_RULE_H, theme.accent, graphics)
        )
        tx = frame.x
        text_w = frame.w
        ty = frame.y + 0.35
    else:  # quiet
        center = True
        tx = frame.x
        text_w = frame.w
    parts.append(
        _visual_text_xml(
            text, tx, ty, text_w, text_h + 0.1, styles,
            size_pt=size, color=color, bold=True, center=center,
            font=font, line_height="125%",
        )
    )
    return "".join(parts)


def _fit_cards(items: list, area: Frame, theme: Theme):
    """The card grid's shared fit: geometry + the settled texts/sizes.

    Split out of :func:`_cards_xml` so :func:`cards_budget_warnings` can dry-run
    the *exact* fit the renderer performs instead of mirroring its constants —
    two copies of this arithmetic would drift.
    """
    n = len(items)
    cols = 2 if n in (2, 4) else 3
    rows = 2 if n == 4 else 1
    gap = _VISUAL_GAP
    if rows == 2 and (area.h - gap) / 2 < _CARD_MIN_H:
        # Under a dashboard strip with a lede and a takeaway the area is ~3cm:
        # two rows of minimum-height cards ran past the band and the footer.
        cols, rows = 4, 1
    card_w = (area.w - gap * (cols - 1)) / cols
    raw_h = (area.h - gap * (rows - 1)) / rows

    text_w = card_w - 2 * _CARD_PAD_X
    # A card beside a side panel is ~5cm wide: at 20pt its title broke every
    # four characters. Narrow cards start a size smaller.
    title_pt = min(theme.body_pt + 2, 20) if card_w >= 7 else min(theme.body_pt, 17)
    child_pt = min(theme.caption_pt + 1, 14)
    title_top = _CARD_PAD_TOP + _CARD_BADGE_H + _CARD_BADGE_GAP

    normalised = [
        item if isinstance(item, tuple) else (str(item), []) for item in items
    ]
    # One title height for the row so every card's children start on the same
    # baseline; one card height so the row stays visually even. The shared fit
    # loop also closes the 2×2 defect where a ~60-char title pushed the child
    # offset past the clamped card and the children landed under (and were
    # painted over by) the second row.
    fitted = _fit_stacked_texts(
        [text for text, _ in normalised],
        [
            "\n".join(str(child) for child in children)
            for _, children in normalised
        ],
        title_pt=title_pt,
        detail_pt=child_pt,
        wrap_w=_wrap_width(text_w),
        title_line_height=_CARD_TITLE_LINE_HEIGHT,
        detail_line_height=_CARD_CHILD_LINE_HEIGHT,
        title_top=title_top,
        detail_gap=_CARD_TITLE_GAP,
        pad_bottom=_CARD_PAD_BOTTOM,
        min_h=_CARD_MIN_H,
        # The ceiling is the space, not a design cap: once the measure counted
        # Noto's real line box, a 6.8cm cap made three-line card titles give
        # up their size before the page ran out of room.
        max_h=raw_h,
        available=raw_h,
    )
    return cols, rows, gap, card_w, text_w, title_top, normalised, fitted


def cards_budget_warnings(slide, theme: Theme, style: Style | None = None) -> list[str]:
    """Budget check for the cards layout — closes its ``check_budget`` blind spot.

    ``cards-area`` is shape-rendered, so textmetrics charges it nothing — yet
    ``Slide.bullets`` carry no IR length cap, making cards the one layout whose
    overflow rode on unbounded input. Only the renderer knows when its fit loop
    bottoms out at the font floors and starts cutting text, so this dry-runs the
    exact fit :func:`_cards_xml` performs and reports every card whose text
    would be truncated — feedback the retry loop turns into shorter content,
    which beats shipping an ellipsis. Self-rescue first, feedback last: fonts
    stepping down is rescue, not a warning.
    """
    if slide.layout != "cards" or not slide.bullets:
        return []
    area = next(f for f in slide_frames(slide, style) if f.role == "cards-area")
    items = _coerce_bullet_items(slide.bullets)
    _cols, _rows, _gap, _cw, _tw, _tt, normalised, fitted = _fit_cards(
        items, area, theme
    )
    label = slide.title or "(未命名投影片)"
    warnings = []
    if fitted.spills:
        warnings.append(
            f"投影片「{label}」的卡片空間不足,文字會超出卡片 — "
            "請縮短文字、減少卡片,或拿掉這頁的 takeaway。"
        )
    for i, (text, children) in enumerate(normalised):
        detail = "\n".join(str(child) for child in children)
        truncated = fitted.titles[i] != text or (
            bool(detail) and fitted.details[i] != detail
        )
        if truncated:
            warnings.append(
                f"投影片「{label}」的第 {i + 1} 張卡片文字過長：即使縮到最小字級"
                f"仍放不下，將被截斷 — 請精簡卡片標題或子項文字。"
            )
    return warnings


def truncation_warnings(
    slide: Slide, theme: Theme, style: Style | None = None
) -> list[str]:
    """Report every text the shape layouts had to cut to an ellipsis.

    The fit loops shrink fonts and then truncate rather than print past a card
    — the right call at render time, but until now only the cards layout told
    anyone. An agent filled a timeline with 40-char details, got a clean "ok",
    and shipped a page of "…". Dry-running the page is the one check that can
    never drift from what the renderer actually draws.
    """
    import xml.etree.ElementTree as ET

    probe = Presentation(title="probe", slides=[slide])
    probe_xml = build_content_xml(probe, theme, style=style)
    root = ET.fromstring(probe_xml.encode("utf-8"))
    frame_tag = f"{{{_CONTENT_NS['draw']}}}frame"
    source = {
        v for v in _string_values(slide.model_dump(mode="json")) if v.endswith("…")
    }
    label = slide.title or "(未命名投影片)"
    warnings = []
    for dropped in re.findall(r"<!--odforge:label-dropped:(.*?)-->", probe_xml):
        warnings.append(
            f"投影片「{label}」的連線標籤「{dropped}」找不到不壓到節點的位置,已省略"
            " — 請縮短標籤或減少節點。"
        )
    drawn_all = "".join("".join(f.itertext()) for f in root.iter(frame_tag))
    # Compared without whitespace: a line laid out at a space drops that space.
    drawn_all = "".join(drawn_all.split())
    for item in slide.bullets:
        for child in getattr(item, "children", []) or []:
            if child.strip() and "".join(child.split()) not in drawn_all:
                warnings.append(
                    f"投影片「{label}」的說明「{child[:16]}」放不下,沒有畫出"
                    " — 請縮短文字或減少子項。"
                )
    if _SPILL_MARK in probe_xml:
        warnings.append(
            f"投影片「{label}」的卡片空間不足,文字會超出卡片 — "
            "請縮短文字、減少項目,或拿掉這頁的 takeaway。"
        )
    if slide.layout == "diagram" and slide.diagram is not None:
        def bare(s: str) -> str:
            return re.sub(r"\s", "", s).rstrip("…")

        drawn = {bare("".join(f.itertext())) for f in root.iter(frame_tag)}
        for node in slide.diagram.nodes:
            if node.detail and not any(
                t and bare(node.detail).startswith(t) for t in drawn
            ):
                warnings.append(
                    f"投影片「{label}」的節點「{node.title}」放不下說明文字,"
                    "說明沒有畫出 — 請縮短節點標題或說明。"
                )
    for frame in root.iter(frame_tag):
        text = "".join(frame.itertext()).strip()
        if text.endswith("…") and text not in source:
            warnings.append(
                f"投影片「{label}」有文字放不下被截斷:「{text[:24]}」"
                "— 請縮短這段文字或減少項目。"
            )
    return warnings


def table_budget_warnings(
    slide: Slide, theme: Theme, style: Style | None = None
) -> list[str]:
    """A table taller than its area — even at the smallest body size — runs
    into the takeaway band or off the page; nothing truncates it, so the
    ellipsis check cannot see it."""
    if slide.layout != "table" or slide.table is None:
        return []
    area = next(f for f in slide_frames(slide, style) if f.role == "table-area")
    _w, _hp, _bp, head_h, row_hs = _table_geometry(slide.table, area, theme)
    need = head_h + sum(row_hs)
    # The row measure is generous (slack + inset per cell); only a clear
    # overrun is reported — 7.6cm in a 7.2cm area rendered clean every time.
    if need <= area.h + 0.15:
        return []
    return [
        f"投影片「{slide.title or '(未命名投影片)'}」的表格需要約 {need:.1f}cm,"
        f"可用 {area.h:.1f}cm — 請減少列數、縮短儲存格文字,或拿掉這頁的 takeaway。"
    ]


def _string_values(data) -> list[str]:
    if isinstance(data, str):
        return [data]
    if isinstance(data, dict):
        return [s for v in data.values() for s in _string_values(v)]
    if isinstance(data, list):
        return [s for v in data for s in _string_values(v)]
    return []


def _cards_xml(
    items: list,
    area: Frame,
    theme: Theme,
    graphics: _GraphicStyles,
    styles: _ParagraphStyles,
    card: str = "filled",
    *,
    flush: float | None = None,
) -> str:
    """Render 2–4 parallel ideas as editorial cards rather than a bullet list.

    Cards are sized to what they actually hold. The height used to be a flat
    ``min(area.h, 6.8)``, which drew a 6.8cm box around a one-line idea and left
    its bottom 60% empty, while the title itself was pinned to a 1.65cm frame a
    three-line CJK heading overran. Both come from the same measurement now:
    :func:`estimate_height_cm` gives the wrapped title height, the card grows to
    fit it (plus any children), and the tallest card sets the height for the
    whole row — uniform, but never larger than its content needs.
    """
    cols, rows, gap, card_w, text_w, title_top, normalised, fitted = _fit_cards(
        items, area, theme
    )
    card_h = fitted.card_h
    if card in _PANEL_CARDS:
        # Visible containers reach down the page like the reference deck's
        # panels (content stays at the top of each card); a short strip of
        # boxes floating over an empty lower half read as unfinished.
        card_h = max(card_h, ((area.h - gap * (rows - 1)) / rows) * _PANEL_FILL)
    if flush is not None:
        # Beside a takeaway panel the grid starts at the panel's top and is at
        # least as tall as the panel's own text; the panel then takes the
        # grid's height (see _page_xml), so the two read as one block.
        card_h = max(card_h, (min(area.h, flush) - gap * (rows - 1)) / rows)
    grid_h = rows * card_h + gap * (rows - 1)
    start_y = (
        area.y
        if flush is not None
        else area.y + _BODY_TOP_GAP
        if card in _PANEL_CARDS
        else _body_y(area, grid_h)
    )
    child_y_offset = fitted.detail_offset

    parts: list[str] = []
    for i in range(len(normalised)):
        row, col = divmod(i, cols)
        card_x = area.x + col * (card_w + gap)
        card_y = start_y + row * (card_h + gap)
        parts.append(_visual_card_xml(card_x, card_y, card_w, card_h, theme, graphics, card))
        parts.append(
            _visual_text_xml(
                f"{i + 1:02d}",
                card_x + _CARD_PAD_X,
                card_y + _CARD_PAD_TOP,
                1.6,
                _CARD_BADGE_H,
                styles,
                size_pt=theme.caption_pt,
                color=theme.accent,
                bold=True,
            )
        )
        parts.append(
            _visual_text_xml(
                fitted.titles[i],
                card_x + _CARD_PAD_X,
                card_y + title_top,
                text_w,
                fitted.title_h,
                styles,
                size_pt=fitted.title_pt,
                color=theme.text,
                bold=True,
                font=theme.font_display,
                line_height="120%",
            )
        )
        if fitted.details[i]:
            parts.append(
                _visual_text_xml(
                    fitted.details[i],
                    card_x + _CARD_PAD_X,
                    card_y + child_y_offset,
                    text_w,
                    max(0.4, card_h - child_y_offset - _CARD_PAD_BOTTOM),
                    styles,
                    size_pt=fitted.detail_pt,
                    color=theme.muted,
                    line_height="135%",
                )
            )
    return (_SPILL_MARK if fitted.spills else "") + "".join(parts)


def _closing_actions_xml(
    items: list,
    area: Frame,
    theme: Theme,
    graphics: _GraphicStyles,
    styles: _ParagraphStyles,
    card: str = "filled",
) -> str:
    """Render concrete closing actions as cards sized to their own text.

    Every box on the card derives from one measured ``title_h`` (see
    :func:`_stacked_card_h`), so the title can never end above the words it was
    drawn to hold and the detail can never be pinned where the title already is.
    """
    if not items:
        return ""

    # A closing page with more actions than this is a content failure the
    # outline stage should prevent — but the renderer must never emit invalid
    # geometry regardless (six-plus cards squeezed text_w toward zero, and at
    # count ≥ 20 produced a negative svg:width). Render the first five.
    items = items[:_CLOSING_MAX_ACTIONS]
    count = len(items)
    gap = 0.45
    card_w = min(10.0, (area.w - gap * (count - 1)) / count)
    grid_w = card_w * count + gap * (count - 1)
    start_x = area.x + (area.w - grid_w) / 2

    # Belt over the count cap: a text frame must never be ≤ 0 wide.
    text_w = max(0.5, card_w - 0.8)
    wrap_w = _wrap_width(text_w)
    entries = [
        item if isinstance(item, tuple) else (str(item), []) for item in items
    ]
    # Children strings carry no IR length cap, so the joined detail can be
    # arbitrarily long: the old loop shrank only the title and let the detail
    # spill onto the inverted background at the 12pt floor. The shared fit
    # loop steps the detail down too, then truncates.
    fitted = _fit_stacked_texts(
        [text for text, _children in entries],
        [
            "\n".join(str(child) for child in children)
            for _text, children in entries
        ],
        title_pt=min(theme.body_pt, 17),
        detail_pt=min(theme.caption_pt, 12),
        wrap_w=wrap_w,
        title_line_height=_CLOSING_TITLE_LINE_HEIGHT,
        detail_line_height=_CLOSING_DETAIL_LINE_HEIGHT,
        title_top=_CLOSING_TITLE_TOP,
        detail_gap=_CLOSING_DETAIL_GAP,
        pad_bottom=_CLOSING_PAD_BOTTOM,
        min_h=_CLOSING_MIN_H,
        max_h=_CLOSING_MAX_H,
        available=area.h,
    )
    card_h, detail_offset = fitted.card_h, fitted.detail_offset

    start_y = area.y + (area.h - card_h) / 2

    parts: list[str] = []
    for index in range(count):
        card_x = start_x + index * (card_w + gap)
        parts.append(
            _visual_card_xml(card_x, start_y, card_w, card_h, theme, graphics, card)
        )
        parts.append(
            _visual_text_xml(
                f"{index + 1:02d}",
                card_x + 0.4,
                start_y + 0.38,
                text_w,
                0.5,
                styles,
                size_pt=theme.caption_pt,
                color=theme.accent,
                bold=True,
            )
        )
        parts.append(
            _visual_text_xml(
                fitted.titles[index],
                card_x + 0.4,
                start_y + _CLOSING_TITLE_TOP,
                text_w,
                fitted.title_h,
                styles,
                size_pt=fitted.title_pt,
                color=theme.text,
                bold=True,
                font=theme.font_display,
                line_height="120%",
            )
        )
        if fitted.details[index]:
            parts.append(
                _visual_text_xml(
                    fitted.details[index],
                    card_x + 0.4,
                    start_y + detail_offset,
                    text_w,
                    max(0.4, card_h - detail_offset - _CLOSING_PAD_BOTTOM),
                    styles,
                    size_pt=fitted.detail_pt,
                    color=theme.muted,
                    line_height="125%",
                )
            )
    return (_SPILL_MARK if fitted.spills else "") + "".join(parts)


def _diagram_positions(
    diagram: DiagramSpec,
    area: Frame,
) -> dict[str, tuple[float, float, float, float]]:
    """Return deterministic node boxes for hub or hierarchy diagrams."""
    if diagram.kind == "hub":
        center_x = area.x + area.w / 2
        center_y = area.y + area.h / 2
        # Three node rows (top / centre / bottom) must leave a connector long
        # enough to carry its label. At the full 2.45cm node height a lede and
        # a takeaway band left ~0.1cm between the centre and the top node, so
        # every vertical edge label was pushed off to float beside the nodes.
        node_h = max(
            _DIAGRAM_MIN_NODE_H + 0.4,
            min(_DIAGRAM_NODE_H, (area.h - 2 * _HUB_MIN_EDGE) / 3),
        )
        positions = {
            diagram.nodes[0].id: (
                center_x - _DIAGRAM_NODE_W / 2,
                center_y - node_h / 2,
                _DIAGRAM_NODE_W,
                node_h,
            )
        }
        outer = diagram.nodes[1:]
        count = len(outer)
        radius_x = min(8.2, (area.w - _DIAGRAM_NODE_W) / 2)
        radius_y = min(3.45, (area.h - node_h) / 2)

        def clearance(offset: float) -> float:
            # Worst gap between any satellite and the hub, along whichever
            # axis separates them. Straight up/down in a short area left the
            # top and bottom nodes touching the hub with no connector at all.
            worst = math.inf
            for i in range(count):
                a = offset + 2 * math.pi * i / count
                dx = abs(radius_x * math.cos(a))
                dy = abs(radius_y * math.sin(a))
                worst = min(worst, max(dx - _DIAGRAM_NODE_W, dy - node_h))
            return worst

        start = max(
            (-math.pi / 2, -math.pi / 2 + math.pi / count,
             -math.pi / 2 + math.pi / (2 * count)),
            key=lambda o: (round(clearance(o), 2) >= _HUB_MIN_EDGE, clearance(o)),
        )
        for i, node in enumerate(outer):
            angle = start + 2 * math.pi * i / count
            node_x = center_x + radius_x * math.cos(angle)
            node_y = center_y + radius_y * math.sin(angle)
            positions[node.id] = (
                node_x - _DIAGRAM_NODE_W / 2,
                node_y - node_h / 2,
                _DIAGRAM_NODE_W,
                node_h,
            )
        return positions

    node_ids = [node.id for node in diagram.nodes]
    indegree = dict.fromkeys(node_ids, 0)
    for edge in diagram.edges:
        indegree[edge.target] += 1
    roots = [node_id for node_id in node_ids if indegree[node_id] == 0]
    if not roots:
        roots = [node_ids[0]]
    levels = dict.fromkeys(roots, 0)
    for _ in node_ids:
        changed = False
        for edge in diagram.edges:
            if edge.source in levels:
                candidate = levels[edge.source] + 1
                if candidate > levels.get(edge.target, -1):
                    levels[edge.target] = candidate
                    changed = True
        if not changed:
            break
    for node_id in node_ids:
        levels.setdefault(node_id, 0)

    max_level = max(levels.values())
    # A cycle can make the relaxation above grow indefinitely. Collapse such a
    # graph to a single balanced row rather than drawing outside the page.
    if max_level >= len(node_ids):
        levels = dict.fromkeys(node_ids, 0)
        max_level = 0
    groups: dict[int, list[str]] = {}
    for node_id in node_ids:
        groups.setdefault(levels[node_id], []).append(node_id)

    level_count = max_level + 1
    # A labelled edge needs a connector long enough to carry its chip.
    vertical_gap = 0.9 if any(edge.label for edge in diagram.edges) else 0.45
    node_h = min(
        _DIAGRAM_NODE_H,
        (area.h - vertical_gap * (level_count - 1)) / level_count,
    )
    widest = max(len(row) for row in groups.values())
    # A chain (one node per level) drawn as a column filled a fifth of the
    # page width; it reads left → right like the process it is.
    if (node_h < _DIAGRAM_MIN_NODE_H and level_count > widest) or (
        widest == 1 and level_count >= 3
    ):
        # A deep, narrow hierarchy (a pipeline drawn as a chain) squeezed into
        # a short area gave each level ~1.2cm and cut every node's text. Flow
        # the levels left → right instead: the page is wider than it is tall.
        gap = 0.9
        col_w = min(
            _DIAGRAM_NODE_W, (area.w - gap * (level_count - 1)) / level_count
        )
        grid_w = col_w * level_count + gap * (level_count - 1)
        start_x = area.x + (area.w - grid_w) / 2
        positions = {}
        for level in range(level_count):
            col = groups.get(level, [])
            if not col:
                continue
            h = min(
                _DIAGRAM_NODE_H,
                (area.h - vertical_gap * (len(col) - 1)) / len(col),
            )
            col_h = h * len(col) + vertical_gap * (len(col) - 1)
            top = area.y + (area.h - col_h) / 2
            for i, node_id in enumerate(col):
                positions[node_id] = (
                    start_x + level * (col_w + gap),
                    top + i * (h + vertical_gap),
                    col_w,
                    h,
                )
        return positions
    grid_h = node_h * level_count + vertical_gap * (level_count - 1)
    start_y = area.y + (area.h - grid_h) / 2
    positions: dict[str, tuple[float, float, float, float]] = {}
    for level in range(level_count):
        row = groups.get(level, [])
        if not row:
            continue
        horizontal_gap = _VISUAL_GAP
        node_w = min(
            _DIAGRAM_NODE_W,
            (area.w - horizontal_gap * (len(row) - 1)) / len(row),
        )
        row_w = node_w * len(row) + horizontal_gap * (len(row) - 1)
        start_x = area.x + (area.w - row_w) / 2
        for i, node_id in enumerate(row):
            positions[node_id] = (
                start_x + i * (node_w + horizontal_gap),
                start_y + level * (node_h + vertical_gap),
                node_w,
                node_h,
            )
    return positions


def _diagram_xml(
    diagram: DiagramSpec,
    area: Frame,
    theme: Theme,
    graphics: _GraphicStyles,
    styles: _ParagraphStyles,
) -> str:
    """Render an editable hub or hierarchy diagram with labelled relations."""
    positions = _diagram_positions(diagram, area)
    line_color = _blend(theme.accent, theme.bg, _DIAGRAM_LINE_BLEND)
    line_style = graphics.name_for_stroke(line_color, _VISUAL_LINE_WIDTH_PT)
    parts: list[str] = []

    label_pt = min(theme.caption_pt, 11)
    # Everything a label must not land on. The node cards go in first because
    # they are drawn last: a label under a card is not crowded, it is gone.
    taken: list[tuple[float, float, float, float]] = list(positions.values())
    for edge in diagram.edges:
        sx, sy, sw, sh = positions[edge.source]
        tx, ty, tw, th = positions[edge.target]
        # Draw the connector between the two cards' edges rather than their
        # centres. The old version relied on the cards to mask the overshoot,
        # which left the label chip masking everything that remained.
        x1, y1, x2, y2 = _trim_to_boxes(
            (sx, sy, sw, sh), (tx, ty, tw, th)
        )
        parts.append(
            _line_xml(
                x1,
                y1,
                x2,
                y2,
                color=line_color,
                width_pt=_VISUAL_LINE_WIDTH_PT,
                style_name=line_style,
            )
        )
        if edge.label:
            label_h = 0.58
            label_w = (
                text_width_cm(edge.label, label_pt)
                + 2 * _FRAME_INSET_X
                + 2 * _EDGE_LABEL_PAD
            )
            candidates = _edge_label_candidates(x1, y1, x2, y2, label_w, label_h)
            placed = _place_edge_label(candidates, label_w, label_h, taken, area)
            if placed is None:
                parts.append(_LABEL_DROP_MARK.format(escape(edge.label)))
                continue
            label_x, label_y, on_line = placed
            taken.append((label_x, label_y, label_w, label_h))
            if on_line:
                label_bg = graphics.name_for_fill(theme.bg)
                parts.append(
                    _rect_xml(
                        label_x,
                        label_y,
                        label_w,
                        label_h,
                        fill=theme.bg,
                        corner_radius_cm=0.08,
                        style_name=label_bg,
                    )
                )
            parts.append(
                _visual_text_xml(
                    edge.label,
                    label_x,
                    label_y + 0.03,
                    label_w,
                    label_h,
                    styles,
                    size_pt=label_pt,
                    color=theme.muted,
                    bold=True,
                    center=True,
                )
            )

    accent_style = graphics.name_for_fill(theme.accent)
    surface_style = graphics.name_for_fill(theme.surface)
    for i, node in enumerate(diagram.nodes):
        x, y, w, h = positions[node.id]
        emphasized = node.emphasis or (diagram.kind == "hub" and i == 0)
        fill = theme.accent if emphasized else theme.surface
        card_style = accent_style if emphasized else surface_style
        parts.append(
            _rect_xml(
                x,
                y,
                w,
                h,
                fill=fill,
                corner_radius_cm=_VISUAL_CARD_CORNER,
                style_name=card_style,
            )
        )
        text_color = theme.bg if emphasized else theme.text
        detail_color = theme.bg if emphasized else theme.muted
        title_size = fact_font_size_pt(
            node.title,
            max(w - 1.5, 0.5),
            min(theme.body_pt, 17),
            min(theme.caption_pt, 12),
        )
        # Measured like every other sized-to-text box (LO slack + inset), and
        # wrapped at the *inset* width — the raw estimate against `w-0.7` let
        # LibreOffice wrap one line more than modelled, and a 24-char title's
        # fourth line printed straight through the detail below.
        node_wrap_w = _wrap_width(max(w - 0.7, 0.5))
        title_cap = max(h - 0.95, 0.62)
        title_text = node.title
        title_h = _measured_text_h(title_text, title_size, node_wrap_w, 1.15)
        if title_h > title_cap:
            # The capped node box cannot hold the title even at the 12pt floor
            # fact_font_size_pt bottomed out at — truncate (LibreOffice does
            # not clip; see _truncate_to_h) instead of printing into the detail.
            title_text = _truncate_to_h(
                node.title, title_size, node_wrap_w, 1.15, title_cap
            )
            title_h = _measured_text_h(title_text, title_size, node_wrap_w, 1.15)
        title_h = min(max(title_h, 0.62), title_cap)
        # Tight insets: measured with Noto's real line box, the old 0.28 / 0.12 /
        # 0.22cm margins left a 2cm node no room for its detail at all.
        title_y = y + 0.18
        parts.append(
            _visual_text_xml(
                title_text,
                x + 0.35,
                title_y,
                w - 0.7,
                title_h,
                styles,
                size_pt=title_size,
                color=text_color,
                bold=True,
                center=True,
                font=theme.font_display,
            )
        )
        detail_y = title_y + title_h + 0.04
        detail_h = y + h - 0.12 - detail_y
        if node.detail and detail_h >= 0.45:
            detail_pt = min(theme.caption_pt, 11)
            parts.append(
                _visual_text_xml(
                    # A 48-char detail can never fit the remaining sliver of a
                    # 2.45cm node — truncated to the box for the same reason
                    # as the title.
                    _truncate_to_h(
                        node.detail, detail_pt, node_wrap_w, 1.25, detail_h
                    ),
                    x + 0.35,
                    detail_y,
                    w - 0.7,
                    detail_h,
                    styles,
                    size_pt=detail_pt,
                    color=detail_color,
                    center=True,
                    line_height="125%",
                )
            )
    return "".join(parts)


def _sources_xml(
    sources,
    theme: Theme,
    styles: _ParagraphStyles,
    spans: _SpanStyles,
) -> str:
    """Render concise source references above the master footer.

    Labelled 「來源(未查證)」, deliberately. ODForge does not check that a cited
    URL resolves, that its title matches, or that the page says what the slide
    claims it says — the model produced these strings and nothing downstream
    verified them. A bare 「來源」 label on a deck that also carries four green
    quality gates reads as a claim the product cannot back up.
    """
    paragraph_style = styles.name_for(
        _SOURCE_FRAME.size_pt,
        False,
        False,
        theme.muted,
    )
    label_style = spans.name_for(_SOURCE_FRAME.size_pt, True, theme.accent)
    source_style = spans.name_for(_SOURCE_FRAME.size_pt, False, theme.muted)
    body = (
        f'<text:span text:style-name="{_attr(label_style)}">來源(未查證)</text:span>'
        f'<text:span text:style-name="{_attr(source_style)}">  </text:span>'
    )
    rendered = []
    for source in sources:
        label = escape(source.label)
        if source.url:
            rendered.append(
                f'<text:a text:style-name="{_attr(source_style)}"'
                f' xlink:type="simple" xlink:href="{_attr(source.url)}">'
                f"{label}</text:a>"
            )
        else:
            rendered.append(
                f'<text:span text:style-name="{_attr(source_style)}">'
                f"{label}</text:span>"
            )
    separator = (
        f'<text:span text:style-name="{_attr(source_style)}"> · </text:span>'
    )
    body += separator.join(rendered)
    inner = (
        f'<text:p text:style-name="{_attr(paragraph_style)}">{body}</text:p>'
    )
    return _frame_box_xml(_SOURCE_FRAME, inner)


# ---------------------------------------------------------------------------
# Semantic list rendering (Task 13.2)
# ---------------------------------------------------------------------------

# A list item is either plain ``str`` (a leaf bullet) or ``(text, children)``
# where ``children`` is a list of further items — this is the internal contract
# Task 14.1's nested ``BulletItem`` IR will lower onto. ``Slide.bullets`` is
# ``list[str]`` today and passes straight through as leaf items.
ListItem = "str | tuple[str, list]"


def _list_style_xml(name: str, theme: Theme) -> str:
    """Build a ``<text:list-style>`` with an accent-coloured bullet per level.

    Every level uses ``theme.bullet_char``; the bullet glyph is tinted with the
    accent colour via a ``<style:text-properties>`` child, and each level's
    indent grows through ``<style:list-level-properties>`` so nested items sit
    further in.
    """
    levels = "".join(
        f'<text:list-level-style-bullet text:level="{level}"'
        f' text:bullet-char="{_attr(theme.bullet_char)}">'
        f"<style:list-level-properties"
        f' text:space-before="{_cm(space_before)}"'
        f' text:min-label-width="{_cm(min_label)}"/>'
        f'<style:text-properties fo:color="{_attr(theme.accent)}"/>'
        f"</text:list-level-style-bullet>"
        for level, space_before, min_label in _LIST_LEVELS
    )
    return f'<text:list-style style:name="{_attr(name)}">{levels}</text:list-style>'


_LIST_GROW_MAX = 4  # pt a short bullet page may step up
_LIST_GROW_FILL = 0.62  # ...while the list stays within this share of its frame


def _grown_list_pt(slide: Slide, theme: Theme, frame: Frame, layout: str) -> int:
    """A short bullet list set larger, so three lines at body size do not sit
    in the top third of an empty page. Measured by the budget gate's own
    model, so what grows here can never trip it."""
    size = frame.size_pt
    for step in range(1, _LIST_GROW_MAX + 1):
        h = frame_content_height(slide, theme, replace(frame, size_pt=size + 1), layout)
        if h is None or h > frame.h * _LIST_GROW_FILL:
            break
        size = frame.size_pt + step
    return size


def _list_xml(
    items,
    styles: _ParagraphStyles,
    *,
    size_pt: int,
    color: str,
    style_name: str | None = None,
    frame_w: float | None = None,
    level: int = 1,
) -> str:
    """Build a ``<text:list>`` for ``items`` (see :data:`ListItem`).

    Only the outermost list carries ``style_name`` (``L1``); nested lists omit
    it so LibreOffice derives their level — and thus their level-2 bullet and
    indent — from the enclosing list-style. Each item's text lives in a
    ``<text:p>`` whose paragraph style carries the loose bullet typography
    (line-height + bottom margin); children recurse into a nested ``<text:list>``
    inside the same ``<text:list-item>``.
    """
    li_parts: list[str] = []
    for item in items:
        if isinstance(item, tuple):
            text, children = item
        else:
            text, children = item, ()
        pstyle = styles.name_for(
            size_pt,
            False,
            False,
            color,
            line_height=_BULLET_LINE_HEIGHT,
            margin_bottom=_BULLET_MARGIN_BOTTOM,
        )
        # With the frame width known, the item is laid out by the engine at
        # the width left after its level's indent (space-before + label).
        body = (
            _broken_xml(text, size_pt, frame_w - _list_indent(level))
            if frame_w is not None
            else escape(text)
        )
        para = f'<text:p text:style-name="{_attr(pstyle)}">{body}</text:p>'
        nested = (
            _list_xml(
                children, styles, size_pt=size_pt, color=color,
                frame_w=frame_w, level=level + 1,
            )
            if children
            else ""
        )
        li_parts.append(f"<text:list-item>{para}{nested}</text:list-item>")
    style_attr = f' text:style-name="{_attr(style_name)}"' if style_name else ""
    return f"<text:list{style_attr}>{''.join(li_parts)}</text:list>"


def _list_indent(level: int) -> float:
    """Where a bullet's text starts (cm), per :data:`_LIST_LEVELS`."""
    for lvl, space_before, min_label in _LIST_LEVELS:
        if lvl == level:
            return space_before + min_label
    return _LIST_LEVELS[-1][1] + _LIST_LEVELS[-1][2]


def _kicker_paragraph_xml(
    text: str,
    styles: _ParagraphStyles,
    theme: Theme,
    color: str | None = None,
    *,
    center: bool = False,
) -> str:
    """Build a wide-tracked "kicker" ``<text:p>`` (caption-size, accent, spaced).

    An eyebrow/kicker line above a heading. The paragraph style carries
    ``fo:letter-spacing`` so the label reads as spaced small caps. ``color``
    overrides the default accent for headings that already sit ON accent — an
    accent kicker on an accent band is an invisible line, not a subtle one.
    """
    style_name = styles.name_for(
        theme.caption_pt,
        True,
        center,
        color or theme.accent,
        letter_spacing=_KICKER_LETTER_SPACING,
    )
    return f'<text:p text:style-name="{_attr(style_name)}">{escape(text)}</text:p>'


def _notes_xml(notes: str, style_name: str) -> str:
    """Build the ``presentation:notes`` block for a slide's speaker notes."""
    return (
        "<presentation:notes>"
        f'<draw:frame draw:style-name="{_GRAPHIC_STYLE}"'
        f' svg:x="2cm" svg:y="2cm" svg:width="24cm" svg:height="11cm">'
        f'<draw:text-box><text:p text:style-name="{_attr(style_name)}">'
        f"{escape(notes)}</text:p></draw:text-box>"
        f"</draw:frame>"
        "</presentation:notes>"
    )


def _section_number_xml(
    ordinal: int,
    theme: Theme,
    styles: _ParagraphStyles,
    blend: float = _SECTION_NUMBER_BLEND,
) -> str:
    """Build the giant faded chapter-number watermark for a section page.

    ``ordinal`` is the section's 1-based position among section slides, rendered
    zero-padded (``01``, ``02`` …). The colour is :func:`_blend`\\ ed 15% from
    accent toward the theme bg so the number reads as a low-contrast watermark on
    the accent fill (see :data:`_SECTION_NUMBER_BLEND`). Emitted *before* the
    title so the title paints on top.
    """
    color = _blend(theme.accent, theme.bg, blend)
    style_name = styles.name_for(_SECTION_NUMBER_PT, True, True, color)
    x, y, w, h = _SECTION_NUMBER_BOX
    frame = Frame("section-number", x, y, w, h, _SECTION_NUMBER_PT, bold=True, center=True)
    para = (
        f'<text:p text:style-name="{_attr(style_name)}">'
        f"{escape(f'{ordinal:02d}')}</text:p>"
    )
    return _frame_box_xml(frame, para)


def _quote_mark_xml(theme: Theme, styles: _ParagraphStyles) -> str:
    """Build the quote layout's big decorative quotation-mark watermark.

    A large glyph tinted :data:`_QUOTE_MARK_BLEND` of the way from accent toward
    the bg — faint enough to read as decoration behind the quote text. Emitted
    before the quote so the text paints on top. Deterministic.
    """
    color = _blend(theme.accent, theme.bg, _QUOTE_MARK_BLEND)
    style_name = styles.name_for(_QUOTE_MARK_PT, True, False, color)
    x, y, w, h = _QUOTE_MARK_BOX
    frame = Frame("quote-mark", x, y, w, h, _QUOTE_MARK_PT, bold=True)
    para = (
        f'<text:p text:style-name="{_attr(style_name)}">'
        f"{escape(_QUOTE_MARK_GLYPH)}</text:p>"
    )
    return _frame_box_xml(frame, para)


_LEADING_NUMBER = re.compile(r"^\s*(\d{1,2})(?!\d)")
_OWN_NUMBERING = re.compile(
    r"^\s*(?:\d{1,2}|[一二三四五六七八九十]{1,3})\s*[、.．)）:：]\s*|^\s*\d{1,2}\s+(?=\S)"
)


def _leading_number(text: str) -> int | None:
    match = _LEADING_NUMBER.match(text or "")
    return int(match.group(1)) if match else None


_AGENDA_FILL = 0.7
_AGENDA_GROW = 8


def _agenda_scale(labels: list[str], frame: Frame, body_pt: int) -> tuple[int, float]:
    """Size and row gap for the agenda: six items at body size filled the top
    third of the page. Grow while every item stays on one line and the list
    stays within :data:`_AGENDA_FILL` of the frame, then spread the rows."""
    n = max(1, len(labels))
    room = frame.h * _AGENDA_FILL

    def row_h(size: int) -> float:
        return size * PT_TO_CM * _CJK_LINE_BOX * _LO_LINE_SPACING_SLACK

    base_gap = float(_BULLET_MARGIN_BOTTOM.removesuffix("cm"))
    size = body_pt
    for candidate in range(body_pt + 1, body_pt + _AGENDA_GROW + 1):
        fits_line = all(
            text_width_cm("00  " + label, candidate) <= frame.w - 0.8 for label in labels
        )
        if not fits_line or n * (row_h(candidate) + base_gap) > room:
            break
        size = candidate
    gap = min(1.0, max(base_gap, (room - n * row_h(size)) / n))
    return size, gap


def _numbered_items_xml(
    items: list,
    frame: Frame,
    theme: Theme,
    para_styles: _ParagraphStyles,
    span_styles: _SpanStyles,
) -> str:
    """Build the agenda layout's numbered items (accent-tinted 01/02… numbers).

    Explicit numbered paragraphs (not an ODF list): each item is one ``text:p``
    at ``body_pt`` whose leading ``<text:span>`` carries a zero-padded ordinal in
    the accent colour, followed by the item text in the body colour. Zero-padding
    ("01") is why this is drawn by hand — ``style:num-format`` cannot pad. Loose
    bullet typography (line-height + bottom margin) spaces the rows.
    """
    labels = [
        # The page numbers its own items; an author's "01 " / "一、" would
        # print twice.
        _OWN_NUMBERING.sub("", _line_text(item), count=1) or _line_text(item)
        for item in items
    ]
    size, gap = _agenda_scale(labels, frame, theme.body_pt)
    num_style = span_styles.name_for(size, True, theme.accent)
    text_style = para_styles.name_for(
        size,
        False,
        False,
        theme.text_color,
        line_height=_BULLET_LINE_HEIGHT,
        margin_bottom=f"{gap:.2f}cm",
    )
    paras = []
    for i, label in enumerate(labels, start=1):
        # ``text:s`` because ODF collapses literal runs of spaces — "01" then
        # ran straight into an item that began with a digit.
        paras.append(
            f'<text:p text:style-name="{_attr(text_style)}">'
            f'<text:span text:style-name="{_attr(num_style)}">'
            f"{escape(f'{i:02d}')}</text:span>"
            f'<text:s text:c="2"/>{escape(label)}</text:p>'
        )
    return _frame_box_xml(frame, "".join(paras))


def _page_xml(
    index: int,
    slide: Slide,
    theme: Theme,
    styles: _ParagraphStyles,
    graphics: _GraphicStyles,
    spans: _SpanStyles,
    section_ordinal: int | None,
    resolved_image: tuple[str, AssetBlob] | None = None,
    branding: Branding | None = None,
    logo: _LogoAsset | None = None,
    style: Style | None = None,
) -> str:
    """Build one ``draw:page`` for a slide, registering its paragraph styles.

    Master page + drawing-page style are chosen by layout: :data:`PLAIN_LAYOUTS`
    use the furniture-free "Plain" master, and the style's inverted layouts
    (:func:`inverted_layouts`) swap in the full-accent drawing-page style. What a
    content heading is marked with, how the divider page is treated and how the
    cover is composed all come from ``style``; ``fact`` text is up-sized to
    display + accent; the Task 14.1 page-role layouts (quote/agenda/comparison/
    chart/closing) are dispatched by frame role below.
    """
    style = style or get_style(None)
    inverted = inverted_layouts(style)
    layout = slide.layout
    parts: list[str] = []
    meta = _cover_meta(slide, branding) if layout == "title" else []
    cover = (
        _cover_layout(slide, style, theme, bool(meta)) if layout == "title" else None
    )
    # Pre-content decorations, emitted first so they sit behind the text.
    if layout == "title":
        parts.append(_cover_deco_xml(style, theme, graphics, cover))
        if style.cover_deco == "dots":
            parts.append(_deco_frame_xml())
    if layout == "section":
        parts.append(
            _section_mark_xml(style, section_ordinal, theme, styles, graphics)
        )
        if slide.subtitle:
            # The divider's subtitle (what the chapter is about) was accepted
            # and never drawn.
            head = section_frames(style)[0]
            # Under metropolis' lower rule, not through it.
            below = 0.95 if style.section == "centered-rules" else 0.15
            sub = Frame(
                "section-subtitle", head.x, head.y + head.h + below, head.w, 1.1,
                LEDE_PT, center=head.center,
            )
            ink = (
                _blend(theme.bg, _section_fill(theme), 0.25)
                if layout in inverted
                else theme.muted
            )
            parts.append(
                _frame_xml(
                    sub, [slide.subtitle],
                    styles.name_for(LEDE_PT, False, head.center, ink),
                )
            )
    if layout == "quote":
        parts.append(_quote_mark_xml(theme, styles))

    # Deck-level branding, drawn with the decorations so page content stays on
    # top of it. Both pieces are optional and independent: a deck may carry a
    # byline with no logo, or the reverse.
    if branding is not None:
        if layout == "title" and branding.byline:
            parts.append(_byline_xml(branding.byline, theme, styles, style, cover))
        if (
            layout == "closing"
            and branding.byline
            and branding.placement in ("cover-closing", "all")
        ):
            # The closing page carries the byline too when the placement says
            # so — the reference deck signs off with who and where.
            ink = theme.bg if layout in inverted else theme.muted
            frame = Frame("byline", 2.0, 14.2, 24.0, 0.9, theme.caption_pt, center=True)
            bstyle = styles.name_for(theme.caption_pt, False, True, ink)
            parts.append(
                _frame_box_xml(
                    frame,
                    f'<text:p text:style-name="{_attr(bstyle)}">'
                    f"{escape(branding.byline)}</text:p>",
                )
            )
        if logo is not None and _page_shows_logo(layout, branding.placement):
            parts.append(_logo_xml(logo, layout, theme, graphics))

    # big-fact fit-to-width (deterministic engine guarantee, not prompt advice):
    # shrink the fact toward one line (floor = theme.h1_pt) and, if it still
    # overruns its frame, push the caption down by the fact's real estimated
    # height so the two boxes can never intersect. Both quantities come from the
    # shared textmetrics model so the size drawn == the size the budget gate
    # charges. Computed once here; consumed in the fact / caption branches below.
    # The cover is the one layout whose geometry the style moves; every other
    # layout takes the shared frames the layout budget is computed against,
    # re-flowed around an optional lede / takeaway (themes.page_frames).
    if layout == "title":
        frames = cover
        if meta and not frames[0].center:
            parts.append(_cover_meta_xml(meta, style, theme, graphics, styles, frames))
        elif meta:
            parts.append(_cover_meta_line_xml(meta, style, theme, styles, frames))
    elif layout == "section":
        frames = section_frames(style)
    else:
        frames = slide_frames(slide, style)

    bigfact_fact_pt: int | None = None
    bigfact_caption_y: float | None = None
    if layout == "big-fact" and slide.fact:
        by_role = {f.role: f for f in frames}
        fact_frame, caption_frame = by_role["fact"], by_role["bullets"]
        # The box's own 0.5cm inset (and a sliver for the width model) is not
        # the fact's to use: sized to the full frame, 「58 萬戶」 wrapped and
        # its 「戶」 printed over the caption placed for one line.
        fact_w = (fact_frame.w - 0.5) * 0.97
        bigfact_fact_pt = fact_font_size_pt(
            slide.fact, fact_w, _bigfact_max_pt(theme), theme.h1_pt
        )
        fact_h = estimate_height_cm(slide.fact, bigfact_fact_pt, fact_w)
        gap = caption_frame.y - (fact_frame.y + fact_frame.h)
        bigfact_caption_y = fact_frame.y + max(fact_frame.h, fact_h) + gap

    # Closing cards on an inverted (accent) ground keep their filled surface —
    # a rule or outline would vanish into it.
    card = "filled" if layout in inverted else style.card
    col_pt = (
        _column_pt(slide, theme, frames)
        if layout in {"comparison", "two-col"}
        else None
    )
    # A takeaway set as the right-hand panel (not the foot band): the body
    # blocks beside it align to its top and bottom.
    panel = next(
        (f for f in frames if f.role == "takeaway" and f.x >= PANEL_X - 0.01), None
    )
    panel_need = (
        _panel_need_h(slide.takeaway, panel, theme) if panel is not None else None
    )
    body_bottom: float | None = None
    pending: int | None = None
    body_start: int | None = None
    area_bottom = 0.0
    global _BODY_ANCHOR_TOP
    # Reset at the end of this function (and at the start of every page).
    _BODY_ANCHOR_TOP = panel is None and any(f.role == "takeaway" for f in frames)
    for frame in frames:
        role = frame.role
        # Where the drawn body actually ends (measured off the shapes the
        # previous frame emitted): the takeaway follows it rather than sitting
        # at a fixed y under a dead band.
        if pending is not None:
            bottom = _xml_bottom("".join(parts[pending:]))
            if bottom is not None:
                body_bottom = max(body_bottom or 0.0, bottom)
            pending = None
        if role in _SIZED_BODY_ROLES:
            pending = len(parts)
            area_bottom = max(area_bottom, frame.y + frame.h)
            if body_start is None:
                body_start = pending

        # chart-area draws a ChartSpec (shapes + labels), not text lines.
        if role == "chart-area":
            if slide.chart is not None:
                parts.append(
                    _chart_xml(
                        slide.chart, frame.x, frame.y, frame.w, frame.h,
                        theme, graphics, styles,
                    )
                )
            continue
        if role == "process-area":
            parts.append(_process_xml(slide.steps, frame, theme, graphics, styles, card))
            continue
        if role == "timeline-area":
            parts.append(_timeline_xml(slide.events, frame, theme, graphics, styles, card))
            continue
        if role == "metrics-area":
            parts.append(
                _metrics_xml(
                    slide.metrics, frame, theme, graphics, styles, card,
                    compact=layout == "dashboard",
                    flush=panel_need if layout != "dashboard" else None,
                )
            )
            continue
        if role == "cards-area":
            parts.append(
                _cards_xml(
                    _coerce_bullet_items(slide.bullets),
                    frame,
                    theme,
                    graphics,
                    styles,
                    card,
                    flush=panel_need,
                )
            )
            continue
        if role == "closing-actions":
            parts.append(
                _closing_actions_xml(
                    _coerce_bullet_items(slide.bullets),
                    frame,
                    theme,
                    graphics,
                    styles,
                    card,
                )
            )
            continue
        if role == "table-area":
            if slide.table is not None:
                parts.append(
                    _table_xml(slide.table, frame, theme, graphics, styles, card)
                )
            continue
        if role == "lede":
            lede_pt = heading_pt(slide.subtitle, frame.w, frame.size_pt)
            lede_style = styles.name_for(
                lede_pt, False, False, theme.muted, font=theme.font_body,
            )
            parts.append(_frame_xml(frame, [slide.subtitle], lede_style, lede_pt))
            continue
        if role == "takeaway":
            if body_bottom is not None and panel is not None:
                frame = replace(
                    frame, h=max(min(frame.h, panel_need), body_bottom - frame.y)
                )
            elif body_bottom is not None and body_start is not None:
                # Body and band travel as one block: closed up (no dead band
                # between them) and set a little above centre in the space
                # they share, so the leftover air is split rather than all
                # pooled at the foot of the page.
                block_bottom = body_bottom + _TAKEAWAY_FOLLOW_GAP + frame.h
                slack = frame.y + frame.h - block_bottom
                dy = max(0.0, slack * _BLOCK_OPTICAL)
                if dy > 0.05:
                    parts[body_start:] = [_shift_y(x, dy) for x in parts[body_start:]]
                frame = replace(
                    frame,
                    y=min(frame.y, body_bottom + dy + _TAKEAWAY_FOLLOW_GAP),
                )
            parts.append(
                _takeaway_xml(
                    slide.takeaway, frame, theme, graphics, styles, style.takeaway
                )
            )
            continue
        if role == "diagram-area":
            if slide.diagram is not None:
                parts.append(
                    _diagram_xml(slide.diagram, frame, theme, graphics, styles)
                )
            continue
        if role == "image-area":
            parts.append(
                _image_area_xml(
                    slide, frame, theme, graphics, styles, resolved_image
                )
            )
            continue

        lines = _role_lines(slide, role)
        if not lines:
            continue

        # Column frames (two-col + comparison left/right) get a rounded surface
        # card behind them — emitted before the text frame so document order
        # keeps the text on top.
        if role in _CARD_ROLES:
            # Both columns share one height — the taller column's content,
            # floored — instead of the full frame: two bullets in an 11cm box
            # was the half-empty page.
            col_h = max(
                _column_h(_role_lines(slide, f.role), _col_layout(slide), theme, f, col_pt)
                for f in frames
                if f.role in _CARD_ROLES
            )
            # Narrow columns (a side panel takes a third of the page) are where
            # the width estimate errs; leave a line's slack inside the box.
            box_h = min(frame.h, max(_COLUMN_MIN_H, col_h + 0.45 + _COLUMN_TEXT_INSET))
            if card in _PANEL_CARDS:
                box_h = max(box_h, frame.h * _COLUMN_FILL)
            if any(f.role == "takeaway" and f.h > 3 for f in frames):
                # Beside a side panel the columns run its full height, so the
                # white cards and the dark panel share a top and a bottom.
                box_h = frame.h - _CARD_PAD
            if card == "filled":
                # Inside the margins like every other container (the text is
                # inset instead): overhanging the frame broke the page grid.
                parts.append(
                    _rect_xml(
                        frame.x,
                        frame.y - _CARD_PAD,
                        frame.w,
                        box_h + 2 * _CARD_PAD,
                        fill=theme.surface,
                        corner_radius_cm=_CARD_CORNER,
                        style_name=graphics.name_for_fill(theme.surface),
                    )
                )
            else:
                # A rule or outline marks the column's own measure; the pad a
                # filled card overhangs by would push it past the page margin.
                top = frame.y - (_CARD_PAD if card in ("outlined", "framed") else 0.05)
                parts.append(
                    _visual_card_xml(
                        frame.x,
                        top,
                        frame.w,
                        box_h + (frame.y - top) + _CARD_PAD,
                        theme,
                        graphics,
                        card,
                    )
                )

        # closing message (title + optional subtitle). Inverted styles paint it
        # in the page colour on the accent ground; light ones keep title ink —
        # bg-on-bg would have made the whole closing page blank.
        if role == "message":
            if layout == "closing" and not slide.bullets:
                frame = replace(frame, y=6)
            message_color = theme.bg if layout in inverted else theme.title_color
            msg_style = styles.name_for(
                theme.h1_pt,
                frame.bold,
                frame.center,
                message_color,
                font=theme.font_display,
                # The subtitle sat flush under the message with no air.
                margin_bottom="0.25cm" if slide.subtitle else None,
            )
            inner = ""
            if slide.kicker:
                # Grown upward by the eyebrow's own height, so the message and
                # the action row below keep the geometry the budget charged.
                k = (
                    _measured_text_h(
                        slide.kicker, theme.caption_pt, _wrap_width(frame.w), 1.0
                    )
                    - _TEXT_BOX_INSET_Y
                )
                frame = replace(frame, y=frame.y - k, h=frame.h + k)
                # The closing page is a chapter of its own (「結語」「請求事項」);
                # its eyebrow was accepted and silently dropped.
                inner += _kicker_paragraph_xml(
                    slide.kicker,
                    styles,
                    theme,
                    _blend(theme.bg, theme.accent, 0.22)
                    if layout in inverted
                    else None,
                    center=frame.center,
                )
            inner += (
                f'<text:p text:style-name="{_attr(msg_style)}">'
                f"{_broken_xml(slide.title, theme.h1_pt, frame.w)}</text:p>"
            )
            if slide.subtitle:
                sub_style = styles.name_for(
                    theme.body_pt, False, frame.center, message_color
                )
                inner += (
                    f'<text:p text:style-name="{_attr(sub_style)}">'
                    f"{_broken_xml(slide.subtitle, theme.body_pt, frame.w)}</text:p>"
                )
            parts.append(_frame_box_xml(frame, inner))
            continue

        # agenda: explicit numbered items with accent-tinted 01/02… numbers.
        if role == "items":
            parts.append(_numbered_items_xml(lines, frame, theme, styles, spans))
            continue

        # chart insights: a caption-size bullet list.
        if role == "insights":
            inner = _list_xml(
                lines,
                styles,
                size_pt=theme.caption_pt,
                color=theme.text_color,
                style_name=_LIST_STYLE_NAME,
                frame_w=frame.w,
            )
            # Beside the chart, centred on it: pinned to the top, two notes
            # floated in the corner over an empty quadrant.
            notes_h = sum(
                _measured_text_h(_line_text(x), theme.caption_pt,
                                 _wrap_width(frame.w - _list_indent(1)), 1.45)
                + 0.3
                for x in lines
            )
            if notes_h < frame.h:
                frame = replace(frame, y=frame.y + (frame.h - notes_h) * 0.45,
                                h=notes_h + 0.2)
            parts.append(_frame_box_xml(frame, inner))
            continue

        # comparison columns: first item is a bold accent column header, the rest
        # are normal bullets (contract decided by the controller).
        if role in _CARD_ROLES and card in _PANEL_CARDS:
            frame = replace(
                frame,
                x=frame.x + _COLUMN_TEXT_INSET,
                y=frame.y + _COLUMN_TEXT_INSET,
                w=frame.w - 2 * _COLUMN_TEXT_INSET,
            )
        if _col_layout(slide) == "comparison" and role in ("left", "right"):
            header_style = styles.name_for(col_pt, True, False, theme.accent)
            inner = (
                f'<text:p text:style-name="{_attr(header_style)}">'
                f"{escape(_line_text(lines[0]))}</text:p>"
            )
            rest = lines[1:]
            if rest:
                inner += _list_xml(
                    rest,
                    styles,
                    size_pt=col_pt,
                    color=theme.text_color,
                    style_name=_LIST_STYLE_NAME,
                    frame_w=frame.w,
                )
            parts.append(_frame_box_xml(frame, inner))
            continue

        # Content-page title: the style's heading mark + optional kicker eyebrow
        # (accent, letter-spaced) rendered above the title text.
        if role == "title":
            if layout not in PLAIN_LAYOUTS:
                parts.append(_title_mark_xml(style, frame, theme, graphics))
            # Inverted grounds (per style) and a filled heading band both put
            # the text on accent, so it has to flip to the page colour.
            on_accent = layout in inverted or (
                style.title_mark == "block" and layout not in PLAIN_LAYOUTS
            )
            # A bleed cover paints the accent ground behind the title too.
            if layout == "title" and _cover_is_bleed(style):
                on_accent = True
            color = theme.bg if on_accent else theme.title_color
            title_pt = heading_pt(_line_text(lines[0]), frame.w, frame.size_pt)
            title_style = styles.name_for(
                title_pt,
                frame.bold,
                frame.center,
                color,
                font=theme.font_display,
            )
            inner = ""
            if slide.kicker and (layout not in PLAIN_LAYOUTS or layout == "title"):
                inner += _kicker_paragraph_xml(
                    slide.kicker,
                    styles,
                    theme,
                    _blend(theme.bg, theme.accent, 0.22) if on_accent else None,
                    center=frame.center,
                )
            inner += "".join(
                f'<text:p text:style-name="{_attr(title_style)}">'
                f"{_broken_xml(_line_text(line), title_pt, frame.w)}</text:p>"
                for line in lines
            )
            parts.append(_frame_box_xml(frame, inner))
            continue

        # Standard semantic list (title-content bullets, two-col columns).
        if role in LIST_ROLES and not frame.center:
            list_pt = col_pt if col_pt is not None else frame.size_pt
            if layout == "title-content" and col_pt is None:
                list_pt = _grown_list_pt(slide, theme, frame, layout)
            inner = _list_xml(
                lines,
                styles,
                size_pt=list_pt,
                color=theme.text_color,
                style_name=_LIST_STYLE_NAME,
                frame_w=frame.w,
            )
            parts.append(_frame_box_xml(frame, inner))
            continue

        # Bare centred / single-line text (subtitle, fact, quote, attribution,
        # big-fact caption bullets).
        size_pt = frame.size_pt
        if role == "fact":
            # fit-to-width: draw the fact at the largest size that keeps it on
            # one line (floor = h1_pt), computed above.
            size_pt = (
                bigfact_fact_pt if bigfact_fact_pt is not None else theme.display_pt
            )
            color = theme.accent
        elif role == "quote":
            size_pt = theme.h1_pt
            color = theme.text_color
        elif role == "attribution":
            size_pt = theme.caption_pt
            color = theme.muted
        elif role == "bullets" and layout == "big-fact":
            color = theme.muted
            # caption never overlaps: drop it below the fact's real height.
            if bigfact_caption_y is not None:
                frame = replace(frame, y=bigfact_caption_y)
        elif role == "subtitle" and layout == "title" and _cover_is_bleed(style):
            # The bleed cover puts the subtitle on the accent ground too; ink
            # colour there is dark-on-dark, i.e. an unreadable second line.
            color = _blend(theme.bg, _section_fill(theme), 0.22)
        else:
            color = theme.text_color

        display_font = (
            theme.font_display if role in {"fact", "quote"} else theme.font_body
        )
        style_name = styles.name_for(
            size_pt,
            frame.bold,
            frame.center,
            color,
            font=display_font,
        )
        # The fact is sized to one line and measured as the renderer's own
        # wrap; forcing breaks into it split 「62,48 / 0人次」 and the caption,
        # placed for one line, printed through the second.
        parts.append(
            _frame_xml(frame, lines, style_name, None if role == "fact" else size_pt)
        )

    if slide.sources:
        parts.append(_sources_xml(slide.sources, theme, styles, spans))

    if slide.notes:
        notes_style = styles.name_for(_NOTES_SIZE_PT, False, False, theme.text_color)
        parts.append(_notes_xml(slide.notes, notes_style))

    _BODY_ANCHOR_TOP = False
    # No takeaway to close up under: the body block alone is set a little
    # above centre in its area, instead of hanging from the top of it.
    if body_start is not None and not slide.takeaway:
        if pending is not None:
            bottom = _xml_bottom("".join(parts[pending:]))
            if bottom is not None:
                body_bottom = max(body_bottom or 0.0, bottom)
        top = _xml_top("".join(parts[body_start:]))
        if body_bottom is not None and top is not None:
            slack = area_bottom - body_bottom
            area_top = min(top, next(f.y for f in frames if f.role in _SIZED_BODY_ROLES))
            want = area_top + (slack + (top - area_top)) * _BLOCK_OPTICAL
            dy = want - top
            if dy > 0.05 and body_bottom + dy <= area_bottom:
                parts[body_start:] = [_shift_y(x, dy) for x in parts[body_start:]]
    master = _PLAIN_MASTER_NAME if layout in PLAIN_LAYOUTS else _MASTER_PAGE_NAME
    dp_style = (
        _SECTION_DRAWING_PAGE_STYLE
        if layout in inverted
        else _DRAWING_PAGE_STYLE
    )
    return (
        f'<draw:page draw:name="page{index}"'
        f' draw:style-name="{dp_style}"'
        f' draw:master-page-name="{master}">'
        f"{''.join(parts)}"
        f"</draw:page>"
    )


# ---------------------------------------------------------------------------
# Pure builders for the three XML parts
# ---------------------------------------------------------------------------


def _drawing_page_style_xml(
    name: str, fill_attrs: str, *, display_page_number: bool = False
) -> str:
    """Build a ``style:family="drawing-page"`` automatic style element."""
    extra = (
        ' presentation:display-page-number="true"' if display_page_number else ""
    )
    return (
        f'<style:style style:name="{_attr(name)}" style:family="drawing-page">'
        f"<style:drawing-page-properties {fill_attrs}{extra}/>"
        f"</style:style>"
    )


_KICKER_CHAPTER = re.compile(r"^\s*(\d{1,2})\s+(.+?)\s*$")


_CJK_THEN_LATIN = re.compile(r"([\u3400-\u9fff])([A-Za-z0-9])")
_LATIN_THEN_CJK = re.compile(r"([A-Za-z0-9%])([\u3400-\u9fff])")


def _space_text(text: str) -> str:
    return _LATIN_THEN_CJK.sub(r"\1 \2", _CJK_THEN_LATIN.sub(r"\1 \2", text))


def _space_mixed_runs(p: Presentation) -> Presentation:
    """A space between CJK and Latin/digits (「雲端 GPU」, 「平板 6 台」).

    LibreOffice's automatic CJK/Latin gap is off (it also padded full-width
    brackets); the Taiwanese convention is a real space, so the text gets one
    before anything is measured or drawn."""
    def walk(value):
        if isinstance(value, str):
            return _space_text(value)
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            return {
                k: (v if k in _NO_SPACE_FIELDS else walk(v)) for k, v in value.items()
            }
        return value

    data = p.model_dump(mode="json")
    data["slides"] = [walk(slide) for slide in data["slides"]]
    return Presentation.model_validate(data)


# Identifiers and machine fields keep their exact spelling.
_NO_SPACE_FIELDS = frozenset(
    {"layout", "id", "source", "target", "src", "url", "kind", "fit", "mode"}
)


def _chapter_key(name: str) -> str:
    """「研究方法 · 資料」 and 「研究方法 · 模型」 are one chapter."""
    return re.split(r"\s*[·・|｜/／—–]\s*", name, maxsplit=1)[0]


def _renumber_chapters(p: Presentation) -> Presentation:
    """Chapter numbers follow the order chapters first appear.

    Kickers are written page by page and drifted — 01, 02, 03, 01, 04 — so
    the running header contradicted the agenda. Numbers are reassigned by the
    order each chapter *name* first appears; decks that do not use the
    "NN 章節名" pattern are returned untouched."""
    order: dict[str, int] = {}
    written: dict[str, set[int]] = {}
    sequence: list[int] = []
    kickers = []
    for slide in p.slides:
        m = _KICKER_CHAPTER.match(slide.kicker or "")
        kickers.append(m)
        if m:
            key = _chapter_key(m.group(2))
            written.setdefault(key, set()).add(int(m.group(1)))
            sequence.append(int(m.group(1)))
            if key not in order:
                order[key] = len(order) + 1
    if not order:
        return p
    consistent = all(len(nums) == 1 for nums in written.values()) and all(
        a <= b for a, b in itertools.pairwise(sequence)
    )
    if consistent:
        # The author's own numbering holds together (00 開場 included): keep it.
        return p
    slides = []
    for slide, m in zip(p.slides, kickers, strict=True):
        if m:
            width = len(m.group(1))
            number = f"{order[_chapter_key(m.group(2))]:0{max(width, 2)}d}"
            slide = slide.model_copy(update={"kicker": f"{number} {m.group(2)}"})
        slides.append(slide)
    return p.model_copy(update={"slides": slides})


def _bigfact_max_pt(theme: Theme) -> int:
    """The hero number is the page: half again the display size (to 96pt),
    which fills the band a 54pt figure left 40% empty below."""
    return min(96, round(theme.display_pt * 1.5))


def build_content_xml(
    p: Presentation,
    theme: Theme,
    resolved_images: Mapping[int, tuple[str, AssetBlob]] | None = None,
    logo: _LogoAsset | None = None,
    style: Style | None = None,
) -> str:
    """Build ``content.xml`` for a presentation. Pure function.

    ``logo`` is the resolved cover mark (``render_odp`` packages the bytes and
    passes the part reference in); ``None`` means the deck carries no logo, or
    the one it named could not be resolved — either way the pages render exactly
    as they did before branding existed. ``style`` defaults to the deck's own
    (``resolve_style``), so a caller holding only a Presentation gets the look
    that Presentation asked for.
    """
    style = style or resolve_style(p)
    styles = _ParagraphStyles(theme.font)
    # Graphic styles for shapes drawn on pages (accent bars, etc.).
    graphics = _GraphicStyles()
    # Text-span styles for inline accent runs (agenda's 01/02… numbers).
    spans = _SpanStyles(theme.font)

    # Only "section" slides carry an auto-computed 1-based ordinal (the giant
    # chapter number); closing pages are inverted too but must not consume or
    # display an ordinal, so they are excluded here.
    section_ordinal: dict[int, int] = {}
    for i, slide in enumerate(p.slides):
        if slide.layout == "section":
            # The chapter number the author wrote wins — on the divider's own
            # kicker, else on the first page of its chapter. Counting dividers
            # printed "01" over a chapter every kicker called "02".
            nxt = p.slides[i + 1].kicker if i + 1 < len(p.slides) else ""
            written = _leading_number(slide.kicker) or _leading_number(nxt)
            section_ordinal[i] = written or len(section_ordinal) + 1

    p = _renumber_chapters(p)
    p = _space_mixed_runs(p)
    # Build pages first so every referenced paragraph/graphic/span style is
    # registered.
    pages = "".join(
        _page_xml(
            i,
            slide,
            theme,
            styles,
            graphics,
            spans,
            section_ordinal.get(i),
            (resolved_images or {}).get(i),
            p.branding,
            logo,
            style,
        )
        for i, slide in enumerate(p.slides)
    )

    # Default content drawing-page carries the bg fill and surfaces the master's
    # page-number placeholder. The accent (inverted) drawing-page is emitted
    # whenever any inverted page (section OR closing) needs it — decoupled from
    # the section-ordinal count so a closing-only deck still gets its accent fill.
    drawing_pages = _drawing_page_style_xml(
        _DRAWING_PAGE_STYLE, _page_fill_attrs(theme), display_page_number=True
    )
    if any(slide.layout in inverted_layouts(style) for slide in p.slides):
        drawing_pages += _drawing_page_style_xml(
            _SECTION_DRAWING_PAGE_STYLE,
            f'draw:fill="solid" draw:fill-color="{_attr(_section_fill(theme))}"',
        )

    automatic_styles = (
        "<office:automatic-styles>"
        f"{styles.xml()}"
        f"{graphics.xml()}"
        f"{spans.xml()}"
        f"{_list_style_xml(_LIST_STYLE_NAME, theme)}"
        f"{drawing_pages}"
        f'<style:style style:name="{_GRAPHIC_STYLE}" style:family="graphic">'
        f'<style:graphic-properties draw:fill="none" draw:stroke="none"/>'
        f"</style:style>"
        "</office:automatic-styles>"
    )

    return (
        f"{_XML_DECL}"
        f"<office:document-content {_ns_decls(_CONTENT_NS)}"
        f' office:version="1.2">'
        f"{automatic_styles}"
        f"<office:body><office:presentation>"
        f"{pages}"
        f"</office:presentation></office:body>"
        f"</office:document-content>"
    )


def _furniture_para_xml(
    name: str,
    font: str,
    size_pt: int,
    color: str,
    *,
    align: str,
    letter_spacing: str | None = None,
) -> str:
    """Build a master-page furniture paragraph style (three-track CJK sizing)."""
    spacing = (
        f' fo:letter-spacing="{_attr(letter_spacing)}"'
        if letter_spacing is not None
        else ""
    )
    return (
        f'<style:style style:name="{_attr(name)}" style:family="paragraph">'
        f'<style:paragraph-properties fo:text-align="{align}"'
        ' style:text-autospace="none"/>'
        f"<style:text-properties{_font_size_attrs(size_pt)}"
        f' fo:color="{_attr(color)}"{spacing}'
        f' style:font-name="{_attr(font)}" style:font-name-asian="{_attr(font)}"/>'
        f"</style:style>"
    )


def _furniture_styles_xml(theme: Theme, style: Style) -> str:
    """Automatic styles the "Standard" master's furniture frames/line reference.

    Only what this 版式 actually draws is emitted — an automatic style nobody
    references is dead weight in every file the user ships.
    """
    line = (
        f'<style:style style:name="{_MP_LINE_STYLE}" style:family="graphic">'
        f'<style:graphic-properties draw:fill="none" draw:stroke="solid"'
        f' svg:stroke-color="{_attr(theme.muted)}"'
        f' svg:stroke-width="{_pt(_FOOTER_LINE_WIDTH_PT)}"/>'
        f"</style:style>"
    )
    frame = (
        f'<style:style style:name="{_MP_FRAME_STYLE}" style:family="graphic">'
        f'<style:graphic-properties draw:fill="none" draw:stroke="none"/>'
        f"</style:style>"
    )
    # metropolis' progress track: a faint accent strip at the very bottom edge.
    track = (
        f'<style:style style:name="{_MP_TRACK_STYLE}" style:family="graphic">'
        f'<style:graphic-properties draw:fill="solid"'
        f' draw:fill-color="{_attr(_blend(theme.accent, theme.bg, 0.45))}"'
        f' draw:stroke="none"/>'
        f"</style:style>"
    )
    page_number = _furniture_para_xml(
        _MP_PAGENUM_STYLE, theme.font, theme.caption_pt, theme.muted, align="end"
    )
    kicker = _furniture_para_xml(
        _MP_KICKER_STYLE,
        theme.font,
        theme.caption_pt,
        theme.muted,
        align="start",
        # The footer carries the deck title, which may be Latin: the kicker's
        # 0.15cm tracking spread "NeighFridge" into "N e i g h F r i d g e".
        letter_spacing=_FOOTER_LETTER_SPACING,
    )
    if style.footer == "none":
        return frame
    if style.footer == "number":
        return frame + page_number
    if style.footer == "progress":
        return frame + track + page_number
    return line + frame + page_number + kicker


def _master_furniture_xml(theme: Theme, title: str, style: Style) -> str:
    """Standard master-page furniture: footer line, page number, kicker title.

    The kicker carries the presentation title (a per-page kicker field arrives in
    Task 14.1). The page-number frame is a ``presentation:class="page-number"``
    placeholder holding ``<text:page-number/>``; content pages surface it via the
    drawing-page ``presentation:display-page-number`` flag.
    """
    footer_line = (
        f'<draw:line draw:style-name="{_MP_LINE_STYLE}"'
        f' draw:layer="{_FURNITURE_LAYER}"'
        f' svg:x1="{_cm(_FOOTER_LINE_X1)}" svg:y1="{_cm(_FOOTER_LINE_Y)}"'
        f' svg:x2="{_cm(_FOOTER_LINE_X2)}" svg:y2="{_cm(_FOOTER_LINE_Y)}"/>'
    )
    page_number = (
        f'<draw:frame draw:style-name="{_MP_FRAME_STYLE}"'
        f' draw:layer="{_FURNITURE_LAYER}"'
        f' presentation:class="page-number"'
        f' svg:x="{_cm(_PAGENUM_X)}" svg:y="{_cm(_FURNITURE_Y)}"'
        f' svg:width="{_cm(_PAGENUM_W)}" svg:height="{_cm(_FURNITURE_H)}">'
        f'<draw:text-box><text:p text:style-name="{_MP_PAGENUM_STYLE}">'
        f"<text:page-number/></text:p></draw:text-box></draw:frame>"
    )
    kicker = (
        f'<draw:frame draw:style-name="{_MP_FRAME_STYLE}"'
        f' draw:layer="{_FURNITURE_LAYER}"'
        f' svg:x="{_cm(_FOOTER_LINE_X1)}" svg:y="{_cm(_FURNITURE_Y)}"'
        f' svg:width="{_cm(_KICKER_W)}" svg:height="{_cm(_FURNITURE_H)}">'
        f'<draw:text-box><text:p text:style-name="{_MP_KICKER_STYLE}">'
        f"{escape(title)}</text:p></draw:text-box></draw:frame>"
    )
    # 版式 decides how much furniture a content page carries. metropolis' one
    # decorative element is a slim progress track at the very bottom edge; the
    # consulting layout keeps the rule + running head + number; the stage and
    # Swiss layouts keep only the number; zen keeps nothing.
    if style.footer == "none":
        return ""
    if style.footer == "number":
        return page_number
    if style.footer == "progress":
        track = (
            f'<draw:rect draw:style-name="{_MP_TRACK_STYLE}"'
            f' draw:layer="{_FURNITURE_LAYER}"'
            f' svg:x="0cm" svg:y="{_cm(_PROGRESS_Y)}"'
            f' svg:width="{_cm(PAGE_W)}" svg:height="{_cm(_PROGRESS_H)}"/>'
        )
        return track + page_number
    return footer_line + page_number + kicker


def build_styles_xml(
    theme: Theme, title: str = "", style: Style | None = None
) -> str:
    """Build ``styles.xml`` (page layout + master pages + bg). Pure function.

    Two master pages are defined: "Standard" (the style's footer furniture) for
    content pages, and "Plain" (no furniture) for title/section pages. ``title``
    supplies the kicker text. The dark preset gets a subtle two-stop linear
    gradient background; light presets keep a flat solid fill.
    """
    style = style or get_style(None)
    font_family = f"'{theme.font}','微軟正黑體',sans-serif"
    # office:styles carries the layer-set (required for master furniture to
    # render — see _LAYER_SET_XML) plus, for the dark preset, the bg gradient.
    gradient = ""
    if _uses_gradient_bg(theme):
        gradient = (
            f'<draw:gradient draw:name="{_attr(_GRADIENT_NAME)}"'
            f' draw:display-name="ODForge Background" draw:style="linear"'
            f' draw:start-color="{_attr(theme.bg)}"'
            f' draw:end-color="{_attr(_lighten(theme.bg, 0.16))}"'
            f' draw:start-intensity="100%" draw:end-intensity="100%"'
            f' draw:angle="450" draw:border="0%"/>'
        )
    office_styles = f"<office:styles>{_LAYER_SET_XML}{gradient}</office:styles>"
    page_fill = _page_fill_attrs(theme)
    return (
        f"{_XML_DECL}"
        f"<office:document-styles {_ns_decls(_STYLES_NS)}"
        f' office:version="1.2">'
        f"<office:font-face-decls>"
        f'<style:font-face style:name="{_attr(theme.font)}"'
        f' svg:font-family="{_attr(font_family)}"/>'
        f"</office:font-face-decls>"
        f"{office_styles}"
        f"<office:automatic-styles>"
        f'<style:page-layout style:name="PM1">'
        f'<style:page-layout-properties fo:page-width="{_cm(PAGE_W)}"'
        f' fo:page-height="{_cm(PAGE_H)}" style:print-orientation="landscape"'
        f' fo:margin-top="0cm" fo:margin-bottom="0cm"'
        f' fo:margin-left="0cm" fo:margin-right="0cm"/>'
        f"</style:page-layout>"
        f'<style:style style:name="{_DRAWING_PAGE_STYLE}"'
        f' style:family="drawing-page">'
        f"<style:drawing-page-properties {page_fill}/>"
        f"</style:style>"
        f"{_furniture_styles_xml(theme, style)}"
        f"</office:automatic-styles>"
        f"<office:master-styles>"
        f'<style:master-page style:name="{_MASTER_PAGE_NAME}"'
        f' style:page-layout-name="PM1"'
        f' draw:style-name="{_DRAWING_PAGE_STYLE}">'
        f"{_master_furniture_xml(theme, title, style)}"
        f"</style:master-page>"
        f'<style:master-page style:name="{_PLAIN_MASTER_NAME}"'
        f' style:page-layout-name="PM1"'
        f' draw:style-name="{_DRAWING_PAGE_STYLE}"/>'
        f"</office:master-styles>"
        f"</office:document-styles>"
    )


def build_meta_xml(title: str) -> str:
    """Build ``meta.xml`` carrying the document title and generator. Pure."""
    return (
        f"{_XML_DECL}"
        f"<office:document-meta {_ns_decls(_META_NS)} office:version=\"1.2\">"
        f"<office:meta>"
        f"<meta:generator>ODForge</meta:generator>"
        f"<dc:title>{escape(title)}</dc:title>"
        f"</office:meta>"
        f"</office:document-meta>"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def render_odp(
    p: Presentation,
    out_path: Path,
    *,
    assets: Mapping[str, AssetInput] | None = None,
    image_provider: ImageProvider | None = None,
) -> Path:
    """Render presentation ``p`` to a native ``.odp`` file at ``out_path``."""
    # resolve_design returns the preset THEMES[p.theme] for v1 decks (design is
    # None) and a custom Theme built from the per-deck DesignSpec otherwise, so a
    # deck carrying design tokens actually renders with them.
    theme = resolve_design(p)
    style = resolve_style(p)
    normalized_assets = normalize_assets(assets)
    provider = image_provider
    if provider is None and any(
        slide.image is not None and bool(slide.image.prompt)
        for slide in p.slides
    ):
        provider = configured_image_provider()

    # Materialize provider output once. The slide is rewritten to asset:// and,
    # when the caller supplied a mutable asset map (Web/CLI do), the bytes are
    # cached there for deterministic QA re-renders and single-page regeneration.
    if provider is not None:
        for slide in p.slides:
            image = slide.image
            if image is None or image.src or not image.prompt:
                continue
            try:
                generated = provider.generate(image.prompt)
            except MediaError:
                continue
            digest = hashlib.sha256(generated.data).hexdigest()[:16]
            asset_id = f"generated-{digest}"
            normalized_assets[asset_id] = generated
            if isinstance(assets, MutableMapping):
                assets[asset_id] = generated
            slide.image = image.model_copy(update={"src": f"asset://{asset_id}"})

    resolved_images: dict[int, tuple[str, AssetBlob]] = {}
    image_parts: dict[str, bytes] = {}
    for index, slide in enumerate(p.slides):
        if slide.image is None:
            continue
        try:
            blob = resolve_image(slide.image, normalized_assets)
        except MediaError:
            # Unsafe/unavailable model-proposed sources render as a visible
            # placeholder. Explicit app-owned uploads are validated earlier.
            blob = None
        if blob is None:
            continue
        digest = hashlib.sha256(blob.data).hexdigest()[:16]
        href = f"Pictures/image-{digest}{blob.extension}"
        image_parts[href] = blob.data
        resolved_images[index] = (href, blob)

    # Cover mark. An unresolvable reference (asset dropped, bytes rejected) is
    # NOT an error: the deck renders without the mark rather than failing a
    # whole generation over a logo. It is app-owned, so this only happens when
    # the upload itself went missing.
    logo: _LogoAsset | None = None
    if p.branding is not None and p.branding.logo:
        blob = normalized_assets.get(p.branding.logo.removeprefix("asset://"))
        if blob is not None:
            digest = hashlib.sha256(blob.data).hexdigest()[:16]
            href = f"Pictures/logo-{digest}{blob.extension}"
            image_parts[href] = blob.data
            logo = _LogoAsset(href, blob.width, blob.height)

    parts: dict[str, str | bytes] = {
        "content.xml": build_content_xml(p, theme, resolved_images, logo, style),
        "styles.xml": build_styles_xml(theme, p.title, style),
        "meta.xml": build_meta_xml(p.title),
    }
    parts.update(image_parts)
    # Only ship the decoration SVG when a title page actually references it —
    # keeps non-title decks free of an unused Pictures part. build_content_xml
    # emits the <draw:image> under the same conditions (title page + a style
    # whose cover decoration is the dot lattice).
    if style.cover_deco == "dots" and any(
        slide.layout in _DECO_LAYOUTS for slide in p.slides
    ):
        parts[_DECO_HREF] = _svg_decoration(theme)
    _embed_deck_fonts(p, theme, parts)
    return write_odf_package(Path(out_path), ODP_MIMETYPE, parts)


def _embed_deck_fonts(p: Presentation, theme: Theme, parts: dict) -> None:
    """Carry the deck's CJK faces inside the package (see :mod:`fontembed`).

    Rewrites the font-face declarations in both XML parts to point at the
    embedded, subset font files. A no-op when embedding is switched off
    (``ODFORGE_EMBED_FONTS=0``), fontTools is missing, or the source fonts are
    not on this machine — the deck then renders exactly as before.
    """
    if os.environ.get("ODFORGE_EMBED_FONTS", "1") == "0":
        return
    families = list(dict.fromkeys([theme.font_body, theme.font_display]))
    text = p.title + "".join(_string_values(p.model_dump(mode="json")))
    embedded = embed_fonts(set(families), text)
    if not embedded:
        return
    decls = (
        "<office:font-face-decls>"
        + font_face_decls(families, embedded, _attr)
        + "</office:font-face-decls>"
    )
    styles = parts["styles.xml"]
    start = styles.index("<office:font-face-decls>")
    end = styles.index("</office:font-face-decls>") + len("</office:font-face-decls>")
    parts["styles.xml"] = styles[:start] + decls + styles[end:]
    content = parts["content.xml"]
    root_end = content.index(">", content.index("<office:document-content")) + 1
    parts["content.xml"] = content[:root_end] + decls + content[root_end:]
    for font in embedded:
        parts[font.part] = font.data
