"""Embed subset CJK fonts into an ODF package.

Why this exists: a deck names "Noto Sans TC" / "Noto Serif TC", but the machine
that opens it often does not have them — or has the files without a working
registration. LibreOffice then substitutes a fallback face and *synthesises*
bold by smearing the regular outline, which turns dense glyphs (量、實、覺、讓)
into blots, and the serif display face silently becomes a heavy sans. Every
generated deck looked worse on the reviewer's machine than on ours.

ODF can carry its fonts (``<svg:font-face-src>`` pointing at ``Fonts/…`` parts,
which LibreOffice loads on open). Shipping four full Noto TC files would add
~28MB to every deck, so each face is **subset to the characters the deck
actually uses** — typically a few hundred KB.

Everything here degrades gracefully: without fontTools, or without the source
font files on this machine, no font is embedded and the deck renders exactly
as before.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

# (family, bold) -> file name, as distributed by Google Fonts / notofonts.
_FILES: dict[tuple[str, bool], tuple[str, ...]] = {
    ("Noto Sans TC", False): ("NotoSansTC-Regular.otf", "NotoSansTC-Regular.ttf"),
    ("Noto Sans TC", True): ("NotoSansTC-Bold.otf", "NotoSansTC-Bold.ttf"),
    ("Noto Serif TC", False): ("NotoSerifTC-Regular.otf", "NotoSerifTC-Regular.ttf"),
    ("Noto Serif TC", True): ("NotoSerifTC-Bold.otf", "NotoSerifTC-Bold.ttf"),
}

# Characters the renderer itself draws (bullets, ellipses, page numbers,
# quote marks) on top of whatever the deck's text contains.
_ALWAYS = (
    "".join(chr(c) for c in range(0x20, 0x7F))
    + "▪•·…—–“”‘’「」『』（）、，。：；！？％＋－×→←↑↓≥≤～／"
)


@dataclass(frozen=True)
class EmbeddedFont:
    family: str
    bold: bool
    part: str  # package path, e.g. "Fonts/NotoSansTC-Bold.otf"
    data: bytes
    fmt: str  # "opentype" | "truetype"


def _search_dirs() -> list[Path]:
    dirs = []
    if env := os.environ.get("ODFORGE_FONT_DIR"):
        dirs.append(Path(env))
    home = Path.home()
    dirs += [
        home / "Library/Fonts",
        Path("/Library/Fonts"),
        home / ".local/share/fonts",
        home / ".fonts",
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
    ]
    if windir := os.environ.get("WINDIR"):
        dirs.append(Path(windir) / "Fonts")
    if local := os.environ.get("LOCALAPPDATA"):
        dirs.append(Path(local) / "Microsoft/Windows/Fonts")
    return [d for d in dirs if d.is_dir()]


@cache
def find_font(family: str, bold: bool) -> Path | None:
    """Locate the source file for a face, searching the usual font folders
    (recursively, since Linux distros nest them). ``None`` when absent."""
    names = _FILES.get((family, bold))
    if not names:
        return None
    for directory in _search_dirs():
        for name in names:
            direct = directory / name
            if direct.is_file():
                return direct
        for name in names:
            found = next(directory.rglob(name), None)
            if found is not None:
                return found
    return None


def _subset(path: Path, text: str) -> tuple[bytes, str] | None:
    try:
        from fontTools import subset
        from fontTools.ttLib import TTFont
    except ImportError:  # optional dependency
        return None
    options = subset.Options()
    # Keep every name record: LibreOffice matches the embedded face to the
    # document's font-face by family name.
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    options.notdef_outline = True
    options.hinting = False
    options.layout_features = ["*"]
    font = TTFont(path, lazy=False)
    subsetter = subset.Subsetter(options)
    subsetter.populate(text=text)
    subsetter.subset(font)
    buf = io.BytesIO()
    font.save(buf)
    fmt = "opentype" if "CFF " in font or "CFF2" in font else "truetype"
    return buf.getvalue(), fmt


def embed_fonts(families: set[str], text: str) -> list[EmbeddedFont]:
    """Subset regular + bold of each family to ``text`` (plus the glyphs the
    renderer draws itself). Families without a source file are skipped."""
    chars = "".join(sorted(set(text) | set(_ALWAYS)))
    out: list[EmbeddedFont] = []
    for family in sorted(families):
        for bold in (False, True):
            path = find_font(family, bold)
            if path is None:
                continue
            result = _subset(path, chars)
            if result is None:
                return []
            data, fmt = result
            ext = ".otf" if fmt == "opentype" else ".ttf"
            out.append(
                EmbeddedFont(family, bold, f"Fonts/{path.stem}{ext}", data, fmt)
            )
    return out


def font_face_decls(
    families: list[str], embedded: list[EmbeddedFont], escape_attr
) -> str:
    """``<style:font-face>`` elements for ``families``, each pointing at its
    embedded parts when there are any."""
    by_family: dict[str, list[EmbeddedFont]] = {}
    for font in embedded:
        by_family.setdefault(font.family, []).append(font)
    parts = []
    for family in families:
        name = escape_attr(family)
        uris = "".join(
            f'<svg:font-face-uri xlink:href="{escape_attr(f.part)}" xlink:type="simple"'
            f' loext:font-style="normal"'
            f' loext:font-weight="{"bold" if f.bold else "normal"}">'
            f'<svg:font-face-format svg:string="{f.fmt}"/>'
            f"</svg:font-face-uri>"
            for f in by_family.get(family, [])
        )
        src = f"<svg:font-face-src>{uris}</svg:font-face-src>" if uris else ""
        parts.append(
            f'<style:font-face style:name="{name}"'
            f" svg:font-family=\"'{name}'\" style:font-pitch=\"variable\">"
            f"{src}</style:font-face>"
        )
    return "".join(parts)
