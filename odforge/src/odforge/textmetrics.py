"""Programmatic layout-budget gate — does a slide's text fit its frames?

This is a *deterministic* pre-render overflow check: the thing open-slide asks
its prompt to police, ODForge enforces in code. It is pure (no I/O, no rendering)
and depends only on the layout geometry (:data:`odforge.themes.LAYOUTS`) and the
resolved design tokens (:class:`odforge.themes.Theme`).

Two public functions:

* :func:`estimate_height_cm` — a monospace-ish text-height model. Each glyph is
  charged an em-width (CJK ≈ 1.0em, Latin per its measured advance in
  :data:`_ASCII_WIDTHS`, anything else 0.6em); the run wraps at the frame width
  and each wrapped line is one ``size_pt * PT_TO_CM * line_height``
  tall. Newlines split the text into independent paragraphs.
* :func:`check_budget` — walks a slide's frames, accumulates each frame's content
  height (paragraph heights + inter-item margins for bullet lists), and returns a
  human-readable message for every frame whose content overruns its box. An empty
  list means the slide fits.

**Mirroring the renderer.** The estimator only works if it charges each frame the
same font size the renderer draws it at. :func:`_frame_content_height` therefore
follows ``render.odp._page_xml`` branch-for-branch: legacy roles render at their
``Frame.size_pt`` while the Task-14.1 roles are re-sized from theme tokens
(quote → ``h1_pt``, attribution → ``caption_pt``, message → ``h1_pt``,
items → ``body_pt``, insights → ``caption_pt``, fact → ``display_pt``). Bullet
lists carry the renderer's 145% line-height and 0.35cm inter-item margin; nested
(level-2) children render ~1cm narrower. Chart bars auto-fit, so ``chart-area``
is never estimated — but a chart page's ``insights`` bullets are.
"""

from __future__ import annotations

import math
import re

from odforge.ir import BulletItem, Slide
from odforge.themes import (
    LIST_ROLES,
    PLAIN_LAYOUTS,
    TAKEAWAY_Y,
    Frame,
    Theme,
    slide_frames,
)

# ── constants ──────────────────────────────────────────────────────────────
# Points → centimetres (1pt = 1/72in, 1in = 2.54cm ⇒ 0.035277…; rounded).
PT_TO_CM = 0.03528

# Per-glyph advance widths, in em (fraction of the point size). A CJK ideograph
# is ~square; Latin is measured per glyph (_ASCII_WIDTHS), anything else ~0.6em.
_CJK_EM = 1.0
_ASCII_EM = 0.6

# The renderer's loose-bullet typography (render/odp.py): list paragraphs get a
# 145% line-height and a 0.35cm bottom margin, and level-2 children indent deeper
# (≈1cm less usable text width than a level-1 item).
_LINE_HEIGHT = 1.45
_MARGIN_BOTTOM_CM = 0.35
_LEVEL2_NARROW_CM = 1.0

# Vertical breathing room a text-box eats top+bottom before content can overflow.
# Small on purpose: the gate should fire on real overruns, not hairline ones.
_FRAME_PADDING_CM = 0.2

# Lowest y (cm) a big-fact caption may extend to before it collides with the
# master-page footer furniture (render.odp._FOOTER_LINE_Y == 14.72). Duplicated
# here rather than imported: render.odp imports textmetrics, never the reverse,
# so this module must stay free of a render dependency.
_BIGFACT_USABLE_BOTTOM_CM = 14.72

# PLAIN_LAYOUTS (bare title frames) and LIST_ROLES (semantic bullet lists) are
# imported from odforge.themes — the single source shared with render.odp so the
# budget gate can never drift from what the renderer actually draws.

