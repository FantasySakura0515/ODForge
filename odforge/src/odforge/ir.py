"""ODForge Document IR (Intermediate Representation).

Pydantic v2 data models describing the JSON contract an LLM produces and the
deterministic renderers consume. This module contains data models only — no
rendering logic.
"""

from __future__ import annotations

from typing import Annotated, List, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter, field_validator

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


class Presentation(BaseModel):
    type: Literal["presentation"] = "presentation"
    title: str
    theme: Literal["academic", "minimal", "dark"] = "academic"
    slides: List[Slide] = Field(default_factory=list, min_length=1)


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
