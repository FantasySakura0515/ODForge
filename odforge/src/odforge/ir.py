"""ODForge Document IR (Intermediate Representation).

Pydantic v2 data models describing the JSON contract an LLM produces and the
deterministic renderers consume. This module contains data models only — no
rendering logic.
"""

from __future__ import annotations

import re
from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

# ---------------------------------------------------------------------------
# Text document blocks (discriminated on "kind")
# ---------------------------------------------------------------------------


class HeadingBlock(BaseModel):
    kind: Literal["heading"] = "heading"
    level: int = Field(ge=1, le=6)
    text: str


class ParagraphBlock(BaseModel):
    kind: Literal["paragraph"] = "paragraph"
    text: str
    style: Literal["body", "quote", "note"] = "body"


class ListBlock(BaseModel):
    kind: Literal["list"] = "list"
    ordered: bool = False
    items: List[str]


class TableBlock(BaseModel):
    kind: Literal["table"] = "table"
    header: List[str]
    rows: List[List[str]]


class TocBlock(BaseModel):
    kind: Literal["toc"] = "toc"


class PageBreakBlock(BaseModel):
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


class TextDoc(BaseModel):
    type: Literal["text"] = "text"
    title: str
    lang: str = "zh-TW"
    blocks: List[Block] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


class BulletItem(BaseModel):
    """A nested bullet: a line of ``text`` with an optional list of ``children``.

    ``Slide.bullets`` accepts either a plain ``str`` (a leaf bullet — the v1
    shape, still valid) or a ``BulletItem`` carrying children. The renderer lowers
    a ``BulletItem`` onto the ``(text, children)`` tuple its semantic-list builder
    already understands (Task 13.2 contract). Children are one level deep (plain
    strings) — all the two-level bullet list style renders.
    """

    text: str
    children: List[str] = Field(default_factory=list)


class ChartSpec(BaseModel):
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

    @field_validator("values")
    @classmethod
    def _values_non_negative(cls, values: List[float]) -> List[float]:
        if any(v < 0 for v in values):
            raise ValueError("chart values must be non-negative (>= 0)")
        return values

    @model_validator(mode="after")
    def _check_shape(self) -> "ChartSpec":
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
class ProcessStep(BaseModel):
    """One concise step in a shape-rendered process flow."""

    title: str = Field(min_length=1, max_length=24)
    detail: str = Field(default="", max_length=56)


class TimelineEvent(BaseModel):
    """One event in a shape-rendered horizontal timeline."""

    label: str = Field(min_length=1, max_length=18)
    title: str = Field(min_length=1, max_length=24)
    detail: str = Field(default="", max_length=48)


class MetricSpec(BaseModel):
    """One headline metric rendered as a large-number card."""

    value: str = Field(min_length=1, max_length=18)
    label: str = Field(min_length=1, max_length=24)
    detail: str = Field(default="", max_length=56)


class DiagramNode(BaseModel):
    """One editable node in a relationship or hierarchy diagram."""

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,16}$")
    title: str = Field(min_length=1, max_length=24)
    detail: str = Field(default="", max_length=48)
    emphasis: bool = False


class DiagramEdge(BaseModel):
    """A directed semantic relationship between two diagram nodes."""

    source: str
    target: str
    label: str = Field(default="", max_length=18)


class DiagramSpec(BaseModel):
    """A small editable diagram with deterministic node placement."""

    kind: Literal["hub", "hierarchy"] = "hub"
    nodes: List[DiagramNode] = Field(min_length=2, max_length=6)
    edges: List[DiagramEdge] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def _check_graph(self) -> "DiagramSpec":
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


class SourceRef(BaseModel):
    """A concise source label with an optional clickable HTTP(S) URL."""

    label: str = Field(min_length=1, max_length=32)
    url: str = Field(default="", max_length=500)

    @field_validator("url")
    @classmethod
    def _http_url_when_present(cls, value: str) -> str:
        if value and not re.match(r"^https?://", value, flags=re.IGNORECASE):
            raise ValueError("source url must start with http:// or https://")
        return value


class MediaAssetRef(BaseModel):
    """Application-owned image asset made available to the slide writer.

    The model sees only the stable ``id`` and descriptive metadata. It never
    receives a local filesystem path.
    """

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,48}$")
    description: str = Field(min_length=1, max_length=240)
    credit: str = Field(default="", max_length=160)


class ImageSpec(BaseModel):
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
    def _source_or_prompt(self) -> "ImageSpec":
        if not self.src and not self.prompt.strip():
            raise ValueError("image requires either src or prompt")
        return self


PageRoleName = Literal[
    "title", "title-content", "two-col", "section", "big-fact",
    "quote", "agenda", "comparison", "chart", "closing",
    "process", "timeline", "metrics", "cards", "diagram",
    "image-focus", "image-split",
]


