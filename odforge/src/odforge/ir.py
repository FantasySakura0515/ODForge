"""ODForge Document IR (Intermediate Representation).

Pydantic v2 data models describing the JSON contract an LLM produces and the
deterministic renderers consume. This module contains data models only — no
rendering logic.
"""

from __future__ import annotations

import math
import re
from typing import Annotated, List, Literal, NamedTuple, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

# themes.py is pure data and imports nothing from this module at runtime (its
# only ir reference is a TYPE_CHECKING annotation), so this direction is safe
# and gives the IR a single source of truth for which themes exist.
from odforge.themes import STYLES, THEMES


class StrictModel(BaseModel):
    """Base for every IR model: an unknown field is an error, not a shrug.

    Pydantic's default is to drop keys it does not recognise. For a contract an
    LLM fills in, that default is the worst possible one: ``"bullet": [...]``
    (singular) validated happily and rendered an empty page, and
    ``"page": "big-fact"`` silently became the default layout. The model got no
    signal, the retry loop had nothing to retry on, and the user got a blank
    slide with no explanation.

    Forbidding extras turns each of those into a message naming the offending
    key — which is exactly what the generation retry needs in order to fix it.
    """

    model_config = ConfigDict(extra="forbid")


# Content limits. These are ABUSE guards, not layout guards. Deciding whether content fits a
# given frame is the budget gate's job (``textmetrics.check_budget``), which can
# see the theme, the font size and the frame — and which repairs or degrades
# rather than rejecting. Setting these low enough to enforce fit would take that
# decision away from the component that can actually make it, and would reject
# decks the budget gate knows how to fix. So they sit well above any layout's
# real capacity: they exist to stop a runaway model from handing the renderer
# thousands of items or a megabyte of prose.
MAX_TITLE_CHARS = 300
MAX_SUBTITLE_CHARS = 400
MAX_BULLET_CHARS = 600
MAX_BULLETS = 60
MAX_CHILDREN = 12
MAX_COLUMN_ITEMS = 40
MAX_NOTES_CHARS = 4000
MAX_FACT_CHARS = 400
MAX_QUOTE_CHARS = 800
MAX_SLIDES = 60
# The product's own page ceiling, shared by the outline model and the web API so
# there is exactly one number to change.
MAX_OUTLINE_PAGES = 30
MIN_OUTLINE_PAGES = 1

# ---------------------------------------------------------------------------
# Text document blocks (discriminated on "kind")
# ---------------------------------------------------------------------------


class HeadingBlock(StrictModel):
    kind: Literal["heading"] = "heading"
    level: int = Field(ge=1, le=6)
    text: str


class ParagraphBlock(StrictModel):
    kind: Literal["paragraph"] = "paragraph"
    text: str
    style: Literal["body", "quote", "note"] = "body"


class ListBlock(StrictModel):
    kind: Literal["list"] = "list"
    ordered: bool = False
    items: List[str]


class TableBlock(StrictModel):
    kind: Literal["table"] = "table"
    header: List[str]
    # Numbers are what LLMs most often put in table cells (年度、金額、數量),
    # pydantic v2 lax mode does NOT coerce int/float → str, and the renderer
    # handles numbers natively — so a str-only row type hard-failed the whole
    # document on realistic output. Mirrors ``Sheet.rows``.
    rows: List[List[Union[str, int, float, None]]]


class TocBlock(StrictModel):
    kind: Literal["toc"] = "toc"


class PageBreakBlock(StrictModel):
    kind: Literal["pagebreak"] = "pagebreak"


Block = Annotated[
    Union[
        HeadingBlock,
        ParagraphBlock,
        ListBlock,
        TableBlock,
        TocBlock,
        PageBreakBlock,
    ],
    Field(discriminator="kind"),
]


class TextDoc(StrictModel):
    type: Literal["text"] = "text"
    title: str
    lang: str = "zh-TW"
    blocks: List[Block] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


# ``min_length=1`` counts characters, and a space is a character. Every content
# invariant in this file was built on that: ``bullets=["   "]`` satisfied "at
# least one item", rendered a page with a heading and nothing under it, and left
# LibreOffice perfectly happy to open the result. Emptiness has to be measured
# after stripping, or "not empty" means "contains at least one keystroke".
def _reject_blank(label: str, value: str) -> str:
    if not value.strip():
        raise ValueError(
            f"{label} is whitespace-only; a page cannot be filled with blanks "
            "— write real content or drop the field"
        )
    return value


def _text_of(item: object) -> str:
    """The visible text of a bullet, whether it is a ``str`` or a ``BulletItem``."""
    return item if isinstance(item, str) else getattr(item, "text", "")


class BulletItem(StrictModel):
    """A nested bullet: a line of ``text`` with an optional list of ``children``.

    ``Slide.bullets`` accepts either a plain ``str`` (a leaf bullet — the v1
    shape, still valid) or a ``BulletItem`` carrying children. The renderer lowers
    a ``BulletItem`` onto the ``(text, children)`` tuple its semantic-list builder
    already understands (Task 13.2 contract). Children are one level deep (plain
    strings) — all the two-level bullet list style renders.
    """

    text: str = Field(min_length=1, max_length=MAX_BULLET_CHARS)
    children: List[str] = Field(default_factory=list, max_length=MAX_CHILDREN)

    @field_validator("text")
    @classmethod
    def _text_is_not_blank(cls, value: str) -> str:
        return _reject_blank("bullet text", value)

    @field_validator("children")
    @classmethod
    def _children_within_limits(cls, items: List[str]) -> List[str]:
        for item in items:
            if len(item) > MAX_BULLET_CHARS:
                raise ValueError(
                    f"nested bullet exceeds {MAX_BULLET_CHARS} characters; "
                    "split it into separate points"
                )
            _reject_blank("nested bullet", item)
        return items


