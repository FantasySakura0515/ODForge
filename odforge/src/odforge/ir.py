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


class Slide(BaseModel):
    layout: Literal["title", "title-content", "two-col", "section", "big-fact"]
    title: str = ""
    subtitle: str = ""
    bullets: List[str] = Field(default_factory=list)
    left: List[str] = Field(default_factory=list)
    right: List[str] = Field(default_factory=list)
    fact: str = ""
    notes: str = ""


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
    theme: Literal["academic", "minimal", "dark"] = "academic"
    slides: List[Slide] = Field(default_factory=list, min_length=1)
    # Optional per-deck design tokens. Absent (``None``) keeps v1 behaviour.
    design: Optional[DesignSpec] = None


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
