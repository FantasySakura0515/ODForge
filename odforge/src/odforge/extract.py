"""Style extraction from an existing ODF template (殺手功能:吃現有範本).

The cheapest source of design quality is a template the user already owns:
governments and schools ship public ODF templates, and ODF is open XML, so
sampling their look is easy. :func:`extract_design` opens such a template
(``.otp``/``.odp``/``.ott``/``.odt``), reads its ``styles.xml`` (and
``content.xml`` for automatic styles), and derives a
:class:`~odforge.ir.DesignSpec` that renders new content into the template's
look.

**MVP scope (定案):** derive the four cheap, high-signal tokens and backfill the
rest — this is *not* full master-page reuse.

* **bg** — the drawing-page / page background fill (a solid ``draw:fill-color``,
  or a gradient's ``draw:start-color`` when the page is painted with one).
* **text / title** — the most frequent ``fo:color`` that clears the WCAG text
  bar against ``bg``.
* **accent** — the most frequent *chromatic* (non-grey) ``fo:color`` that clears
  the accent bar.
* **fonts** — the ``font-face-decls`` declarations mapped onto
  :data:`~odforge.ir.FONT_WHITELIST`.

Every candidate is fed through the *same* :class:`~odforge.ir.Palette` /
:class:`~odforge.ir.FontPair` validators the renderer trusts. Any field
extraction cannot confidently fill — or that fails contrast — is backfilled from
the academic preset, so the result is ALWAYS a valid ``DesignSpec``. Only a
genuinely unreadable / corrupt file raises (:class:`TemplateExtractionError`).

**surface / muted derivation:** these two are hard to sample reliably, so they
are *derived* to guarantee contrast holds regardless of the extracted bg/text:
``surface`` is ``bg`` blended a hair toward ``text`` (it carries no contrast
constraint — it only backs cards), and ``muted`` is ``text`` blended toward
``bg`` by the largest amount that still clears the 3:1 muted bar (the "mutest"
legible tone). If even that cannot be met, ``muted`` falls back to plain
``text`` (which trivially clears 3:1).
"""

from __future__ import annotations

import zipfile
from collections import Counter
from pathlib import Path
from typing import Optional

import lxml.etree as etree

from odforge.ir import (
    FONT_WHITELIST,
    DesignSpec,
    FontPair,
    Palette,
    _HEX_RE,
    _relative_luminance,
    contrast_ratio,
)
from odforge.themes import THEMES

# The preset the extractor backfills from when a field can't be confidently
# determined or fails contrast. Academic is the project default art direction.
_PRESET = THEMES["academic"]

# ODF namespaces this extractor reaches into.
_NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
}

# Parts we try to read, in priority order. styles.xml holds the master/main
# drawing-page + font-face-decls; content.xml carries per-page automatic styles.
_PARTS = ("styles.xml", "content.xml")

# A colour counts as "chromatic" (an accent candidate, not a grey/near-white/
# near-black) when the spread between its max and min RGB channel exceeds this.
_CHROMA_MIN = 28

# WCAG bars, mirrored from ir.Palette so we can pre-filter candidates before
# construction (Palette re-checks them authoritatively).
_TEXT_MIN = 4.5
_ACCENT_MIN = 3.0
_MUTED_MIN = 3.0


class TemplateExtractionError(Exception):
    """Raised when a template is unreadable — not a zip, or carrying no readable
    ODF part (styles.xml / content.xml). A plausible-but-imperfect template never
    raises; it is backfilled from the preset instead."""


def _q(prefix: str, local: str) -> str:
    """Clark-notation qualified name for an lxml attribute/element lookup."""
    return f"{{{_NS[prefix]}}}{local}"


# ---------------------------------------------------------------------------
# Zip + XML reading
# ---------------------------------------------------------------------------


