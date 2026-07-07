"""Programmatic layout-budget gate — does a slide's text fit its frames?

This is a *deterministic* pre-render overflow check: the thing open-slide asks
its prompt to police, ODForge enforces in code. It is pure (no I/O, no rendering)
and depends only on the layout geometry (:data:`odforge.themes.LAYOUTS`) and the
resolved design tokens (:class:`odforge.themes.Theme`).

Two public functions:

* :func:`estimate_height_cm` — a monospace-ish text-height model. Each glyph is
  charged an em-width (CJK ≈ 1.0em, everything else ≈ 0.55em); the run wraps at
  the frame width and each wrapped line is one ``size_pt * PT_TO_CM * line_height``
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

from odforge.ir import BulletItem, Slide
from odforge.themes import LAYOUTS, Frame, Theme

# ── constants ──────────────────────────────────────────────────────────────
# Points → centimetres (1pt = 1/72in, 1in = 2.54cm ⇒ 0.035277…; rounded).
PT_TO_CM = 0.03528

# Per-glyph advance widths, in em (fraction of the point size). A CJK ideograph
# is ~square; Latin/ASCII averages a bit over half an em.
_CJK_EM = 1.0
_ASCII_EM = 0.55

# The renderer's loose-bullet typography (render/odp.py): list paragraphs get a
# 145% line-height and a 0.35cm bottom margin, and level-2 children indent deeper
# (≈1cm less usable text width than a level-1 item).
_LINE_HEIGHT = 1.45
_MARGIN_BOTTOM_CM = 0.35
_LEVEL2_NARROW_CM = 1.0

# Vertical breathing room a text-box eats top+bottom before content can overflow.
# Small on purpose: the gate should fire on real overruns, not hairline ones.
_FRAME_PADDING_CM = 0.2

# Layouts whose title frame renders "bare" (no kicker eyebrow) — mirrors
# render.odp._PLAIN_LAYOUTS, which gates the kicker paragraph.
_PLAIN_LAYOUTS = frozenset({"title", "section", "closing"})
# Roles the renderer draws as a semantic bullet list when left-aligned.
_LIST_ROLES = frozenset({"bullets", "left", "right"})

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
)


def _is_cjk(ch: str) -> bool:
    """True when ``ch`` is a full-width (CJK / kana / full-width form) glyph."""
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _em_width(text: str) -> float:
    """Total advance width of ``text`` in em (CJK 1.0, else 0.55)."""
    return sum(_CJK_EM if _is_cjk(ch) else _ASCII_EM for ch in text)


def estimate_height_cm(
    text: str, size_pt: int, width_cm: float, line_height: float = _LINE_HEIGHT
) -> float:
    """Estimate the rendered height (cm) of ``text`` in a ``width_cm``-wide box.

    Model: each glyph advances an em-width (CJK ≈ 1.0em, else ≈ 0.55em); a
    paragraph's em-run is ``size_pt * PT_TO_CM`` cm/em wide and wraps at
    ``width_cm``; every wrapped line is ``size_pt * PT_TO_CM * line_height`` tall
    (minimum one line, even for empty text). ``\\n`` splits the text into
    independent paragraphs whose heights sum.
    """
    width = width_cm if width_cm > 0 else 0.01  # guard degenerate widths
    line_cm = size_pt * PT_TO_CM * line_height
    total = 0.0
    for paragraph in text.split("\n"):
        run_cm = _em_width(paragraph) * size_pt * PT_TO_CM
        lines = max(1, math.ceil(run_cm / width))
        total += lines * line_cm
    return total


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
    if role == "subtitle":
        return [slide.subtitle] if slide.subtitle else []
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

    # chart-area draws data-proportional bars that always fit their box.
    if role == "chart-area":
        return None

    lines = _role_lines(slide, role)
    if not lines:
        return None

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
        if slide.kicker and layout not in _PLAIN_LAYOUTS:
            h += estimate_height_cm(slide.kicker, theme.caption_pt, frame.w)
        return h + _plain_height(lines, frame.size_pt, frame.w)

    # standard semantic list (title-content bullets, two-col columns).
    if role in _LIST_ROLES and not frame.center:
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


def check_budget(slide: Slide, theme: Theme) -> list[str]:
    """Return an overflow message for every frame whose text overruns its box.

    Walks ``LAYOUTS[slide.layout]``; for each frame, estimates the content height
    and compares it against the frame height less a small padding allowance
    (:data:`_FRAME_PADDING_CM`, 0.2cm total). An empty list means the whole slide
    fits. Messages name the slide, the frame role, and the estimated vs available
    height in cm — actionable feedback the retry loop (Task 15.2) feeds back to
    the LLM.
    """
    label = slide.title or slide.fact or slide.quote or "(未命名投影片)"
    messages: list[str] = []
    for frame in LAYOUTS.get(slide.layout, ()):
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