class ChartSpec(StrictModel):
    """A horizontal bar chart's data, drawn as data-proportional ``draw:rect``\\ s.

    Standalone in Phase 13.5 — the ``chart`` slide layout and the ``Slide.chart``
    field that carry one onto a page arrive in Task 14.1; the renderer already
    knows how to draw one via ``render.odp._chart_xml``.

    Kept deliberately small: at most **8 bars**. More than that reads as a table,
    not a chart, so it is rejected ("不支援就閉嘴") rather than crammed. ``values``
    must be non-negative (a bar's width can't be negative) and equal in count to
    ``labels``. ``highlight`` — when given — names the one bar the renderer fills
    with the accent colour and must index an existing bar.
    """

    labels: List[str] = Field(min_length=1)
    values: List[float] = Field(min_length=1)
    unit: str = ""
    highlight: Optional[int] = None

    @field_validator("labels")
    @classmethod
    def _labels_are_not_blank(cls, labels: List[str]) -> List[str]:
        for label in labels:
            _reject_blank("chart label", label)
        return labels

    @field_validator("values")
    @classmethod
    def _values_non_negative(cls, values: List[float]) -> List[float]:
        # ``NaN < 0`` and ``inf < 0`` are both False, so the sign check alone
        # waves them through — and the renderer then dies converting NaN bar
        # widths. Reject here, where the message says which value is wrong.
        if any(not math.isfinite(v) for v in values):
            raise ValueError("chart values must be finite numbers")
        if any(v < 0 for v in values):
            raise ValueError("chart values must be non-negative (>= 0)")
        return values

    @model_validator(mode="after")
    def _check_shape(self) -> ChartSpec:
        n = len(self.values)
        if len(self.labels) != n:
            raise ValueError(
                f"labels/values length mismatch: {len(self.labels)} labels "
                f"vs {n} values — they must pair up one-to-one"
            )
        if n > 8:
            raise ValueError(
                f"chart supports at most 8 bars, got {n} — split it into "
                f"multiple charts (不支援就閉嘴)"
            )
        if self.highlight is not None and not 0 <= self.highlight < n:
            raise ValueError(
                f"highlight index {self.highlight} out of range for {n} bars "
                f"(expected 0..{n - 1})"
            )
        return self


# The 10 page layouts a slide can render. Shared verbatim by ``Slide.layout``
# and ``PageRole.role`` (Task 15.1) — an outline page names the layout its
# stage-2 slide will use, so the two vocabularies must never drift apart.
class ProcessStep(StrictModel):
    """One concise step in a shape-rendered process flow."""

    title: str = Field(min_length=1, max_length=24)
    detail: str = Field(default="", max_length=56)

    @field_validator("title")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _reject_blank("step title", value)


class TimelineEvent(StrictModel):
    """One event in a shape-rendered horizontal timeline."""

    label: str = Field(min_length=1, max_length=18)
    title: str = Field(min_length=1, max_length=24)
    detail: str = Field(default="", max_length=48)

    @field_validator("label", "title")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _reject_blank("timeline text", value)


class MetricSpec(StrictModel):
    """One headline metric rendered as a large-number card."""

    value: str = Field(min_length=1, max_length=18)
    label: str = Field(min_length=1, max_length=24)
    detail: str = Field(default="", max_length=56)

    @field_validator("value", "label")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _reject_blank("metric text", value)


class TableSpec(StrictModel):
    """A compact comparison table: header ``columns``, text ``rows``, and an
    optional ``highlight`` row index (e.g. one's own product in a competitor
    matrix). Deliberately small — beyond 8 rows it reads as a spreadsheet, and
    the .ods renderer is the right tool for that."""

    columns: List[str] = Field(min_length=2, max_length=6)
    rows: List[List[str]] = Field(min_length=1, max_length=8)
    highlight: Optional[int] = None

    @field_validator("columns")
    @classmethod
    def _column_labels(cls, value: List[str]) -> List[str]:
        for label in value:
            _reject_blank("a table column label", label)
            if len(label) > 16:
                raise ValueError("table column labels must be ≤16 characters")
        return value

    @model_validator(mode="after")
    def _rows_match_columns(self) -> TableSpec:
        width = len(self.columns)
        for i, row in enumerate(self.rows):
            if len(row) != width:
                raise ValueError(
                    f"table row {i} has {len(row)} cells; expected {width} "
                    "(one per column)"
                )
            for cell in row:
                if len(cell) > 40:
                    raise ValueError("table cells must be ≤40 characters")
        if self.highlight is not None and not 0 <= self.highlight < len(self.rows):
            raise ValueError(
                f"table highlight index {self.highlight} is out of range "
                f"(0..{len(self.rows) - 1})"
            )
        return self


class DiagramNode(StrictModel):
    """One editable node in a relationship or hierarchy diagram."""

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,16}$")
    title: str = Field(min_length=1, max_length=24)
    detail: str = Field(default="", max_length=48)
    emphasis: bool = False

    @field_validator("title")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _reject_blank("node title", value)