def _read_roots(template_path: Path) -> dict[str, etree._Element]:
    """Open the ODF zip and parse the parts we understand.

    Returns a mapping of part name -> parsed root for every part in
    :data:`_PARTS` that is present AND well-formed. Raises
    :class:`TemplateExtractionError` when the file is missing, is not a zip, or
    yields no parseable ODF part at all.
    """
    path = Path(template_path)
    if not path.is_file():
        raise TemplateExtractionError(f"找不到範本檔案:{path}")

    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise TemplateExtractionError(
            f"範本不是有效的 ODF/zip 檔:{path}({exc})"
        ) from exc

    roots: dict[str, etree._Element] = {}
    with zf:
        names = set(zf.namelist())
        for part in _PARTS:
            if part not in names:
                continue
            try:
                data = zf.read(part)
            except (KeyError, zipfile.BadZipFile):
                continue
            try:
                roots[part] = etree.fromstring(data)
            except etree.XMLSyntaxError:
                # A malformed part is skipped; if every part is malformed we
                # raise below.
                continue

    if not roots:
        raise TemplateExtractionError(
            f"範本不含可解析的 ODF 樣式(styles.xml / content.xml):{path}"
        )
    return roots


def _ordered_roots(roots: dict[str, etree._Element]) -> list[etree._Element]:
    """The parsed roots in priority order (styles.xml before content.xml)."""
    return [roots[p] for p in _PARTS if p in roots]


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------


def _norm_hex(value: Optional[str]) -> Optional[str]:
    """Normalise a colour to upper-case ``#RRGGBB``, or None if not a 6-hex value.

    Named colours, ``transparent``, and 3-digit shorthands are rejected (None) —
    the renderer's palette only speaks ``#RRGGBB``.
    """
    if not value:
        return None
    v = value.strip()
    if not _HEX_RE.match(v):
        return None
    return v.upper()


def _is_chromatic(hex_color: str) -> bool:
    """True when a colour is saturated enough to read as an accent (not a grey).

    Filters greys, near-white, and near-black by the spread of their RGB
    channels, so the accent candidate is an actual hue rather than the ink or
    paper colour.
    """
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return (max(r, g, b) - min(r, g, b)) >= _CHROMA_MIN


def _blend(from_hex: str, to_hex: str, amount: float) -> str:
    """Return ``from_hex`` moved ``amount`` (0..1) of the way toward ``to_hex``.

    Same channel-wise linear blend the renderer uses for its watermarks; used
    here to derive ``surface`` and ``muted`` from the extracted bg/text.
    """

    def _mix(a: int, b: int) -> int:
        return round(a + (b - a) * amount)

    r = _mix(int(from_hex[1:3], 16), int(to_hex[1:3], 16))
    g = _mix(int(from_hex[3:5], 16), int(to_hex[3:5], 16))
    b = _mix(int(from_hex[5:7], 16), int(to_hex[5:7], 16))
    return f"#{r:02X}{g:02X}{b:02X}"


def _max_contrast_ink(bg: str) -> str:
    """A guaranteed-legible ink for ``bg`` (clears the 4.5:1 text bar).

    Prefers a soft near-ink/near-paper tone; if that dips under the bar on a
    mid-tone bg, falls back to pure black or white — for any bg one of the two
    always clears ~4.58:1, so this can never return an illegible colour.
    """
    soft = "#F5F5F5" if _relative_luminance(bg) < 0.4 else "#141414"
    if contrast_ratio(soft, bg) >= _TEXT_MIN:
        return soft
    return "#FFFFFF" if _relative_luminance(bg) < 0.18 else "#000000"


# ---------------------------------------------------------------------------
# Extraction: background
# ---------------------------------------------------------------------------


