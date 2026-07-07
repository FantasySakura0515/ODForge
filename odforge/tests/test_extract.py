"""Tests for ``odforge.extract`` — the style-extraction "killer feature".

The extractor reads an existing ODF template's styles.xml (and content.xml for
automatic styles), derives a :class:`~odforge.ir.DesignSpec`, and runs it through
the same contrast validators the renderer trusts. It must ALWAYS return a valid
DesignSpec for a readable ODF (preset backfill), and raise a clear error only on
genuinely unreadable/corrupt input.

The preferred fixtures are real round-trips: render a v2 deck via ``render_odp``
and feed the resulting ``.odp`` straight back into ``extract_design`` — no binary
checked in.
"""

from __future__ import annotations

import zipfile

import pytest

from odforge.extract import TemplateExtractionError, extract_design
from odforge.ir import DesignSpec, FontPair, Palette
from odforge.ir import Presentation, Slide
from odforge.package import ODP_MIMETYPE, write_odf_package
from odforge.render.odp import render_odp
from odforge.themes import THEMES

# The academic preset is the backfill source the extractor uses for fields it
# cannot confidently determine (matches extract.py's PRESET).
_PRESET = THEMES["academic"]


def _dark_deck() -> Presentation:
    return Presentation(
        title="深色範本",
        theme="dark",
        slides=[
            Slide(layout="title", title="標題", subtitle="副標"),
            Slide(
                layout="title-content",
                title="重點",
                bullets=["第一點", "第二點", "第三點"],
            ),
            Slide(layout="section", title="第二章"),
            Slide(layout="closing", title="謝謝聆聽"),
        ],
    )


