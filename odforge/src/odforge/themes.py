"""ODForge presentation theme and layout data.

Pure-data module for the ``.odp`` renderer. Defines the slide page geometry,
the per-layout text frames, and the resolved design tokens (:class:`Theme`).
Contains no rendering logic; the only IR dependency is :class:`Presentation`
(imported lazily inside :func:`resolve_design` to keep this a pure-data module).

Page size is 16:9 = 28cm x 15.75cm. Positions and sizes are in centimetres;
font sizes are in points.

A :class:`Theme` is the *only* visual vocabulary the renderer understands: a
fully-resolved bundle of colour + typography tokens. It is produced either from
a built-in preset (:data:`THEMES`) or from a validated per-deck
:class:`~odforge.ir.DesignSpec` via :func:`resolve_design`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from odforge.ir import Presentation


@dataclass(frozen=True)
class Frame:
    role: str  # "title" | "subtitle" | "bullets" | "left" | "right" | "fact"
    x: float
    y: float
    w: float
    h: float  # cm
    size_pt: int
    bold: bool = False
    center: bool = False


@dataclass(frozen=True)
class Theme:
    """Fully-resolved design tokens the renderer consumes.

    Colours are ``#RRGGBB`` hex; ``*_pt`` sizes are points. ``title_color``
    defaults conceptually to ``text`` but is stored explicitly so dark themes
    can override it. The legacy ``text_color`` / ``font`` names the v1 ``.odp``
    renderer reads are exposed as read-only aliases below.
    """

    bg: str
    surface: str
    text: str
    muted: str
    accent: str
    title_color: str
    font_display: str
    font_body: str
    display_pt: int
    h1_pt: int
    body_pt: int
    caption_pt: int
    bullet_char: str = "▪"

    # -- v1 renderer compatibility aliases (read-only) -----------------------
    @property
    def text_color(self) -> str:
        """Legacy alias for :attr:`text` (render/odp.py, pre-Phase-13)."""
        return self.text

    @property
    def font(self) -> str:
        """Legacy alias for :attr:`font_body` (render/odp.py, pre-Phase-13)."""
        return self.font_body


# (display, h1, body, caption) point sizes per density tier.
SCALES: dict[str, tuple[int, int, int, int]] = {
    "compact": (44, 24, 16, 12),
    "standard": (54, 28, 18, 13),
    "display": (66, 32, 20, 14),
}


PAGE_W: float = 28.0
PAGE_H: float = 15.75


LAYOUTS: dict[str, list[Frame]] = {
    "title": [
        Frame("title", 2, 5.5, 24, 3, 40, bold=True, center=True),
        Frame("subtitle", 2, 9, 24, 2, 20, center=True),
    ],
    "title-content": [
        Frame("title", 1.5, 0.8, 25, 2, 28, bold=True),
        Frame("bullets", 1.5, 3.5, 25, 11, 18),
    ],
    "two-col": [
        Frame("title", 1.5, 0.8, 25, 2, 28, bold=True),
        Frame("left", 1.5, 3.5, 12, 11, 16),
        Frame("right", 14.5, 3.5, 12, 11, 16),
    ],
    "section": [
        Frame("title", 2, 6.5, 24, 3, 36, bold=True, center=True),
    ],
    "big-fact": [
        Frame("fact", 2, 5, 24, 4, 48, bold=True, center=True),
        Frame("bullets", 2, 10, 24, 3, 16, center=True),
    ],
    # -- Task 14.1 page-role layouts ----------------------------------------
    # size_pt values below are the 'standard' scale defaults; the renderer
    # re-sizes these roles from the resolved Theme tokens (h1_pt / body_pt /
    # caption_pt) so a per-deck DesignSpec scale is honoured, exactly as the
    # 'fact' role is up-sized to display_pt.
    "quote": [
        Frame("quote", 3, 4.8, 22, 4.5, 28, center=True),
        Frame("attribution", 3, 10, 22, 1.2, 13, center=True),
    ],
    "agenda": [
        Frame("title", 1.5, 0.8, 25, 2, 28, bold=True),
        Frame("items", 3, 3.8, 22, 10.5, 18),
    ],
    "comparison": [
        Frame("title", 1.5, 0.8, 25, 2, 28, bold=True),
        Frame("left", 1.5, 3.2, 12.2, 11.3, 18),
        Frame("right", 14.3, 3.2, 12.2, 11.3, 18),
    ],
    "chart": [
        Frame("title", 1.5, 0.8, 25, 2, 28, bold=True),
        Frame("chart-area", 1.5, 3.5, 15.5, 11, 18),
        Frame("insights", 17.6, 3.5, 8.9, 11, 13),
    ],
    "process": [
        Frame("title", 1.5, 0.8, 25, 2, 28, bold=True),
        Frame("process-area", 1.5, 3.5, 25, 10.5, 16),
    ],
    "timeline": [
        Frame("title", 1.5, 0.8, 25, 2, 28, bold=True),
        Frame("timeline-area", 1.5, 3.4, 25, 10.6, 16),
    ],
    "metrics": [
        Frame("title", 1.5, 0.8, 25, 2, 28, bold=True),
        Frame("metrics-area", 1.5, 3.5, 25, 10.5, 16),
    ],
    "cards": [
        Frame("title", 1.5, 0.8, 25, 2, 28, bold=True),
        Frame("cards-area", 1.5, 3.5, 25, 10.5, 16),
    ],
    "diagram": [
        Frame("title", 1.5, 0.8, 25, 2, 28, bold=True),
        Frame("diagram-area", 1.5, 3.5, 25, 10.3, 16),
    ],
    "image-focus": [
        Frame("title", 1.5, 0.8, 25, 2, 28, bold=True),
        Frame("image-area", 1.5, 3.25, 25, 10.45, 16),
    ],
    "image-split": [
        Frame("title", 1.5, 0.8, 25, 2, 28, bold=True),
        Frame("image-area", 1.5, 3.35, 13.4, 10.25, 16),
        Frame("bullets", 16.0, 3.55, 10.5, 9.8, 17),
    ],
    "closing": [
        Frame("message", 2, 3.6, 24, 3, 28, bold=True, center=True),
        Frame("closing-actions", 2, 8.2, 24, 4, 16),
    ],
}


# Layout / role sets shared by the renderer (render.odp) and the budget gate
# (textmetrics). Kept here — the single source both modules import — so a future
# layout change can never let the two drift apart (which would silently diverge
# the budget estimate from what the renderer actually draws).
#
# Layouts whose title frame renders "bare": no kicker eyebrow, no page-number /
# footer furniture (the opening title, the full-accent section divider, and the
# inverted closing page).
PLAIN_LAYOUTS: frozenset[str] = frozenset({"title", "section", "closing"})
# Frame roles whose (left-aligned) multi-item content renders as a semantic
# bullet list; centred frames (e.g. the big-fact caption) stay bare paragraphs.
LIST_ROLES: frozenset[str] = frozenset({"bullets", "left", "right"})


_STANDARD = SCALES["standard"]


def _preset(
    *,
    bg: str,
    surface: str,
    text: str,
    muted: str,
    accent: str,
    font_display: str,
    font_body: str,
    title_color: str | None = None,
) -> Theme:
    """Build a preset Theme at the 'standard' scale (title_color defaults to text)."""
    display_pt, h1_pt, body_pt, caption_pt = _STANDARD
    return Theme(
        bg=bg,
        surface=surface,
        text=text,
        muted=muted,
        accent=accent,
        title_color=title_color if title_color is not None else text,
        font_display=font_display,
        font_body=font_body,
        display_pt=display_pt,
        h1_pt=h1_pt,
        body_pt=body_pt,
        caption_pt=caption_pt,
    )


# Three built-in art directions. Colours are hand-picked to (a) read well and
# (b) clear the very contrast bars ir.Palette enforces (text/bg >= 4.5,
# accent/bg >= 3.0, muted/bg >= 3.0) — see the contrast tests.
THEMES: dict[str, Theme] = {
    # 學術藍 + 暖白: a scholarly blue on warm off-white; serif display for gravitas.
    "academic": _preset(
        bg="#FBF9F4",
        surface="#EFEADD",
        text="#1F2733",
        muted="#5B6470",
        accent="#1A4B8C",
        font_display="Noto Serif TC",
        font_body="Noto Sans TC",
    ),
    # 暖灰單色 + 一點橘: warm-grey monochrome lifted by a single burnt-orange accent.
    "minimal": _preset(
        bg="#FAF8F5",
        surface="#ECE8E2",
        text="#2B2926",
        muted="#6E6A63",
        accent="#C0560E",
        font_display="Noto Sans TC",
        font_body="Noto Sans TC",
    ),
    # 深靛 + 亮青: deep indigo ground with a bright cyan accent; light text.
    "dark": _preset(
        bg="#171826",
        surface="#252842",
        text="#E8EAF2",
        muted="#9BA0C4",
        accent="#3DD6E6",
        font_display="Noto Sans TC",
        font_body="Noto Sans TC",
    ),
    # 企業青綠: 2026 'Transformative Teal' on a cool near-white; calm/trustworthy.
    "teal": _preset(
        bg="#F4F7F7",
        surface="#E4EDEC",
        text="#1B2A2C",
        muted="#556463",
        accent="#0F766E",
        font_display="Noto Sans TC",
        font_body="Noto Sans TC",
    ),
    # 森綠 + 奶油: deep forest green on warm cream, serif display; editorial/永續.
    "forest": _preset(
        bg="#F6F2E9",
        surface="#E9E1CE",
        text="#23291F",
        muted="#5E5849",
        accent="#2C6E49",
        font_display="Noto Serif TC",
        font_body="Noto Sans TC",
    ),
    # 海軍藍 + 冷白: navy ink + electric-blue accent on cool white; corporate/SaaS.
    "navy": _preset(
        bg="#FAFBFD",
        surface="#EAEEF5",
        text="#16233F",
        muted="#55617A",
        accent="#2563EB",
        font_display="Noto Sans TC",
        font_body="Noto Sans TC",
    ),
    # 午夜紫 + 電光紫: midnight-plum ground with a bright violet accent; creative/tech.
    "violet": _preset(
        bg="#1A1526",
        surface="#2A2440",
        text="#ECEAF4",
        muted="#9A93B8",
        accent="#B49BF5",
        font_display="Noto Sans TC",
        font_body="Noto Sans TC",
    ),
}


def resolve_design(p: "Presentation") -> Theme:
    """Resolve a presentation's visual tokens into the single :class:`Theme`
    the renderer consumes.

    With no per-deck ``design`` the built-in preset ``THEMES[p.theme]`` is
    returned unchanged. When ``p.design`` is present (already validated by
    pydantic) its palette maps 1:1 onto the Theme colours, ``title_color``
    follows ``text``, fonts come from the pairing, and the point sizes are taken
    from :data:`SCALES` keyed by ``design.scale``. ``design.mode`` is a hint for
    the LLM layer and deliberately does not affect sizing here.
    """
    if p.design is None:
        return THEMES[p.theme]

    d = p.design
    pal = d.palette
    display_pt, h1_pt, body_pt, caption_pt = SCALES[d.scale]
    return Theme(
        bg=pal.bg,
        surface=pal.surface,
        text=pal.text,
        muted=pal.muted,
        accent=pal.accent,
        title_color=pal.text,
        font_display=d.fonts.display,
        font_body=d.fonts.body,
        display_pt=display_pt,
        h1_pt=h1_pt,
        body_pt=body_pt,
        caption_pt=caption_pt,
    )