# CJK / full-width Unicode blocks charged a full em. Kept deliberately small and
# documented rather than exhaustive (the brief's "keep it simple"):
#   3000–303F  CJK symbols & punctuation
#   3040–30FF  Hiragana + Katakana
#   3400–4DBF  CJK Unified Ideographs Extension A
#   4E00–9FFF  CJK Unified Ideographs
#   FF00–FF60  Full-width ASCII variants + full-width punctuation
#   FFE0–FFE6  Full-width signs
# Anything else (Latin, digits, half-width punctuation) is charged ASCII width.
_CJK_RANGES: tuple[tuple[int, int], ...] = (
    (0x3000, 0x303F),
    (0x3040, 0x30FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xFF00, 0xFF60),
    (0xFFE0, 0xFFE6),
    # Drawn full-width by CJK fonts: circled numbers, geometric shapes (the
    # "○" in 陳○○ masking), radicals, bopomofo, enclosed/compat ideographs,
    # and the supplementary ideograph planes. Counting them as 0.55em made a
    # table row one line short and its text printed into the next row.
    (0x0370, 0x03FF),  # Greek (κ, α…) — full-width in the CJK face
    (0x2190, 0x21FF),
    (0x2200, 0x22FF),
    (0x2460, 0x24FF),
    (0x25A0, 0x25FF),
    (0x2E80, 0x2FDF),
    (0x3100, 0x312F),
    (0x3200, 0x33FF),
    (0xF900, 0xFAFF),
    (0x20000, 0x3134F),
)


def _is_cjk(ch: str) -> bool:
    """True when ``ch`` is a full-width (CJK / kana / full-width form) glyph."""
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


# Printable ASCII advance widths (em), the wider of Noto Sans / Serif TC
# regular and bold, read off the font files. A flat 0.55em undercharged Latin
# by ~9% on average and by nearly half for 「m」「W」: at display sizes a
# 「520→160ms」 sized to one line wrapped, and its 「s」 printed on the label.
_ASCII_WIDTHS: dict[str, float] = {
    ch: w
    for chars, w in (
    (' ', 0.26),
    ("'", 0.33),
    (',.:;j|', 0.34),
    ('il', 0.35),
    ('!-', 0.37),
    ('[]{}', 0.38),
    ('()/\\f', 0.4),
    ('t', 0.42),
    ('I', 0.43),
    ('rs', 0.5),
    ('*?', 0.51),
    ('z', 0.52),
    ('c', 0.56),
    ('"J', 0.57),
    ('evy', 0.58),
    ('$012345678_a', 0.59),
    ('+9<=>^gx~', 0.6),
    ('#', 0.61),
    ('S', 0.62),
    ('Z`o', 0.63),
    ('Lq', 0.64),
    ('Fk', 0.65),
    ('d', 0.66),
    ('EPbp', 0.67),
    ('u', 0.68),
    ('Thn', 0.69),
    ('CY', 0.7),
    ('B', 0.71),
    ('VX', 0.73),
    ('AR', 0.75),
    ('G', 0.76),
    ('K', 0.77),
    ('DOQ', 0.79),
    ('&', 0.8),
    ('N', 0.81),
    ('U', 0.82),
    ('H', 0.86),
    ('w', 0.87),
    ('%', 0.97),
    ('M', 1.0),
    ('@m', 1.01),
    ('W', 1.09),
    )
    for ch in chars
}


def _em_width(text: str) -> float:
    """Total advance width of ``text`` in em (CJK 1.0, measured Latin, else
    0.6)."""
    return sum(
        _CJK_EM if _is_cjk(ch) else _ASCII_WIDTHS.get(ch, _ASCII_EM) for ch in text
    )


def text_width_cm(text: str, size_pt: int) -> float:
    """Width (cm) ``text`` needs on a single line at ``size_pt``.

    The companion to :func:`estimate_height_cm`, under the same em-width model
    (CJK ≈ 1.0em, Latin measured per glyph). Used where a box must be sized *to* its text
    rather than the text wrapped into a fixed box — a diagram's edge-label chip,
    for instance, which at a fixed width covered the whole connector it sat on.
    """
    return _em_width(text) * size_pt * PT_TO_CM


_LINE_END_WASTE_EM = 0.9


def estimate_height_cm(
    text: str, size_pt: int, width_cm: float, line_height: float = _LINE_HEIGHT
) -> float:
    """Estimate the rendered height (cm) of ``text`` in a ``width_cm``-wide box.

    Model: each glyph advances an em-width (CJK ≈ 1.0em, Latin measured per glyph); a
    paragraph's em-run is ``size_pt * PT_TO_CM`` cm/em wide and wraps at
    ``width_cm``; every wrapped line is ``size_pt * PT_TO_CM * line_height`` tall
    (minimum one line, even for empty text). ``\\n`` splits the text into
    independent paragraphs whose heights sum.
    """
    width = width_cm if width_cm > 0 else 0.01  # guard degenerate widths
    line_cm = size_pt * PT_TO_CM * line_height
    total = 0.0
    em_cm = size_pt * PT_TO_CM
    for paragraph in text.split("\n"):
        run_cm = _em_width(paragraph) * em_cm
        lines = max(1, math.ceil(run_cm / width))
        if lines > 1:
            # A wrapped line rarely ends flush: the next glyph does not fit,
            # or kinsoku keeps 「，」 off a line start, or a Latin word moves
            # down whole. Charge each line ~half an em of that waste — in a
            # 5cm card column it is the difference between the two-line
            # estimate and the three lines LibreOffice actually sets.
            usable = max(width - _LINE_END_WASTE_EM * em_cm, em_cm)
            lines = max(1, math.ceil(run_cm / usable))
        total += lines * line_cm
    return total


