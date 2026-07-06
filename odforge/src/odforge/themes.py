"""ODForge presentation theme and layout data.

Pure-data module for the ``.odp`` renderer (Task 4.2). Defines the slide page
geometry, the per-layout text frames, and the theme colour palettes. Contains
no rendering logic and does not import the IR or renderer modules.

Page size is 16:9 = 28cm x 15.75cm. Positions and sizes are in centimetres;
font sizes are in points.
"""

from dataclasses import dataclass


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
    bg: str  # background colour hex, e.g. "#FFFFFF"
    title_color: str
    text_color: str
    accent: str
    font: str = "Noto Sans TC"


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
}


THEMES: dict[str, Theme] = {
    "academic": Theme(
        bg="#FFFFFF",
        title_color="#1A3C6E",
        text_color="#222222",
        accent="#1A3C6E",
    ),
    "minimal": Theme(
        bg="#FAFAFA",
        title_color="#333333",
        text_color="#444444",
        accent="#888888",
    ),
    "dark": Theme(
        bg="#1E1E2E",
        title_color="#F5F5F5",
        text_color="#D8D8D8",
        accent="#7AA2F7",
    ),
}