class DiagramEdge(StrictModel):
    """A directed semantic relationship between two diagram nodes."""

    source: str
    target: str
    label: str = Field(default="", max_length=18)


class DiagramSpec(StrictModel):
    """A small editable diagram with deterministic node placement."""

    kind: Literal["hub", "hierarchy"] = "hub"
    nodes: List[DiagramNode] = Field(min_length=2, max_length=6)
    edges: List[DiagramEdge] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def _check_graph(self) -> DiagramSpec:
        ids = [node.id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("diagram node ids must be unique")
        known = set(ids)
        for edge in self.edges:
            if edge.source not in known or edge.target not in known:
                raise ValueError(
                    "diagram edges must reference existing node ids"
                )
            if edge.source == edge.target:
                raise ValueError("diagram edges cannot connect a node to itself")
        return self


class SourceRef(StrictModel):
    """A concise source label with an optional clickable HTTP(S) URL."""

    label: str = Field(min_length=1, max_length=32)
    url: str = Field(default="", max_length=500)

    @field_validator("label")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        return _reject_blank("source label", value)

    @field_validator("url")
    @classmethod
    def _http_url_when_present(cls, value: str) -> str:
        if value and not re.match(r"^https?://", value, flags=re.IGNORECASE):
            raise ValueError("source url must start with http:// or https://")
        return value


class MediaAssetRef(StrictModel):
    """Application-owned image asset made available to the slide writer.

    The model sees only the stable ``id`` and descriptive metadata. It never
    receives a local filesystem path.
    """

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,48}$")
    description: str = Field(min_length=1, max_length=240)
    credit: str = Field(default="", max_length=160)


class ImageSpec(StrictModel):
    """One raster image placed on a slide.

    ``src`` may be an app-managed ``asset://`` reference, an HTTPS URL, or a
    PNG/JPEG data URI. Remote fetching is disabled unless the host application
    explicitly opts in. ``prompt`` is used only when a configured image
    provider is available.
    """

    src: str = Field(default="", max_length=12_000_000)
    prompt: str = Field(default="", max_length=800)
    alt: str = Field(min_length=1, max_length=240)
    caption: str = Field(default="", max_length=180)
    credit: str = Field(default="", max_length=160)
    fit: Literal["contain", "fill"] = "contain"

    @field_validator("alt")
    @classmethod
    def _alt_is_not_blank(cls, value: str) -> str:
        # Alt text is the accessibility contract. Spaces satisfy a screen reader
        # exactly as well as an empty string does.
        return _reject_blank("image alt text", value)

    @field_validator("src")
    @classmethod
    def _safe_source_scheme(cls, value: str) -> str:
        if not value:
            return value
        if value.startswith("asset://"):
            asset_id = value.removeprefix("asset://")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", asset_id):
                raise ValueError("asset image src must use asset://<safe-id>")
            return value
        if re.match(
            r"^data:image/(?:png|jpeg);base64,", value, flags=re.IGNORECASE
        ):
            return value
        if re.match(r"^https://", value, flags=re.IGNORECASE):
            return value
        raise ValueError(
            "image src must use asset://, https://, or a PNG/JPEG data URI"
        )

    @model_validator(mode="after")
    def _source_or_prompt(self) -> ImageSpec:
        if not self.src and not self.prompt.strip():
            raise ValueError("image requires either src or prompt")
        return self


PageRoleName = Literal[
    "title", "title-content", "two-col", "section", "big-fact",
    "quote", "agenda", "comparison", "chart", "closing",
    "process", "timeline", "metrics", "cards", "diagram",
    "image-focus", "image-split", "dashboard", "table",
]