def _extract_bg(roots: list[etree._Element]) -> Optional[str]:
    """Extract the page background colour, or None if none can be read.

    Walks the roots in priority order (styles.xml first, where our own decks put
    the single main drawing-page style ``dp1``). For each ``drawing-page-
    properties`` it reads a solid ``draw:fill-color`` directly, or resolves a
    ``draw:fill-gradient-name`` to that gradient's ``draw:start-color`` (this is
    how the dark preset paints its background). Falls back to a page-layout's
    ``fo:background-color``. The first valid colour found wins.
    """
    for root in roots:
        gradients = {
            g.get(_q("draw", "name")): g for g in root.iter(_q("draw", "gradient"))
        }
        for dpp in root.iter(_q("style", "drawing-page-properties")):
            fill = dpp.get(_q("draw", "fill"))
            if fill == "gradient":
                gname = dpp.get(_q("draw", "fill-gradient-name"))
                grad = gradients.get(gname)
                if grad is not None:
                    start = _norm_hex(grad.get(_q("draw", "start-color")))
                    if start:
                        return start
                continue
            # solid (or a bare fill-color with the fill attr omitted)
            color = _norm_hex(dpp.get(_q("draw", "fill-color")))
            if color:
                return color
        # Fallback: a page background set on the page-layout itself.
        for plp in root.iter(_q("style", "page-layout-properties")):
            color = _norm_hex(plp.get(_q("fo", "background-color")))
            if color:
                return color
    return None


# ---------------------------------------------------------------------------
# Extraction: colour frequency (text / accent candidates)
# ---------------------------------------------------------------------------


def _colour_counts(roots: list[etree._Element]) -> Counter:
    """Count ``fo:color`` occurrences across every style in every root.

    The frequency of a text colour among style definitions is a cheap proxy for
    how much of the deck wears it — the body/text colour dominates, chart/accent
    tints are rarer. Counts feed both the text pick and the accent pick.
    """
    counts: Counter = Counter()
    for root in roots:
        for el in root.iter():
            color = _norm_hex(el.get(_q("fo", "color")))
            if color:
                counts[color] += 1
    return counts