def fact_font_size_pt(
    text: str, width_cm: float, display_pt: int, h1_pt: int
) -> int:
    """Largest big-fact font size (pt) that keeps ``text`` on a single line.

    Steps down 1pt at a time from ``display_pt`` and returns the first size at
    which ``text`` fits one line in a ``width_cm``-wide box under the same
    em-width model :func:`estimate_height_cm` uses (CJK ≈ 1.0em, Latin measured per glyph).
    ``h1_pt`` is a hard floor: if even at ``h1_pt`` the text still wraps, ``h1_pt``
    is returned (the renderer then drops the caption to keep the two from
    overlapping, and — in the unrescuable extreme — :func:`check_budget` reports).

    Shared by the renderer (the size it draws) and the budget gate (the size it
    charges) so the two can never disagree. Deterministic; a pure function of the
    text, width and the two size bounds.
    """
    width = width_cm if width_cm > 0 else 0.01
    em = _em_width(text)
    if em <= 0:
        return display_pt
    for size in range(display_pt, h1_pt, -1):
        if em * size * PT_TO_CM <= width:
            return size
    return h1_pt


def heading_pt(text: str, frame_w: float, size_pt: int) -> int:
    """The size a heading is drawn at: its frame size, unless a step of up to
    ~18% down keeps it on one line.

    A title one or two characters too long for its line wrapped and left a
    lone character on line two — the most visible tell of an unedited deck.
    When one line is out of reach even at the floor, the full size stands
    (two honest lines beat one cramped one). ``frame_w`` is the frame width;
    the 0.5cm is LibreOffice's text-box inset, as in the renderer.
    """
    floor = max(12, round(size_pt * 0.82))
    width = frame_w - 0.5
    em = _em_width(text)
    for size in range(size_pt, floor - 1, -1):
        if em * size * PT_TO_CM <= width:
            return size
    return size_pt


# ── frame content resolution (mirrors render.odp) ──────────────────────────


def _item_text_children(item) -> tuple[str, list]:
    """Return ``(text, children)`` for a bullet item (``str`` or ``BulletItem``)."""
    if isinstance(item, BulletItem):
        return item.text, list(item.children)
    return str(item), []


def _role_lines(slide: Slide, role: str) -> list:
    """The content items a frame's ``role`` contributes — mirrors
    ``render.odp._role_lines`` (an empty list means the frame is skipped)."""
    if role == "title":
        return [slide.title] if slide.title else []
    if role in ("subtitle", "lede"):
        return [slide.subtitle] if slide.subtitle else []
    if role == "takeaway":
        return [slide.takeaway] if slide.takeaway else []
    if role == "fact":
        return [slide.fact] if slide.fact else []
    if role == "quote":
        return [slide.quote] if slide.quote else []
    if role == "attribution":
        return [slide.attribution] if slide.attribution else []
    if role == "message":
        return [slide.title] if slide.title else []
    if role in ("bullets", "items", "insights"):
        return list(slide.bullets)
    if role == "left":
        return list(slide.left)
    if role == "right":
        return list(slide.right)
    return []


def _list_height(items: list, size_pt: int, width_cm: float) -> float:
    """Accumulated height of a bullet list: each item's paragraph height plus a
    ``_MARGIN_BOTTOM_CM`` inter-item gap; nested children render ~1cm narrower.
    """
    total = 0.0
    for item in items:
        text, children = _item_text_children(item)
        total += estimate_height_cm(text, size_pt, width_cm) + _MARGIN_BOTTOM_CM
        for child in children:
            child_text, _ = _item_text_children(child)
            total += (
                estimate_height_cm(child_text, size_pt, width_cm - _LEVEL2_NARROW_CM)
                + _MARGIN_BOTTOM_CM
            )
    return total