class Slide(StrictModel):
    layout: PageRoleName
    title: str = Field(default="", max_length=MAX_TITLE_CHARS)
    subtitle: str = Field(default="", max_length=MAX_SUBTITLE_CHARS)
    # Either plain strings (v1) or nested ``BulletItem``\\ s. Coerced by pydantic's
    # smart union: a str stays a str, a dict/object becomes a BulletItem — so v1
    # all-string decks pass through transparently.
    bullets: List[Union[str, BulletItem]] = Field(
        default_factory=list, max_length=MAX_BULLETS
    )
    left: List[str] = Field(default_factory=list, max_length=MAX_COLUMN_ITEMS)
    right: List[str] = Field(default_factory=list, max_length=MAX_COLUMN_ITEMS)
    fact: str = Field(default="", max_length=MAX_FACT_CHARS)
    # Task 14.1 page-role fields. ``quote``/``attribution`` feed the quote layout;
    # ``kicker`` is an eyebrow above a content-page title; ``chart`` carries a
    # ChartSpec onto the chart layout.
    quote: str = Field(default="", max_length=MAX_QUOTE_CHARS)
    attribution: str = Field(default="", max_length=120)
    kicker: str = Field(default="", max_length=60)
    chart: Optional[ChartSpec] = None
    steps: List[ProcessStep] = Field(default_factory=list, max_length=5)
    events: List[TimelineEvent] = Field(default_factory=list, max_length=5)
    metrics: List[MetricSpec] = Field(default_factory=list, max_length=4)
    diagram: Optional[DiagramSpec] = None
    image: Optional[ImageSpec] = None
    table: Optional[TableSpec] = None
    # A one-sentence conclusion rendered as an inverted band at the foot of a
    # content page — the "so what" of the slide, visually distinct from bullets.
    takeaway: str = Field(default="", max_length=90)
    sources: List[SourceRef] = Field(default_factory=list, max_length=3)
    notes: str = Field(default="", max_length=MAX_NOTES_CHARS)

    @field_validator("bullets", "left", "right")
    @classmethod
    def _items_within_length(cls, items: list) -> list:
        for item in items:
            text = _text_of(item)
            if len(text) > MAX_BULLET_CHARS:
                raise ValueError(
                    f"a bullet exceeds {MAX_BULLET_CHARS} characters and cannot "
                    "fit any layout; shorten it or split it across pages"
                )
            _reject_blank("a bullet", text)
        return items

    @model_validator(mode="after")
    def _check_layout_fields(self) -> Slide:
        """Cross-field checks: a layout must carry the content it draws.

        Every layout below draws something specific. A page that names a layout
        and then omits what the layout draws does not render "mostly right" — it
        renders an empty frame under a heading, which is worse than a validation
        error because nothing reports it.
        """
        # Minimum content invariants. Previously only the specialised layouts
        # were checked, so ``title-content`` with no bullets, ``two-col`` with one
        # empty column and ``summary``-style pages with nothing on them all
        # rendered as blank slides that still counted toward the page total.
        # ``filled`` — not the raw list. The two differ exactly when the model
        # padded a page out with blanks to satisfy a count.
        def filled(items: list) -> list:
            return [item for item in items if _text_of(item).strip()]

        bullets, left, right = filled(self.bullets), filled(self.left), filled(self.right)
        if self.layout in {"title-content", "agenda"} and not bullets:
            raise ValueError(
                f'layout="{self.layout}" requires at least one item in '
                "Slide.bullets — an empty content page renders as a bare heading."
            )
        if self.layout == "two-col" and not (left and right):
            raise ValueError(
                'layout="two-col" requires content in BOTH Slide.left and '
                "Slide.right; a one-sided two-column page renders half-empty."
            )
        if self.layout == "comparison" and not (left and right):
            raise ValueError(
                'layout="comparison" requires content in BOTH Slide.left and '
                "Slide.right — a comparison with one side is not a comparison."
            )
        if self.layout == "big-fact" and not self.fact.strip():
            raise ValueError(
                'layout="big-fact" requires a non-empty Slide.fact — that number '
                "is the entire page."
            )
        if self.layout in {"title", "section", "closing"} and not self.title.strip():
            raise ValueError(
                f'layout="{self.layout}" requires a non-empty Slide.title.'
            )
        if self.layout == "chart" and self.chart is None:
            raise ValueError(
                'layout="chart" requires a chart: set Slide.chart to a ChartSpec '
                "(labels/values). Got chart=None."
            )
        if self.layout == "quote" and not self.quote.strip():
            raise ValueError(
                'layout="quote" requires non-empty quote text: set Slide.quote. '
                "Got an empty quote."
            )
        if self.layout == "process" and len(self.steps) < 2:
            raise ValueError(
                'layout="process" requires 2 to 5 steps in Slide.steps.'
            )
        if self.layout == "timeline" and len(self.events) < 2:
            raise ValueError(
                'layout="timeline" requires 2 to 5 events in Slide.events.'
            )
        if self.layout == "metrics" and len(self.metrics) < 2:
            raise ValueError(
                'layout="metrics" requires 2 to 4 metrics in Slide.metrics.'
            )
        if self.layout == "cards" and not 2 <= len(bullets) <= 4:
            raise ValueError(
                'layout="cards" requires 2 to 4 items in Slide.bullets.'
            )
        if self.layout == "dashboard":
            # The composite page draws both: a stat strip AND a card grid.
            if len(self.metrics) < 2:
                raise ValueError(
                    'layout="dashboard" requires 2 to 4 metrics in '
                    "Slide.metrics (the stat strip)."
                )
            if not 2 <= len(bullets) <= 4:
                raise ValueError(
                    'layout="dashboard" requires 2 to 4 items in '
                    "Slide.bullets (the card grid)."
                )
        if self.layout == "table" and self.table is None:
            raise ValueError(
                'layout="table" requires a TableSpec in Slide.table.'
            )
        if self.layout == "diagram" and self.diagram is None:
            raise ValueError(
                'layout="diagram" requires a DiagramSpec in Slide.diagram.'
            )
        if self.layout in {"image-focus", "image-split"} and self.image is None:
            raise ValueError(
                f'layout="{self.layout}" requires an ImageSpec in Slide.image.'
            )
        return self


# ---------------------------------------------------------------------------
# Design tokens (per-deck visual variation, validated before it reaches the
# renderer). An LLM designs a palette + font pairing per presentation; these
# models guarantee the tokens are well-formed and legible.
# ---------------------------------------------------------------------------

FONT_WHITELIST = ["Noto Sans TC", "Noto Serif TC", "微軟正黑體", "標楷體"]

_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _relative_luminance(hex_color: str) -> float:
    """WCAG 2.x relative luminance of a ``#RRGGBB`` sRGB colour (0..1)."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)

    def _linearize(channel: int) -> float:
        c = channel / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * _linearize(r) + 0.7152 * _linearize(g) + 0.0722 * _linearize(b)


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG contrast ratio between two ``#RRGGBB`` colours (1..21)."""
    l1 = _relative_luminance(fg)
    l2 = _relative_luminance(bg)
    lighter, darker = (l1, l2) if l1 >= l2 else (l2, l1)
    return (lighter + 0.05) / (darker + 0.05)