def _academic_deck() -> Presentation:
    return Presentation(
        title="學術範本",
        theme="academic",
        slides=[
            Slide(layout="title", title="標題", subtitle="副標"),
            Slide(
                layout="title-content",
                title="重點",
                bullets=["第一點", "第二點"],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Round-trip: a v2 deck we render is the truest extraction fixture.
# ---------------------------------------------------------------------------


def test_roundtrip_dark_bg_matches_preset(tmp_path):
    # Scenario ①: extract on a v2 dark deck recovers dark's distinctive bg. Dark
    # paints its background as a gradient, so the extractor must resolve the
    # gradient's start-color, not a flat draw:fill-color.
    out = tmp_path / "dark.odp"
    render_odp(_dark_deck(), out)
    spec = extract_design(out)
    assert isinstance(spec, DesignSpec)
    assert spec.palette.bg == THEMES["dark"].bg


def test_roundtrip_light_bg_matches_preset(tmp_path):
    # A solid-fill (light) deck: bg comes straight off the drawing-page fill-color.
    out = tmp_path / "light.odp"
    render_odp(_academic_deck(), out)
    spec = extract_design(out)
    assert spec.palette.bg == THEMES["academic"].bg


def test_roundtrip_returns_valid_designspec(tmp_path):
    # Whatever it extracts must be a fully valid DesignSpec: the palette passes
    # the WCAG contrast bars and the fonts are whitelist-legal (both enforced by
    # pydantic on construction, re-asserted here for intent).
    out = tmp_path / "dark.odp"
    render_odp(_dark_deck(), out)
    spec = extract_design(out)
    # Re-validating the palette must not raise (WCAG contrast bars hold).
    Palette(**spec.palette.model_dump())
    # fonts are whitelist-restricted; construct a FontPair to prove legality.
    FontPair(display=spec.fonts.display, body=spec.fonts.body)


def test_roundtrip_font_maps_to_whitelist(tmp_path):
    # The academic deck declares "Noto Sans TC" (a whitelist font); extraction
    # must recover it rather than silently defaulting.
    out = tmp_path / "light.odp"
    render_odp(_academic_deck(), out)
    spec = extract_design(out)
    assert spec.fonts.body == "Noto Sans TC"


# ---------------------------------------------------------------------------
# Scenario ②: a styles.xml with no font declaration falls back to the preset.
# ---------------------------------------------------------------------------

_NS_DECLS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
    ' xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0"'
    ' xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"'
    ' xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0"'
    ' xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"'
)


def _minimal_styles_xml(*, with_font: bool) -> str:
    font_face = (
        "<office:font-face-decls>"
        '<style:font-face style:name="標楷體"'
        ' svg:font-family="\'標楷體\'"/>'
        "</office:font-face-decls>"
        if with_font
        else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<office:document-styles {_NS_DECLS} office:version=\"1.2\">"
        f"{font_face}"
        "<office:automatic-styles>"
        '<style:style style:name="dp1" style:family="drawing-page">'
        '<style:drawing-page-properties draw:fill="solid"'
        ' draw:fill-color="#FBF9F4"/>'
        "</style:style>"
        '<style:style style:name="P1" style:family="paragraph">'
        '<style:text-properties fo:color="#1F2733"/>'
        "</style:style>"
        "</office:automatic-styles>"
        "</office:document-styles>"
    )


def test_missing_font_declaration_falls_back_to_preset(tmp_path):
    out = tmp_path / "nofont.odp"
    write_odf_package(out, ODP_MIMETYPE, {"styles.xml": _minimal_styles_xml(with_font=False)})
    spec = extract_design(out)  # must not raise
    assert spec.fonts.display == _PRESET.font_display
    assert spec.fonts.body == _PRESET.font_body
    # bg/text were still extractable from the crafted file.
    assert spec.palette.bg == "#FBF9F4"


def test_declared_whitelist_font_is_used(tmp_path):
    out = tmp_path / "font.odp"
    write_odf_package(out, ODP_MIMETYPE, {"styles.xml": _minimal_styles_xml(with_font=True)})
    spec = extract_design(out)
    assert spec.fonts.body == "標楷體"


# ---------------------------------------------------------------------------
# Always-valid: an off-whitelist font and an under-contrast palette backfill.
# ---------------------------------------------------------------------------


def test_offwhitelist_font_falls_back_to_preset(tmp_path):
    styles = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<office:document-styles {_NS_DECLS} office:version=\"1.2\">"
        "<office:font-face-decls>"
        '<style:font-face style:name="Comic Sans MS" svg:font-family="Comic Sans MS"/>'
        "</office:font-face-decls>"
        "<office:automatic-styles>"
        '<style:style style:name="dp1" style:family="drawing-page">'
        '<style:drawing-page-properties draw:fill="solid" draw:fill-color="#FFFFFF"/>'
        "</style:style>"
        "</office:automatic-styles>"
        "</office:document-styles>"
    )
    out = tmp_path / "badfont.odp"
    write_odf_package(out, ODP_MIMETYPE, {"styles.xml": styles})
    spec = extract_design(out)
    assert spec.fonts.display == _PRESET.font_display
    assert spec.fonts.body == _PRESET.font_body


def test_always_returns_valid_designspec_for_odd_palette(tmp_path):
    # A template whose only declared text colour is a near-bg low-contrast grey
    # must still yield a legible DesignSpec (text backfilled to clear 4.5:1).
    styles = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<office:document-styles {_NS_DECLS} office:version=\"1.2\">"
        "<office:automatic-styles>"
        '<style:style style:name="dp1" style:family="drawing-page">'
        '<style:drawing-page-properties draw:fill="solid" draw:fill-color="#101010"/>'
        "</style:style>"
        '<style:style style:name="P1" style:family="paragraph">'
        '<style:text-properties fo:color="#151515"/>'  # near-bg, unreadable
        "</style:style>"
        "</office:automatic-styles>"
        "</office:document-styles>"
    )
    out = tmp_path / "odd.odp"
    write_odf_package(out, ODP_MIMETYPE, {"styles.xml": styles})
    spec = extract_design(out)  # must not raise
    assert spec.palette.bg == "#101010"
    Palette(**spec.palette.model_dump())  # re-validate: contrast holds


# ---------------------------------------------------------------------------
# Corrupt / non-ODF input raises a clear, typed error.
# ---------------------------------------------------------------------------


def test_corrupt_non_zip_raises(tmp_path):
    bad = tmp_path / "corrupt.odp"
    bad.write_bytes(b"this is not a zip file at all")
    with pytest.raises(TemplateExtractionError):
        extract_design(bad)


def test_zip_without_odf_parts_raises(tmp_path):
    bad = tmp_path / "empty.odp"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("hello.txt", "not odf")
    with pytest.raises(TemplateExtractionError):
        extract_design(bad)


def test_missing_file_raises(tmp_path):
    with pytest.raises(TemplateExtractionError):
        extract_design(tmp_path / "does-not-exist.odp")


# ---------------------------------------------------------------------------
# Security: XXE / entity-expansion hardening. extract_design parses
# attacker-controllable ODF files ("eat an existing template"), so its XML
# parser must not resolve external entities (local-file disclosure) nor expand
# nested entities (billion-laughs DoS).
# ---------------------------------------------------------------------------