def _plain_height(lines: list, size_pt: int, width_cm: float) -> float:
    """Accumulated height of bare (non-list) paragraphs — one per line, no gap."""
    total = 0.0
    for line in lines:
        text, _ = _item_text_children(line)
        total += estimate_height_cm(text, size_pt, width_cm)
    return total


def _frame_content_height(
    slide: Slide, theme: Theme, frame: Frame, layout: str
) -> float | None:
    """Estimated content height (cm) for one frame, or ``None`` when the frame
    draws nothing (empty content) or auto-fitting shapes (``chart-area``).

    Branch order mirrors ``render.odp._page_xml`` exactly so the size charged to
    each role matches what the renderer actually draws.
    """
    role = frame.role

    # Shape-rendered areas fit their content inside the assigned box.
    if role in {"chart-area", "closing-actions", "table-area"}:
        return None

    lines = _role_lines(slide, role)
    if not lines:
        return None

    # lede: the renderer steps it down (heading_pt) to keep it on one line;
    # charging the full size reported a lede that drew on one line as 1.6cm.
    if role == "lede":
        size = heading_pt(slide.subtitle, frame.w, frame.size_pt)
        if text_width_cm(slide.subtitle, size) <= frame.w - 0.5:
            return estimate_height_cm("一", size, frame.w)
        return estimate_height_cm(slide.subtitle, size, frame.w)

    # closing message: title at h1_pt (+ optional subtitle at body_pt), plain.
    if role == "message":
        h = estimate_height_cm(slide.title, theme.h1_pt, frame.w)
        if slide.subtitle:
            h += estimate_height_cm(slide.subtitle, theme.body_pt, frame.w)
        return h

    # agenda: numbered items at body_pt with loose-bullet spacing. The renderer
    # (_numbered_items_xml) draws only each item's top-level text via _line_text
    # — BulletItem children are silently dropped — so children are NOT charged.
    if role == "items":
        total = 0.0
        for item in lines:
            text, _ = _item_text_children(item)
            total += (
                estimate_height_cm(text, theme.body_pt, frame.w) + _MARGIN_BOTTOM_CM
            )
        return total

    # chart insights: a caption-size bullet list.
    if role == "insights":
        return _list_height(lines, theme.caption_pt, frame.w)

    # comparison columns: bold header (body_pt, plain) + the rest as a body_pt list.
    if layout == "comparison" and role in ("left", "right"):
        header_text, _ = _item_text_children(lines[0])
        h = estimate_height_cm(header_text, theme.body_pt, frame.w)
        rest = lines[1:]
        if rest:
            h += _list_height(rest, theme.body_pt, frame.w)
        return h

    # content-page title: optional kicker eyebrow (caption_pt) + title (size_pt).
    if role == "title":
        h = 0.0
        if slide.kicker and layout not in PLAIN_LAYOUTS:
            h += estimate_height_cm(slide.kicker, theme.caption_pt, frame.w)
        for line in lines:
            text, _ = _item_text_children(line)
            h += estimate_height_cm(
                text, heading_pt(text, frame.w, frame.size_pt), frame.w
            )
        return h

    # standard semantic list (title-content bullets, two-col columns).
    if role in LIST_ROLES and not frame.center:
        return _list_height(lines, frame.size_pt, frame.w)

    # bare centred / single-line text (subtitle, fact, quote, attribution,
    # big-fact caption bullets). Size follows the renderer's per-role re-sizing.
    size_pt = frame.size_pt
    if role == "fact":
        size_pt = theme.display_pt
    elif role == "quote":
        size_pt = theme.h1_pt
    elif role == "attribution":
        size_pt = theme.caption_pt
    return _plain_height(lines, size_pt, frame.w)


def frame_content_height(
    slide: Slide, theme: Theme, frame: Frame, layout: str
) -> float | None:
    """Public name for :func:`_frame_content_height` — the renderer sizes the
    two-column cards to exactly the height this gate charges."""
    return _frame_content_height(slide, theme, frame, layout)