class Palette(StrictModel):
    """A five-colour deck palette. Hex format and legibility are validated so
    unreadable colour choices never reach the renderer."""

    bg: str
    surface: str
    text: str
    muted: str
    accent: str

    @field_validator("bg", "surface", "text", "muted", "accent")
    @classmethod
    def _valid_hex(cls, value: str) -> str:
        if not _HEX_RE.match(value):
            raise ValueError(
                f'invalid colour {value!r}: expected 6-digit hex "#RRGGBB"'
            )
        return value

    @model_validator(mode="after")
    def _check_contrast(self) -> Palette:
        # (name, colour, backdrop-name, backdrop, minimum ratio) — WCAG floors.
        # ``surface`` is a first-class backdrop, not decoration: cards, metric
        # tiles and closing actions all paint text on it. Checking only ``bg``
        # accepted a light-bg/dark-surface palette whose dark text was invisible
        # on every card. All built-in themes clear these bars comfortably.
        checks = [
            ("text", self.text, "bg", self.bg, 4.5),
            ("accent", self.accent, "bg", self.bg, 3.0),
            ("muted", self.muted, "bg", self.bg, 3.0),
            ("text", self.text, "surface", self.surface, 4.5),
            ("accent", self.accent, "surface", self.surface, 3.0),
            ("muted", self.muted, "surface", self.surface, 3.0),
        ]
        failures = []
        for name, colour, back_name, backdrop, minimum in checks:
            ratio = contrast_ratio(colour, backdrop)
            if ratio < minimum:
                failures.append(
                    f"{name}/{back_name} contrast {ratio:.2f} < {minimum} "
                    f"required ({colour} on {backdrop})"
                )
        if failures:
            raise ValueError("insufficient contrast — " + "; ".join(failures))
        return self


class FontPair(StrictModel):
    """Display + body typefaces, restricted to fonts we know ship with CJK
    coverage on the target machines."""

    display: str
    body: str

    @field_validator("display", "body")
    @classmethod
    def _in_whitelist(cls, value: str) -> str:
        if value not in FONT_WHITELIST:
            raise ValueError(
                f"font {value!r} not allowed; choose from FONT_WHITELIST "
                f"{FONT_WHITELIST}"
            )
        return value


class DesignSpec(StrictModel):
    """Per-deck design tokens: colour palette, font pairing, and density knobs."""

    palette: Palette
    fonts: FontPair
    scale: Literal["compact", "standard", "display"] = "standard"
    mode: Literal["detailed", "presenter"] = "presenter"


LOGO_PLACEMENTS = ("cover", "cover-closing", "all")

# Output languages the generator writes decks in. Lives here (the data module)
# rather than in llm.py so the IR can validate the field without importing the
# generation layer. "bilingual" is 中英對照: Chinese first, English in brackets.
LANGUAGES: dict[str, str] = {
    "zh-TW": "繁體中文",
    "en": "English",
    "bilingual": "中英對照",
}


class Branding(StrictModel):
    """Deck-level cover branding: who is presenting, and the organisation mark.

    Application-owned, not model-owned: the web/CLI layer overwrites whatever a
    model may have invented here with what the user actually typed and uploaded.
    ``logo`` uses the same ``asset://`` vocabulary as :class:`ImageSpec.src` so
    the renderer resolves it through the one asset map it already has.
    """

    byline: str = Field(default="", max_length=160)
    logo: str = Field(default="", max_length=400)
    placement: Literal["cover", "cover-closing", "all"] = "cover-closing"

    @field_validator("logo")
    @classmethod
    def _asset_reference_only(cls, value: str) -> str:
        # A cover mark is app-supplied. Allowing https:// or data: here would
        # hand a model-authored deck an outbound fetch on every render.
        if value and not value.startswith("asset://"):
            raise ValueError('logo must be an "asset://<id>" reference')
        return value