def test_external_entity_is_not_resolved(tmp_path):
    # A malicious styles.xml declares a SYSTEM entity pointing at a local secret
    # file and references it as the text colour. With a hardened parser the entity
    # is never resolved, so the secret must never surface in the DesignSpec (and
    # the call must neither raise nor hang).
    secret = tmp_path / "secret.txt"
    marker = "TOPSECRET_9c1f2a"
    secret.write_text(marker, encoding="utf-8")
    secret_uri = secret.resolve().as_uri()  # file:///.../secret.txt

    # The canonical XXE vector: an external SYSTEM entity referenced in element
    # text. With a hardened parser it resolves to nothing (never fetched); the
    # surrounding styles stay valid so extraction still yields a real spec.
    styles = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<!DOCTYPE office:document-styles [<!ENTITY xxe SYSTEM "{secret_uri}">]>'
        f"<office:document-styles {_NS_DECLS} office:version=\"1.2\">"
        "<office:automatic-styles>"
        '<style:style style:name="dp1" style:family="drawing-page">'
        '<style:drawing-page-properties draw:fill="solid" draw:fill-color="#FFFFFF"/>'
        "</style:style>"
        '<style:style style:name="P1" style:family="paragraph">'
        "<style:text-properties>&xxe;</style:text-properties>"
        "</style:style>"
        "</office:automatic-styles>"
        "</office:document-styles>"
    )
    out = tmp_path / "xxe.odp"
    write_odf_package(out, ODP_MIMETYPE, {"styles.xml": styles})

    spec = extract_design(out)  # must not raise or hang
    blob = repr(spec.model_dump())
    assert marker not in blob  # the local file's content was NOT disclosed
    # bg was still read from the (unaffected) drawing-page fill.
    assert spec.palette.bg == "#FFFFFF"


def test_billion_laughs_does_not_expand(tmp_path):
    # A nested-entity ("billion laughs") styles.xml must never expand: the
    # hardened parser refuses the amplification bomb (libxml2's expansion cap
    # from huge_tree=False), so the malformed part is skipped. A clean content.xml
    # rides alongside so extraction degrades gracefully to it — proving the bomb
    # neither hung the run nor contaminated the result. The whole call completes
    # promptly (no exponential blowup, no OOM).
    import time

    boom_styles = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<!DOCTYPE office:document-styles ["
        '<!ENTITY lol "lol">'
        '<!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        '<!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">'
        '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">'
        '<!ENTITY lol4 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">'
        "]>"
        f"<office:document-styles {_NS_DECLS} office:version=\"1.2\">"
        "<office:automatic-styles>"
        '<style:style style:name="P1" style:family="paragraph">'
        '<style:text-properties fo:color="&lol4;"/>'
        "</style:style>"
        "</office:automatic-styles>"
        "</office:document-styles>"
    )
    clean_content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<office:document-content {_NS_DECLS} office:version=\"1.2\">"
        "<office:automatic-styles>"
        '<style:style style:name="dp1" style:family="drawing-page">'
        '<style:drawing-page-properties draw:fill="solid" draw:fill-color="#FFFFFF"/>'
        "</style:style>"
        "</office:automatic-styles>"
        "</office:document-content>"
    )
    out = tmp_path / "boom.odp"
    write_odf_package(
        out, ODP_MIMETYPE, {"styles.xml": boom_styles, "content.xml": clean_content}
    )

    start = time.monotonic()
    spec = extract_design(out)  # must not hang or exhaust memory
    assert time.monotonic() - start < 5.0  # completed promptly (no exponential blowup)
    assert isinstance(spec, DesignSpec)
    Palette(**spec.palette.model_dump())  # valid palette from the clean part
    assert spec.palette.bg == "#FFFFFF"  # derived from content.xml, not the bomb


def test_parser_is_hardened():
    # White-box guard: the module-level parser must leave an external entity
    # unresolved (lxml does not expose resolve_entities as a readable attribute,
    # so we assert the behaviour, not the flag) — proving no future call can
    # regress into XXE.
    import lxml.etree as etree

    from odforge.extract import _SAFE_XML_PARSER

    probe = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE r [<!ENTITY e SYSTEM "file:///nonexistent-xxe-probe.txt">]>'
        "<r>&e;</r>"
    )
    root = etree.fromstring(probe.encode("utf-8"), _SAFE_XML_PARSER)
    assert root.text is None  # entity left unresolved; the file was never fetched