def _check_big_fact(slide: Slide, theme: Theme, label: str, style=None) -> list[str]:
    """Budget check for the big-fact layout — closes the fact-frame blind spot.

    The renderer auto-rescues an over-long fact: it shrinks the fact toward the
    ``h1_pt`` floor to keep it on one line and drops the caption below the fact's
    real height so the two never overlap (see ``render.odp._page_xml``). A fact
    that merely wraps is therefore handled in-engine and must NOT be reported.

    Feedback is warranted only in the unrescuable extreme: when the fact, even
    charged at its ``h1_pt`` floor, plus the caption reserved beneath it, cannot
    fit above the footer line (:data:`_BIGFACT_USABLE_BOTTOM_CM`). Only then does
    the message reach the Task 15.2 retry loop — self-rescue first, feedback last.
    """
    by_role = {f.role: f for f in slide_frames(slide, style)}
    fact_frame, caption_frame = by_role["fact"], by_role["bullets"]
    if not slide.fact:
        return []
    # Charge the fact at the shrink floor — the smallest the renderer will draw it.
    floor_h = estimate_height_cm(slide.fact, theme.h1_pt, fact_frame.w)
    gap = caption_frame.y - (fact_frame.y + fact_frame.h)
    caption_lines = _role_lines(slide, caption_frame.role)
    caption_h = (
        _plain_height(caption_lines, caption_frame.size_pt, caption_frame.w)
        if caption_lines
        else 0.0
    )
    reserved = (gap + caption_h) if caption_lines else 0.0
    usable_bottom = (
        TAKEAWAY_Y - 0.3 if slide.takeaway.strip() else _BIGFACT_USABLE_BOTTOM_CM
    )
    available = usable_bottom - fact_frame.y - reserved
    if floor_h > available:
        return [
            f"投影片「{label}」的 fact「{slide.fact}」即使縮到最小字級"
            f"（{theme.h1_pt}pt）仍需約 {floor_h:.1f}cm > 可用 {available:.1f}cm"
            f"（與下方說明文字疊放後會超出版面）— 請縮短 fact 文字。"
        ]
    return []


_SHAPE_LAYOUTS = frozenset(
    {"process", "timeline", "metrics", "dashboard", "diagram", "chart", "closing", "table"}
)


def check_budget(slide: Slide, theme: Theme, style=None) -> list[str]:
    """Return an overflow message for every frame whose text overruns its box.

    Walks ``LAYOUTS[slide.layout]``; for each frame, estimates the content height
    and compares it against the frame height less a small padding allowance
    (:data:`_FRAME_PADDING_CM`, 0.2cm total). An empty list means the whole slide
    fits. Messages name the slide, the frame role, and the estimated vs available
    height in cm — actionable feedback the retry loop (Task 15.2) feeds back to
    the LLM.

    The big-fact layout is special-cased (:func:`_check_big_fact`): because the
    renderer auto-shrinks the fact and pushes the caption clear of it, a plain
    per-frame walk would both mis-charge the fact (it is no longer drawn at
    ``display_pt``) and miss the true failure mode (fact + caption running off the
    page). That combined, floor-aware check replaces the walk for big-fact.
    """
    label = slide.title or slide.fact or slide.quote or "(未命名投影片)"
    if slide.layout == "big-fact":
        return _check_big_fact(slide, theme, label, style)
    messages: list[str] = []
    if slide.layout == "cards":
        # cards-area is shape-rendered (this walk charges it nothing) but rides
        # on unbounded ``bullets``. The renderer owns the fit, so it owns the
        # check — imported lazily because render.odp imports this module. The
        # walk below still covers the page-title frame.
        from odforge.render.odp import cards_budget_warnings

        messages.extend(cards_budget_warnings(slide, theme, style))
    elif slide.layout in _SHAPE_LAYOUTS:
        # Same ownership argument for every other shape layout: the renderer's
        # fit loop is the authority on what gets cut, so ask it.
        from odforge.render.odp import table_budget_warnings, truncation_warnings

        messages.extend(truncation_warnings(slide, theme, style))
        messages.extend(table_budget_warnings(slide, theme, style))
    for frame in slide_frames(slide, style):
        if frame.role == "takeaway" and frame.h > 3:
            continue  # a side panel: its text is fitted by the renderer
        content_h = _frame_content_height(slide, theme, frame, slide.layout)
        if content_h is None:
            continue
        available = frame.h - _FRAME_PADDING_CM
        if content_h > available:
            messages.append(
                f"投影片「{label}」的「{frame.role}」框內容超出版面："
                f"預估 {content_h:.1f}cm > 可用 {available:.1f}cm"
                f"（frame 高 {frame.h:g}cm）— 請精簡文字或改用其他版面。"
            )
    return messages