class Presentation(StrictModel):
    type: Literal["presentation"] = "presentation"
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    # Validated against the theme registry rather than a hand-kept Literal, so a
    # new built-in preset is one edit in themes.py instead of three copies that
    # drift. ``json_schema_extra`` keeps the tool schema an enum, which is what
    # stops a model inventing a theme name in the first place.
    theme: str = Field(
        default="academic", json_schema_extra={"enum": sorted(THEMES)}
    )
    # 版式: the layout personality (cover composition, heading marks, divider
    # treatment, footer furniture). Orthogonal to ``theme``/``design``, which
    # only decide colour and type. Omitted → the palette's paired default.
    style: Optional[str] = Field(
        default=None, json_schema_extra={"enum": sorted(STYLES)}
    )
    slides: List[Slide] = Field(
        default_factory=list, min_length=1, max_length=MAX_SLIDES
    )
    # Optional per-deck design tokens. Absent (``None``) keeps v1 behaviour.
    design: Optional[DesignSpec] = None
    # Cover byline / logo. Absent (``None``) renders exactly as before.
    branding: Optional[Branding] = None

    @field_validator("theme")
    @classmethod
    def _known_theme(cls, value: str) -> str:
        if value not in THEMES:
            raise ValueError(
                f"unknown theme {value!r}; choose from {sorted(THEMES)}"
            )
        return value

    @field_validator("style")
    @classmethod
    def _known_style(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in STYLES:
            raise ValueError(
                f"unknown style {value!r}; choose from {sorted(STYLES)}"
            )
        return value


# ---------------------------------------------------------------------------
# Outline — stage-1 of the two-stage LLM pipeline (Task 15.1)
#
# Before any content is written, the model designs the deck's art direction
# (``DesignSpec``) and a page-role outline in one call. Stage 2 later fills each
# ``PageRole`` with real content. These models are plain, JSON-serializable
# pydantic so the front-end cockpit can consume an Outline directly.
# ---------------------------------------------------------------------------

class PageRole(StrictModel):
    """One page in an :class:`Outline`.

    ``role`` is the layout the page will render as (the ``PageRoleName``
    vocabulary shared with ``Slide.layout``); ``title`` is its heading;
    ``gist`` is a one-line summary of what the page says. ``gist`` is required
    and non-blank — stage 2 depends on it for per-page guidance, so a missing
    or empty gist must fail validation (and trigger the retry path) rather
    than slip through silently.
    """

    role: PageRoleName
    title: str = Field(max_length=MAX_TITLE_CHARS)
    gist: str = Field(min_length=1, max_length=600)
    visual_intent: str = Field(default="", max_length=160)


class Outline(StrictModel):
    """Stage-1 blueprint: the deck's art direction plus a page-role outline.

    ``design`` is optional — when the model's palette can't pass contrast the
    pipeline strips it (``None``) and the renderer falls back to a preset theme,
    rather than crashing on a bad palette. ``mode`` picks the narrative register
    (``presenter`` = 講者型, large-type/minimal; ``detailed`` = 自讀型, complete
    text). At least one page is required.
    """

    design: Optional[DesignSpec] = None
    mode: Literal["detailed", "presenter"] = "presenter"
    # Capped at the product's own page ceiling. A model that returns 80 pages is
    # not "ambitious", it is about to spend eighty stage-2 generations and render
    # a deck nobody asked for — catch it before the first one.
    pages: List[PageRole] = Field(
        min_length=MIN_OUTLINE_PAGES, max_length=MAX_OUTLINE_PAGES
    )
    # Application-owned context copied from the user's original request after
    # stage 1 so the content-writing model keeps the full brief.
    source_prompt: str = ""
    media_assets: List[MediaAssetRef] = Field(default_factory=list, max_length=12)
    image_generation_available: bool = False
    # Which language stage 2 writes the deck in. Application-owned like the
    # fields above: the caller overwrites whatever stage 1 returned with the
    # user's actual choice, so a model that ignores the enum cannot silently
    # switch the deck's language.
    language: str = Field(
        default="zh-TW", json_schema_extra={"enum": sorted(LANGUAGES)}
    )

    @field_validator("language")
    @classmethod
    def _known_language(cls, value: str) -> str:
        if value not in LANGUAGES:
            raise ValueError(
                f"unknown language {value!r}; choose from {sorted(LANGUAGES)}"
            )
        return value


# ---------------------------------------------------------------------------
# Spreadsheet
# ---------------------------------------------------------------------------


# A1-style reference: column letters + 1-based row (``B5``, ``AA12``). Optional
# ``$`` anchors are accepted because OpenFormula writes them for absolute refs.
_CELL_REF_RE = re.compile(r"^\$?([A-Za-z]{1,3})\$?([1-9][0-9]*)$")

# Functions we will actually emit and that LibreOffice computes. Anything outside
# this set is refused rather than written out and left to resolve as ``#NAME?``
# in the user's spreadsheet — the project's standing "不支援就閉嘴" rule. Growing
# the list is one edit; shipping a broken formula is a broken deliverable.
OPENFORMULA_FUNCTIONS = frozenset(
    """
    ABS AND AVERAGE AVERAGEIF CEILING CONCATENATE COUNT COUNTA COUNTIF DATE DAY
    EXP FLOOR HLOOKUP IF IFERROR INDEX INT LARGE LEFT LEN LN LOG LOG10 LOWER
    MATCH MAX MEDIAN MID MIN MOD MONTH NOT NOW OR POWER PRODUCT RANK RIGHT ROUND
    ROUNDDOWN ROUNDUP SIGN SMALL SQRT STDEV SUM SUMIF SUMPRODUCT TEXT TODAY TRIM
    TRUNC UPPER VAR VLOOKUP YEAR
    """.split()
)

_FUNCTION_CALL_RE = re.compile(r"([A-Za-z][A-Za-z0-9._]*)\s*\(")

# --- reference scanning ----------------------------------------------------
#
# Two syntaxes reach us and BOTH have to be understood, because the bounds check
# downstream is only as good as this scanner. Reading just one of them is worse
# than reading neither: it reports "references verified" over a formula whose
# references were never looked at.
#
#   ``[.B2]``, ``[.B2:.B7]``, ``[Sheet2.B2]``, ``['My Sheet'.B2]``
#       OpenFormula's bracketed form — what the renderer emits.
#   ``B2``, ``A1:A999``, ``$A$1``, ``Sheet2.B2``
#       the bare form models emit constantly. Previously invisible, which is how
#       ``of:=SUM(A1:A999)`` passed validation on a seven-row sheet and shipped a
#       spreadsheet full of empty-cell arithmetic.
_A1 = r"\$?[A-Za-z]{1,3}\$?[0-9]+"
_SHEET_NAME = r"(?:'(?:[^']|'')*'|\$?[A-Za-z_一-鿿][\w一-鿿]*)"
_BRACKET_BODY_RE = re.compile(r"\[([^\]]*)\]")
_BRACKET_PART_RE = re.compile(rf"^\s*(?:({_SHEET_NAME})\s*)?\.?({_A1})\s*$")
# Guards, in order: not preceded by an identifier character (so the ``LOG`` of
# ``LOG10(`` cannot be read as column LOG row 10); optional ``Sheet2.`` prefix;
# the A1 body; and not followed by an identifier character or ``(`` (so a
# function name can never be mistaken for a cell).
_BARE_REF_RE = re.compile(
    r"(?<![A-Za-z0-9_.$])"
    rf"(?:({_SHEET_NAME})\.)?"
    rf"({_A1})"
    r"(?![A-Za-z0-9_(])"
)
# Double-quoted string literals are data, not references: ``TEXT(A1,"B5")``
# names one cell, not two.
_STRING_LITERAL_RE = re.compile(r'"(?:[^"\\]|\\.)*"')


class CellReference(NamedTuple):
    """One cell a formula names. ``sheet`` is ``None`` for a same-sheet ref."""

    sheet: Optional[str]
    column: int
    row: int


def _unquote_sheet(name: str) -> str:
    """``'My Sheet'`` → ``My Sheet``; ``$Sheet2`` → ``Sheet2``."""
    name = name.strip().lstrip("$")
    if name.startswith("'") and name.endswith("'") and len(name) >= 2:
        return name[1:-1].replace("''", "'")
    return name


def _column_index(letters: str) -> int:
    """``A`` → 0, ``B`` → 1, ``AA`` → 26."""
    index = 0
    for char in letters.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def _format_ref(column: int, row: int) -> str:
    """``(1, 4)`` → ``B5`` — so a bounds error names the cell the model wrote."""
    letters = ""
    index = column
    while True:
        letters = chr(ord("A") + index % 26) + letters
        index = index // 26 - 1
        if index < 0:
            break
    return f"{letters}{row + 1}"


def parse_cell_ref(ref: str) -> tuple[int, int]:
    """``"B5"`` → ``(column=1, row=4)``, both 0-based. Raises on a bad ref."""
    match = _CELL_REF_RE.match(ref.strip())
    if match is None:
        raise ValueError(
            f"invalid cell reference {ref!r}: expected A1 style, e.g. 'B5' or 'AA12'"
        )
    return _column_index(match.group(1)), int(match.group(2)) - 1


class FormulaSpec(StrictModel):
    cell: str
    formula: str

    @field_validator("cell")
    @classmethod
    def _target_is_a_cell_ref(cls, value: str) -> str:
        parse_cell_ref(value)  # raises with the offending ref in the message
        return value

    @field_validator("formula")
    @classmethod
    def _must_use_of_namespace(cls, value: str) -> str:
        if not value.startswith("of:="):
            raise ValueError('formula must start with "of:="')
        body = value[len("of:=") :]
        if not body.strip():
            raise ValueError("formula body is empty after the 'of:=' prefix")
        if body.count("(") != body.count(")"):
            raise ValueError(
                f"unbalanced parentheses in formula {value!r} — LibreOffice will "
                "refuse to parse it"
            )
        unknown = sorted(
            {
                name.upper()
                for name in _FUNCTION_CALL_RE.findall(body)
                if name.upper() not in OPENFORMULA_FUNCTIONS
            }
        )
        if unknown:
            raise ValueError(
                f"unsupported spreadsheet function(s): {', '.join(unknown)}. "
                "ODForge only emits functions it has verified LibreOffice "
                f"computes; supported: {', '.join(sorted(OPENFORMULA_FUNCTIONS))}"
            )
        return value

    def references(self) -> List[CellReference]:
        """Every cell the formula body names, bracketed or bare, 0-based.

        Range endpoints only — a range's interior cannot be out of bounds if both
        ends are in bounds, so checking the endpoints is sufficient and cheap.
        """
        body = self.formula
        found: List[CellReference] = []
        for bracket in _BRACKET_BODY_RE.findall(body):
            # Within one bracket the range end inherits the start's sheet:
            # ``[Sheet2.B2:.B9]`` is Sheet2 twice, not Sheet2 and then here.
            inherited: Optional[str] = None
            for part in bracket.split(":"):
                match = _BRACKET_PART_RE.match(part)
                if match is None:
                    continue
                sheet, ref = match.groups()
                name = _unquote_sheet(sheet) if sheet else inherited
                inherited = inherited or name
                column, row = parse_cell_ref(ref)
                found.append(CellReference(name, column, row))
        # Bracketed refs are consumed before the bare scan so the same cell is not
        # reported twice, and string literals are blanked so their contents cannot
        # masquerade as references.
        remainder = _STRING_LITERAL_RE.sub('""', _BRACKET_BODY_RE.sub("", body))
        for sheet, ref in _BARE_REF_RE.findall(remainder):
            column, row = parse_cell_ref(ref)
            found.append(
                CellReference(_unquote_sheet(sheet) if sheet else None, column, row)
            )
        return found

    def referenced_cells(self) -> List[tuple[int, int]]:
        """Same-sheet ``(column, row)`` pairs only — cross-sheet refs are resolved
        by :class:`Spreadsheet`, which is the only scope that knows the sizes of
        the other sheets."""
        return [(ref.column, ref.row) for ref in self.references() if ref.sheet is None]


class Sheet(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    columns: List[str] = Field(min_length=1, max_length=64)
    # ``None`` marks an intentionally empty cell — typically a formula target
    # the LLM leaves null because a formula computes its value.
    rows: List[List[Union[str, int, float, None]]] = Field(max_length=5000)
    formulas: List[FormulaSpec] = Field(default_factory=list, max_length=500)

    @field_validator("rows")
    @classmethod
    def _numbers_must_be_finite(
        cls, rows: List[List[Union[str, int, float, None]]]
    ) -> List[List[Union[str, int, float, None]]]:
        """NaN/±Infinity are not numbers a spreadsheet can hold.

        ODF has no representation for them: the renderer writes
        ``office:value="nan"`` and LibreOffice shows ``#VALUE!`` — a broken cell
        in a file we certified as valid. Reject at the contract, where the
        message can name the row and column.
        """
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                if isinstance(value, bool) or not isinstance(value, float):
                    continue
                if not math.isfinite(value):
                    raise ValueError(
                        f"cell at row {r + 1}, column {c + 1} is {value!r}; "
                        "spreadsheet numbers must be finite "
                        "(NaN/Infinity have no ODF representation)"
                    )
        return rows

    @model_validator(mode="after")
    def _check_formula_targets(self) -> Sheet:
        """Formula targets must be unique, in range, and reference real cells.

        The sheet's addressable area is the header row plus its data rows; a
        formula that writes to (or reads from) outside it produces a cell
        LibreOffice cannot resolve, which is exactly the ``#REF!``/``#NAME?``
        class of failure a "valid ODF" claim must not cover for.
        """
        width = len(self.columns)
        # Row 1 is the header; data rows follow. A formula may target the row
        # immediately after the data (a totals row the IR leaves implicit).
        height = 1 + len(self.rows) + 1
        seen: dict[str, int] = {}
        for index, spec in enumerate(self.formulas):
            column, row = parse_cell_ref(spec.cell)
            key = spec.cell.replace("$", "").upper()
            if key in seen:
                raise ValueError(
                    f"duplicate formula target {spec.cell!r} (formulas "
                    f"{seen[key] + 1} and {index + 1}) — the second would "
                    "silently overwrite the first"
                )
            seen[key] = index
            if not (0 <= column < width and 0 <= row < height):
                raise ValueError(
                    f"formula target {spec.cell!r} is outside sheet "
                    f"{self.name!r} ({width} columns × {height} rows)"
                )
            for ref in spec.references():
                if ref.sheet is not None:
                    # Resolved by ``Spreadsheet`` — a sheet cannot see its siblings.
                    continue
                if not (0 <= ref.column < width and 0 <= ref.row < height):
                    raise ValueError(
                        f"formula for {spec.cell!r} references "
                        f"{_format_ref(ref.column, ref.row)}, outside sheet "
                        f"{self.name!r} ({width} columns × {height} rows)"
                    )
        return self


class Spreadsheet(StrictModel):
    type: Literal["spreadsheet"] = "spreadsheet"
    title: str
    sheets: List[Sheet] = Field(default_factory=list, min_length=1)

    @model_validator(mode="after")
    def _check_cross_sheet_references(self) -> Spreadsheet:
        """Resolve ``Sheet2.B2`` / ``[Sheet2.B2]`` against the sheet it names.

        This is the only scope that can: a ``Sheet`` validator sees its own
        dimensions and nothing else, so a cross-sheet reference reaching it can
        only be waved through. Waved through is how ``#REF!`` gets certified as
        valid ODF.
        """
        sizes = {
            sheet.name: (len(sheet.columns), 1 + len(sheet.rows) + 1)
            for sheet in self.sheets
        }
        # LibreOffice resolves sheet names case-insensitively; a model that writes
        # ``sheet2.B2`` for a sheet called ``Sheet2`` means the same sheet.
        folded = {name.casefold(): name for name in sizes}
        for sheet in self.sheets:
            for spec in sheet.formulas:
                for ref in spec.references():
                    if ref.sheet is None:
                        continue
                    target = folded.get(ref.sheet.casefold())
                    if target is None:
                        raise ValueError(
                            f"formula for {spec.cell!r} on sheet {sheet.name!r} "
                            f"references unknown sheet {ref.sheet!r}; this "
                            f"spreadsheet has {sorted(sizes)}"
                        )
                    width, height = sizes[target]
                    if not (0 <= ref.column < width and 0 <= ref.row < height):
                        raise ValueError(
                            f"formula for {spec.cell!r} on sheet {sheet.name!r} "
                            f"references {target}.{_format_ref(ref.column, ref.row)}, "
                            f"outside that sheet ({width} columns × {height} rows)"
                        )
        return self


# ---------------------------------------------------------------------------
# Top-level parsing (discriminated on "type")
# ---------------------------------------------------------------------------

Document = Annotated[
    Union[TextDoc, Presentation, Spreadsheet],
    Field(discriminator="type"),
]

_document_adapter: TypeAdapter[Union[TextDoc, Presentation, Spreadsheet]] = TypeAdapter(
    Document
)


def parse_ir(data: dict) -> Union[TextDoc, Presentation, Spreadsheet]:
    """Validate a raw dict into the matching Document IR model.

    Dispatches on the ``type`` discriminator. Raises ``pydantic.ValidationError``
    for unknown types or invalid payloads.
    """
    return _document_adapter.validate_python(data)
