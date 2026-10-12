"""Keep an edited document's embedded fonts in step with its text.

ODForge embeds *subsets* of its fonts — only the characters a deck uses — so a
deck stays small and still looks right on a machine without the fonts. Editing
breaks that bargain: type a character the deck never had and the embedded face
has no glyph for it, so LibreOffice (and the browser) draw that character in
some fallback font. This module notices missing glyphs and re-subsets from the
original font files when this machine has them.

Everything here is best effort. Without fontTools or the source files nothing is
re-embedded and the document is saved exactly as edited.
"""

from __future__ import annotations

import hashlib
import io
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Set

from lxml import etree

from .fontembed import _ALWAYS, embed_fonts, find_font
from .scene import q

# The families the editor offers even when a document has not used them yet:
# they can be embedded (when installed here), so the result travels.
OFFERED = ("Noto Sans TC", "Noto Serif TC")


def _family_of(face: etree._Element) -> str:
    return face.get(q("svg", "font-family"), face.get(q("style", "name"), "")).strip("'\"")


def font_faces(roots: Iterable[etree._Element]) -> List[etree._Element]:
    return [face for root in roots for face in root.iter(q("style", "font-face"))]


def referenced_families(roots: List[etree._Element]) -> Set[str]:
    """Families some style actually names (not just declares)."""
    names: Set[str] = set()
    for root in roots:
        for props in root.iter(q("style", "text-properties")):
            for attr in ("font-name", "font-name-asian", "font-name-complex"):
                value = props.get(q("style", attr))
                if value:
                    names.add(value)
    by_name = {face.get(q("style", "name")): _family_of(face) for face in font_faces(roots)}
    return {by_name.get(name, name) for name in names}


def choices(roots: List[etree._Element]) -> List[Dict[str, object]]:
    """Fonts the editor lets a person pick, with whether they can be embedded here."""
    families = [_family_of(face) for face in font_faces(roots)]
    for family in OFFERED:
        if find_font(family, False) is not None:
            families.append(family)
    seen: Dict[str, Dict[str, object]] = {}
    for family in families:
        if family and family not in seen:
            seen[family] = {
                "family": family,
                "embeddable": find_font(family, False) is not None,
            }
    return list(seen.values())


def document_text(roots: List[etree._Element]) -> str:
    parts: List[str] = []
    for root in roots:
        for el in root.iter(q("text", "p"), q("text", "h")):
            parts.append("".join(el.itertext()))
    return "".join(parts)


@lru_cache(maxsize=64)
def _coverage(digest: str, data: bytes) -> Optional[frozenset]:
    try:
        from fontTools.ttLib import TTFont
    except ImportError:
        return None
    try:
        cmap = TTFont(io.BytesIO(data), lazy=True)["cmap"].getBestCmap() or {}
    except Exception:
        return None
    return frozenset(cmap)


def coverage(data: bytes) -> Optional[frozenset]:
    """Code points an embedded font has glyphs for (``None`` when unreadable)."""
    return _coverage(hashlib.sha1(data).hexdigest(), data)


def needed_chars(text: str) -> Set[int]:
    return {ord(c) for c in set(text) | set(_ALWAYS) if not c.isspace() or c == " "}


def rebuild(family: str, text: str):
    """Fresh subsets (regular + bold) of ``family`` covering ``text``, or ``[]``."""
    return embed_fonts({family}, text)