# ── deck-level checks ───────────────────────────────────────────────────────

_STRUCTURAL = frozenset({"agenda", "section", "quote", "big-fact"})
_REPEATABLE = frozenset({"cards", "process", "timeline", "metrics", "two-col", "comparison"})
_CN_DIGITS = dict(zip("兩二三四五六七八九十", (2, 2, 3, 4, 5, 6, 7, 8, 9, 10), strict=True))
# 「七項指標」「三大原因」「5 步」: a count the page promises. 個 counts only
# before a list noun — 「兩個月」「三個縣市」 are measures, not the page's items.
_TITLE_COUNT = re.compile(
    r"(\d{1,2}|[兩二三四五六七八九十])\s*"
    r"(項|大|條|步|點|件|招|種|道|題"
    r"|個\s*(?:建議|原則|問題|重點|步驟|目標|原因|指標|階段|面向|關鍵|方向|策略|行動"
    r"|轉折|教訓|功能|模組|成果|請求|決議|坑|挑戰|風險))"
)


def _slide_items(slide: Slide) -> int | None:
    """How many parallel items the page itself shows, when it has one list."""
    if slide.layout == "process":
        return len(slide.steps)
    if slide.layout == "timeline":
        return len(slide.events)
    if slide.layout == "metrics":
        return len(slide.metrics)
    if slide.layout == "table" and slide.table is not None:
        return len(slide.table.rows)
    if slide.layout in {"cards", "title-content", "closing"}:
        return len(slide.bullets) or None
    return None


def deck_warnings(slides: list[Slide]) -> list[str]:
    """Pacing and consistency problems no single page shows: too many
    structural pages, one card layout repeated through the deck, a title
    promising a count its page does not deliver. Advisory, like the budget."""
    total = len(slides)
    out: list[str] = []
    layouts = [s.layout for s in slides]
    structural = sum(1 for lay in layouts if lay in _STRUCTURAL)
    cap = max(2, round(total * 0.18))
    if structural > cap:
        out.append(
            f"結構頁過多:{total} 頁中有 {structural} 頁是目錄/章節/引言/大數字頁"
            f"(建議不超過 {cap} 頁)— 把部分章節頁併入內容頁的眉標,大數字改用 metrics。"
        )
    sections = layouts.count("section")
    if sections and total < 20:
        out.append(
            f"{total} 頁的簡報不需要章節分隔頁(目前 {sections} 頁)— 章節改寫在各頁眉標即可。"
        )
    if layouts.count("quote") > 1:
        out.append("引言頁超過 1 頁 — 只留最有力的一句。")
    for lay in sorted(_REPEATABLE):
        n = layouts.count(lay)
        if n > max(2, total // 5):
            out.append(
                f"版面「{lay}」用了 {n} 次,讀起來像同一頁重複 — 改用 table/chart/diagram/"
                "comparison 等其他版面呈現其中幾頁。"
            )
    for i, slide in enumerate(slides, start=1):
        for msg in (count_mismatch(slide), bare_cards(slide)):
            if msg:
                out.append(f"第 {i} 頁:{msg}")
    return out


def bare_cards(slide: Slide) -> str | None:
    """Cards where some carry a description and some only a title — the page
    reads as left unfinished. A content fix (write the missing line), never a
    space problem, so it is not part of :func:`check_budget`."""
    if slide.layout not in {"cards", "dashboard", "closing"} or not slide.bullets:
        return None
    bare = [
        i + 1
        for i, item in enumerate(slide.bullets)
        if not (isinstance(item, BulletItem) and item.children)
    ]
    if bare and len(bare) < len(slide.bullets):
        return (
            f"第 {'、'.join(map(str, bare))} 張卡片只有標題、沒有說明 — "
            "每張卡片都補一句具體說明(children)。"
        )
    return None


def count_mismatch(slide: Slide) -> str | None:
    """A title that promises a count (「三項」「五個建議」) its page does not show."""
    m = _TITLE_COUNT.search(slide.title or "")
    items = _slide_items(slide)
    if not m or items is None:
        return None
    raw = m.group(1)
    promised = int(raw) if raw.isdigit() else _CN_DIGITS[raw]
    if 2 <= promised <= 12 and promised != items:
        return (
            f"標題說「{m.group(0)}」,這頁卻列了 {items} 項 — "
            "若數字指的就是這些項目,請讓兩者一致(否則可忽略)。"
        )
    return None