def _sorted_by_freq(counts: Counter) -> list[str]:
    """Colours most-frequent first, ties broken by hex for determinism."""
    return [c for c, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _pick_text(counts: Counter, bg: str) -> str:
    """Pick the deck's text colour: the most frequent legible ``fo:color``.

    Prefers the highest-frequency colour that isn't the bg and clears the 4.5:1
    text bar; falls back to the preset's text (if it happens to clear against
    this bg) and finally to a guaranteed-legible ink.
    """
    for color in _sorted_by_freq(counts):
        if color != bg and contrast_ratio(color, bg) >= _TEXT_MIN:
            return color
    if contrast_ratio(_PRESET.text, bg) >= _TEXT_MIN:
        return _PRESET.text
    return _max_contrast_ink(bg)


def _pick_accent(counts: Counter, bg: str, text: str) -> str:
    """Pick the accent: the most frequent *chromatic* ``fo:color`` that clears
    the 3:1 accent bar and isn't the bg or the text colour.

    Falls back to the preset accent (when it clears against this bg), then to the
    text colour (which trivially clears 3:1) so an accent always exists.
    """
    for color in _sorted_by_freq(counts):
        if color in (bg, text):
            continue
        if _is_chromatic(color) and contrast_ratio(color, bg) >= _ACCENT_MIN:
            return color
    if contrast_ratio(_PRESET.accent, bg) >= _ACCENT_MIN:
        return _PRESET.accent
    return text


def _derive_surface(bg: str, text: str) -> str:
    """Derive ``surface`` as ``bg`` nudged a hair toward ``text``.

    Surface only backs cards and carries no contrast constraint, so a slight
    blend that reads as a distinct panel against the page is enough.
    """
    return _blend(bg, text, 0.08)


def _derive_muted(text: str, bg: str) -> str:
    """Derive ``muted`` as ``text`` blended toward ``bg`` — the mutest legible tone.

    Tries progressively smaller blends toward the bg and returns the *most*
    muted one (largest blend) that still clears the 3:1 muted bar; at blend 0 the
    colour is ``text`` itself, which trivially clears it, so this always returns.
    """
    for amount in (0.5, 0.45, 0.4, 0.35, 0.3, 0.25, 0.2, 0.1, 0.0):
        candidate = _blend(text, bg, amount)
        if contrast_ratio(candidate, bg) >= _MUTED_MIN:
            return candidate
    return text  # unreachable (amount=0.0 == text), kept for total-function clarity


def _build_palette(roots: list[etree._Element]) -> Palette:
    """Assemble a validated :class:`~odforge.ir.Palette` from the parsed roots.

    Extracts bg (preset-backfilled), picks text/accent from ``fo:color``
    frequency, derives surface/muted to guarantee contrast, then constructs the
    Palette. The construction is wrapped: on the off chance the derived tuple
    still fails pydantic's validators, the whole palette falls back to the preset
    (which is valid by construction) — so this never raises.
    """
    bg = _extract_bg(roots) or _PRESET.bg
    counts = _colour_counts(roots)
    text = _pick_text(counts, bg)
    accent = _pick_accent(counts, bg, text)
    surface = _derive_surface(bg, text)
    muted = _derive_muted(text, bg)
    try:
        return Palette(bg=bg, surface=surface, text=text, muted=muted, accent=accent)
    except ValueError:
        return Palette(
            bg=_PRESET.bg,
            surface=_PRESET.surface,
            text=_PRESET.text,
            muted=_PRESET.muted,
            accent=_PRESET.accent,
        )


# ---------------------------------------------------------------------------
# Extraction: fonts
# ---------------------------------------------------------------------------


def _map_font(name: Optional[str]) -> Optional[str]:
    """Map a declared font name onto a whitelist entry, or None if unknown.

    Exact match against :data:`~odforge.ir.FONT_WHITELIST` after trimming quotes
    and whitespace — we only reuse fonts we know ship with CJK coverage.
    """
    if not name:
        return None
    cleaned = name.strip().strip("'\"").strip()
    return cleaned if cleaned in FONT_WHITELIST else None


def _split_family(family: Optional[str]) -> list[str]:
    """Split an ``svg:font-family`` CSS-style list into individual names."""
    if not family:
        return []
    return [tok for tok in family.split(",")]


def _extract_fonts(roots: list[etree._Element]) -> FontPair:
    """Derive a :class:`~odforge.ir.FontPair` from the template's font declarations.

    Primary signal is each ``<style:font-face>``'s ``style:name`` (in document
    order); the ``svg:font-family`` fallback chain is consulted only when no
    ``style:name`` maps to the whitelist. With one whitelisted font the pairing
    uses it for both display and body (our decks' "one product, one typeface"
    reality); with two or more, the first is the display face and the second the
    body face. When nothing whitelist-legal is declared, both faces fall back to
    the preset pairing.
    """
    named: list[str] = []
    family: list[str] = []
    for root in roots:
        for ff in root.iter(_q("style", "font-face")):
            mapped = _map_font(ff.get(_q("style", "name")))
            if mapped and mapped not in named:
                named.append(mapped)
            for token in _split_family(ff.get(_q("svg", "font-family"))):
                fam = _map_font(token)
                if fam and fam not in family:
                    family.append(fam)

    fonts = named or family
    if not fonts:
        return FontPair(display=_PRESET.font_display, body=_PRESET.font_body)
    if len(fonts) == 1:
        return FontPair(display=fonts[0], body=fonts[0])
    return FontPair(display=fonts[0], body=fonts[1])


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def extract_design(template_path: Path) -> DesignSpec:
    """Derive a :class:`~odforge.ir.DesignSpec` from an existing ODF template.

    Opens ``template_path`` (``.otp``/``.odp``/``.ott``/``.odt``), reads
    ``styles.xml`` and ``content.xml``, and samples the template's background,
    text/accent colours and font declarations into a validated ``DesignSpec``.
    Fields that can't be sampled confidently — or that fail the WCAG contrast
    bars — are backfilled from the academic preset, so the return value is ALWAYS
    a valid ``DesignSpec`` for a readable template.

    Raises :class:`TemplateExtractionError` only when the file is missing, is not
    a zip, or contains no parseable ODF part.
    """
    roots_map = _read_roots(template_path)
    roots = _ordered_roots(roots_map)
    palette = _build_palette(roots)
    fonts = _extract_fonts(roots)
    return DesignSpec(palette=palette, fonts=fonts, scale="standard", mode="presenter")