class Slide(BaseModel):
    layout: PageRoleName
    title: str = ""
    subtitle: str = ""
    # Either plain strings (v1) or nested ``BulletItem``\\ s. Coerced by pydantic's
    # smart union: a str stays a str, a dict/object becomes a BulletItem — so v1
    # all-string decks pass through transparently.
    bullets: List[Union[str, BulletItem]] = Field(default_factory=list)
    left: List[str] = Field(default_factory=list)
    right: List[str] = Field(default_factory=list)
    fact: str = ""
    # Task 14.1 page-role fields. ``quote``/``attribution`` feed the quote layout;
    # ``kicker`` is an eyebrow above a content-page title; ``chart`` carries a
    # ChartSpec onto the chart layout.
    quote: str = ""
    attribution: str = ""
    kicker: str = ""
    chart: Optional[ChartSpec] = None
    steps: List[ProcessStep] = Field(default_factory=list, max_length=5)
    events: List[TimelineEvent] = Field(default_factory=list, max_length=5)
    metrics: List[MetricSpec] = Field(default_factory=list, max_length=4)
    diagram: Optional[DiagramSpec] = None
    image: Optional[ImageSpec] = None
    sources: List[SourceRef] = Field(default_factory=list, max_length=3)
    notes: str = ""

    @model_validator(mode="after")
    def _check_layout_fields(self) -> "Slide":
        """Cross-field checks: a layout must carry the content it draws."""
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
        if self.layout == "cards" and not 2 <= len(self.bullets) <= 4:
            raise ValueError(
                'layout="cards" requires 2 to 4 items in Slide.bullets.'
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


class Palette(BaseModel):
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
    def _check_contrast(self) -> "Palette":
        # (name, colour, minimum ratio) — WCAG floors for legibility on ``bg``.
        checks = [
            ("text", self.text, 4.5),
            ("accent", self.accent, 3.0),
            ("muted", self.muted, 3.0),
        ]
        failures = []
        for name, colour, minimum in checks:
            ratio = contrast_ratio(colour, self.bg)
            if ratio < minimum:
                failures.append(
                    f"{name}/bg contrast {ratio:.2f} < {minimum} required "
                    f"({colour} on {self.bg})"
                )
        if failures:
            raise ValueError("insufficient contrast — " + "; ".join(failures))
        return self


class FontPair(BaseModel):
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


class DesignSpec(BaseModel):
    """Per-deck design tokens: colour palette, font pairing, and density knobs."""

    palette: Palette
    fonts: FontPair
    scale: Literal["compact", "standard", "display"] = "standard"
    mode: Literal["detailed", "presenter"] = "presenter"


class Presentation(BaseModel):
    type: Literal["presentation"] = "presentation"
    title: str
    theme: Literal[
        "academic", "minimal", "dark", "teal", "forest", "navy", "violet"
    ] = "academic"
    slides: List[Slide] = Field(default_factory=list, min_length=1)
    # Optional per-deck design tokens. Absent (``None``) keeps v1 behaviour.
    design: Optional[DesignSpec] = None


# ---------------------------------------------------------------------------
# Outline — stage-1 of the two-stage LLM pipeline (Task 15.1)
#
# Before any content is written, the model designs the deck's art direction
# (``DesignSpec``) and a page-role outline in one call. Stage 2 later fills each
# ``PageRole`` with real content. These models are plain, JSON-serializable
# pydantic so the front-end cockpit can consume an Outline directly.
# ---------------------------------------------------------------------------

class PageRole(BaseModel):
    """One page in an :class:`Outline`.

    ``role`` is the layout the page will render as (the ``PageRoleName``
    vocabulary shared with ``Slide.layout``); ``title`` is its heading;
    ``gist`` is a one-line summary of what the page says. ``gist`` is required
    and non-blank — stage 2 depends on it for per-page guidance, so a missing
    or empty gist must fail validation (and trigger the retry path) rather
    than slip through silently.
    """

    role: PageRoleName
    title: str
    gist: str = Field(min_length=1)
    visual_intent: str = Field(default="", max_length=160)


class Outline(BaseModel):
    """Stage-1 blueprint: the deck's art direction plus a page-role outline.

    ``design`` is optional — when the model's palette can't pass contrast the
    pipeline strips it (``None``) and the renderer falls back to a preset theme,
    rather than crashing on a bad palette. ``mode`` picks the narrative register
    (``presenter`` = 講者型, large-type/minimal; ``detailed`` = 自讀型, complete
    text). At least one page is required.
    """

    design: Optional[DesignSpec] = None
    mode: Literal["detailed", "presenter"] = "presenter"
    pages: List[PageRole] = Field(min_length=1)
    # Application-owned context copied from the user's original request after
    # stage 1 so the content-writing model keeps the full brief.
    source_prompt: str = ""
    media_assets: List[MediaAssetRef] = Field(default_factory=list, max_length=12)
    image_generation_available: bool = False


# ---------------------------------------------------------------------------
# Spreadsheet
# ---------------------------------------------------------------------------


class FormulaSpec(BaseModel):
    cell: str
    formula: str

    @field_validator("formula")
    @classmethod
    def _must_use_of_namespace(cls, value: str) -> str:
        if not value.startswith("of:="):
            raise ValueError('formula must start with "of:="')
        return value


class Sheet(BaseModel):
    name: str
    columns: List[str]
    # ``None`` marks an intentionally empty cell — typically a formula target
    # the LLM leaves null because a formula computes its value.
    rows: List[List[Union[str, int, float, None]]]
    formulas: List[FormulaSpec] = Field(default_factory=list)


class Spreadsheet(BaseModel):
    type: Literal["spreadsheet"] = "spreadsheet"
    title: str
    sheets: List[Sheet] = Field(default_factory=list, min_length=1)


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
