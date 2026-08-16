import dataclasses
import re
import zipfile

import lxml.etree as etree
import pytest

from odforge.ir import (
    DesignSpec,
    FontPair,
    Palette,
    Presentation,
    Slide,
    contrast_ratio,
)
from odforge.media import AssetBlob
from odforge.package import ODP_MIMETYPE
from odforge.render import render
from odforge.render.odp import (
    _GraphicStyles,
    _LogoAsset,
    _line_xml,
    _rect_xml,
    _section_fill,
    build_content_xml,
    build_styles_xml,
    render_odp,
)
from odforge.themes import (
    LAYOUTS,
    PAGE_H,
    PAGE_W,
    SCALES,
    THEME_LABELS,
    THEMES,
    resolve_design,
)
from odforge.validate import find_soffice


def test_all_layouts_exist():
    # Text-first layouts plus shape-rendered visual storytelling layouts.
    assert set(LAYOUTS) == {
        "title", "title-content", "two-col", "section", "big-fact",
        "quote", "agenda", "comparison", "chart", "closing",
        "process", "timeline", "metrics", "cards", "diagram",
        "image-focus", "image-split",
    }


def test_frames_within_page_bounds():
    for name, frames in LAYOUTS.items():
        for f in frames:
            assert f.x >= 0 and f.y >= 0, (name, f.role)
            assert f.x + f.w <= PAGE_W, (name, f.role)
            assert f.y + f.h <= PAGE_H, (name, f.role)


def test_all_presets_exist():
    assert set(THEMES) == {
        "academic", "minimal", "dark", "teal", "forest", "navy", "violet",
        "crimson", "slate", "gold", "sky", "plum", "clay",
    }


def test_every_preset_has_a_display_label():
    # The gallery and the CLI both name themes from THEME_LABELS; a preset with
    # no label would show up as a bare id in the UI.
    assert set(THEME_LABELS) == set(THEMES)


def test_theme_colors_are_valid_hex():
    hexre = re.compile(r"^#[0-9A-Fa-f]{6}$")
    for t in THEMES.values():
        for c in (t.bg, t.title_color, t.text_color, t.accent):
            assert hexre.match(c), c


def test_frames_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        LAYOUTS["title"][0].x = 0


def test_layout_roles_match_expectation():
    assert [f.role for f in LAYOUTS["two-col"]] == ["title", "left", "right"]
    assert [f.role for f in LAYOUTS["big-fact"]] == ["fact", "bullets"]


# ---------------------------------------------------------------------------
# Task 4.2: .odp renderer tests
# ---------------------------------------------------------------------------

NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "manifest": "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0",
}


def content_root(path):
    with zipfile.ZipFile(path) as z:
        return etree.fromstring(z.read("content.xml"))


def test_odp_mimetype(tmp_path, sample_presentation):
    out = render_odp(sample_presentation, tmp_path / "p.odp")
    with zipfile.ZipFile(out) as z:
        assert z.read("mimetype").decode() == ODP_MIMETYPE


def test_one_page_per_slide(tmp_path, sample_presentation):
    root = content_root(render_odp(sample_presentation, tmp_path / "p.odp"))
    assert len(root.findall(".//draw:page", NS)) == len(sample_presentation.slides)


def test_slide_titles_present(tmp_path, sample_presentation):
    root = content_root(render_odp(sample_presentation, tmp_path / "p.odp"))
    all_text = "".join(root.itertext())
    for s in sample_presentation.slides:
        if s.title:
            assert s.title in all_text


def test_bullets_each_get_paragraph(tmp_path):
    p = Presentation(title="t", slides=[
        Slide(layout="title-content", title="T", bullets=["甲", "乙", "丙"])])
    root = content_root(render_odp(p, tmp_path / "p.odp"))
    page = root.findall(".//draw:page", NS)[0]
    texts = [t for t in page.itertext() if t.strip()]
    for b in ["甲", "乙", "丙"]:
        assert b in texts


def test_two_col_has_three_frames(tmp_path, sample_presentation):
    root = content_root(render_odp(sample_presentation, tmp_path / "p.odp"))
    two_col_idx = next(i for i, s in enumerate(sample_presentation.slides) if s.layout == "two-col")
    page = root.findall(".//draw:page", NS)[two_col_idx]
    assert len(page.findall(".//draw:frame", NS)) == 3


def test_notes_rendered(tmp_path, sample_presentation):
    root = content_root(render_odp(sample_presentation, tmp_path / "p.odp"))
    pages = root.findall(".//draw:page", NS)
    for i, s in enumerate(sample_presentation.slides):
        if s.notes:
            notes = pages[i].findall(".//presentation:notes", NS)
            assert notes, f"slide {i} missing notes"
            assert s.notes in "".join(notes[0].itertext())


def test_frame_geometry_in_cm(tmp_path, sample_presentation):
    root = content_root(render_odp(sample_presentation, tmp_path / "p.odp"))
    first_frame = root.findall(".//draw:page", NS)[0].findall(".//draw:frame", NS)[0]
    for attr in ("width", "height", "x", "y"):
        v = first_frame.get("{%s}%s" % (NS["svg"], attr))
        assert v is not None and v.endswith("cm")


def test_xml_escaping(tmp_path):
    p = Presentation(title="a<b>&c", slides=[
        Slide(layout="title", title='x < y & "z"', subtitle="w>v")])
    out = render_odp(p, tmp_path / "p.odp")
    content_root(out)  # lxml 解析不炸 = well-formed
    with zipfile.ZipFile(out) as z:
        assert etree.fromstring(z.read("styles.xml")) is not None
        assert etree.fromstring(z.read("meta.xml")) is not None


def test_styles_xml_master_page_and_bg(tmp_path, sample_presentation):
    render_odp(sample_presentation, tmp_path / "p.odp")
    with zipfile.ZipFile(tmp_path / "p.odp") as z:
        root = etree.fromstring(z.read("styles.xml"))
    assert root.findall(".//style:master-page", NS)
    xml_str = etree.tostring(root).decode()
    assert THEMES["academic"].bg in xml_str  # sample_presentation 是 academic 主題


def test_page_size_16_9(tmp_path, sample_presentation):
    render_odp(sample_presentation, tmp_path / "p.odp")
    with zipfile.ZipFile(tmp_path / "p.odp") as z:
        s = z.read("styles.xml").decode()
    assert "28cm" in s and "15.75cm" in s


def test_dispatch_presentation(tmp_path, sample_presentation):
    out = render(sample_presentation, tmp_path / "p.odp")
    assert out.exists()


def test_cjk_font_size_applied(tmp_path, sample_presentation):
    render_odp(sample_presentation, tmp_path / "p.odp")
    with zipfile.ZipFile(tmp_path / "p.odp") as z:
        content = z.read("content.xml").decode("utf-8")
    assert 'style:font-size-asian="40pt"' in content
    assert 'style:font-weight-asian="bold"' in content


def test_unknown_theme_falls_back_to_academic(tmp_path):
    # Presentation.theme 是 Literal 不會出現未知值;此測試鎖 render_odp 對 THEMES 的取值方式
    p = Presentation(title="t", slides=[Slide(layout="title", title="T")])
    out = render_odp(p, tmp_path / "p.odp")
    assert out.exists()


# ---------------------------------------------------------------------------
# Task 12.2: tokenized Theme + SCALES + resolve_design
# ---------------------------------------------------------------------------

def test_theme_has_full_token_set():
    """The resolved Theme carries the complete design token set."""
    t = THEMES["academic"]
    for field in (
        "bg", "surface", "text", "muted", "accent", "title_color",
        "font_display", "font_body",
        "display_pt", "h1_pt", "body_pt", "caption_pt", "bullet_char",
    ):
        assert hasattr(t, field), field
    assert t.bullet_char == "▪"


def test_theme_compat_aliases_still_resolve():
    """v1 renderer reads theme.text_color / theme.font — keep them working."""
    t = THEMES["dark"]
    assert t.text_color == t.text
    assert t.font == t.font_body


def test_all_presets_pass_contrast_bars():
    """① Every preset clears the same WCAG bars the ir.py validator enforces."""
    for name, t in THEMES.items():
        assert contrast_ratio(t.text, t.bg) >= 4.5, (name, "text/bg")
        assert contrast_ratio(t.accent, t.bg) >= 3.0, (name, "accent/bg")
        assert contrast_ratio(t.muted, t.bg) >= 3.0, (name, "muted/bg")


def test_presets_survive_palette_validation():
    """Presets are legal Palettes — construct one from each preset's colours."""
    for name, t in THEMES.items():
        Palette(bg=t.bg, surface=t.surface, text=t.text, muted=t.muted, accent=t.accent)


def test_presets_use_standard_scale_sizes():
    """Presets default to the 'standard' scale sizes."""
    display, h1, body, caption = SCALES["standard"]
    for t in THEMES.values():
        assert (t.display_pt, t.h1_pt, t.body_pt, t.caption_pt) == (display, h1, body, caption)


def test_scales_three_tiers_strictly_increasing_per_slot():
    """④ compact < standard < display for every (display, h1, body, caption) slot."""
    assert set(SCALES) == {"compact", "standard", "display"}
    for slot in range(4):
        c = SCALES["compact"][slot]
        s = SCALES["standard"][slot]
        d = SCALES["display"][slot]
        assert c < s < d, (slot, c, s, d)


def test_resolve_design_none_returns_preset():
    """② design=None → THEMES[p.theme]."""
    for theme_name in ("academic", "minimal", "dark"):
        p = Presentation(title="t", theme=theme_name,
                         slides=[Slide(layout="title", title="T")])
        assert resolve_design(p) is THEMES[theme_name]


def test_resolve_design_uses_design_colors_and_fonts():
    """③ A valid DesignSpec → Theme colours/fonts/sizes come from the design."""
    design = DesignSpec(
        palette=Palette(bg="#0B1020", surface="#1B2340", text="#F0F3FF",
                        muted="#9AA6D0", accent="#5AD0E0"),
        fonts=FontPair(display="Noto Serif TC", body="Noto Sans TC"),
        scale="display",
        mode="detailed",
    )
    p = Presentation(title="t", theme="academic", design=design,
                     slides=[Slide(layout="title", title="T")])
    t = resolve_design(p)
    assert t.bg == "#0B1020"
    assert t.surface == "#1B2340"
    assert t.text == "#F0F3FF"
    assert t.muted == "#9AA6D0"
    assert t.accent == "#5AD0E0"
    assert t.title_color == "#F0F3FF"  # title_color = text
    assert t.font_display == "Noto Serif TC"
    assert t.font_body == "Noto Sans TC"
    assert (t.display_pt, t.h1_pt, t.body_pt, t.caption_pt) == SCALES["display"]


def test_resolve_design_scale_selects_sizes():
    """design.scale picks the SCALES tier; mode does not alter sizes."""
    for scale in ("compact", "standard", "display"):
        design = DesignSpec(
            palette=Palette(bg="#FFFFFF", surface="#EEEEEE", text="#111111",
                            muted="#666666", accent="#0055AA"),
            fonts=FontPair(display="Noto Sans TC", body="Noto Sans TC"),
            scale=scale,
            mode="presenter",
        )
        p = Presentation(title="t", design=design,
                         slides=[Slide(layout="title", title="T")])
        t = resolve_design(p)
        assert (t.display_pt, t.h1_pt, t.body_pt, t.caption_pt) == SCALES[scale]


# ---------------------------------------------------------------------------
# Task 13.1: graphic primitives (rect / line) + gradient background
# ---------------------------------------------------------------------------


def _q(prefix: str, local: str) -> str:
    """Clark-notation name for a namespaced attribute/element in the NS dict."""
    return "{%s}%s" % (NS[prefix], local)


def parse_fragment(fragment: str):
    """Parse a builder's XML fragment inside a namespaced root element.

    Proves the fragment is well-formed and enables namespace-qualified
    assertions instead of raw substring matching.
    """
    decls = " ".join(f'xmlns:{p}="{u}"' for p, u in NS.items())
    return etree.fromstring(f"<root {decls}>{fragment}</root>".encode())


def test_graphic_styles_dedup_and_sequential_names():
    gs = _GraphicStyles()
    a = gs.name_for_fill("#FF0000")
    b = gs.name_for_fill("#FF0000")       # identical fill → shared style
    c = gs.name_for_fill("#00FF00")       # different fill → new style
    d = gs.name_for_stroke("#0000FF", 2)  # stroke → its own style
    assert a == b == "G1"
    assert c == "G2"
    assert d == "G3"


def test_rect_xml_emits_draw_rect_and_style_fill_color():
    gs = _GraphicStyles()
    name = gs.name_for_fill("#1A4B8C")
    root = parse_fragment(
        _rect_xml(1, 2, 5.5, 3, fill="#1A4B8C", style_name=name) + gs.xml()
    )
    rect = root.find("draw:rect", NS)
    assert rect is not None
    assert rect.get(_q("draw", "style-name")) == name
    assert rect.get(_q("svg", "x")) == "1cm"
    assert rect.get(_q("svg", "y")) == "2cm"
    assert rect.get(_q("svg", "width")) == "5.5cm"
    assert rect.get(_q("svg", "height")) == "3cm"
    style = root.find("style:style", NS)
    assert style is not None
    assert style.get(_q("style", "name")) == name
    assert style.get(_q("style", "family")) == "graphic"
    props = style.find("style:graphic-properties", NS)
    assert props.get(_q("draw", "fill")) == "solid"
    assert props.get(_q("draw", "fill-color")) == "#1A4B8C"
    assert props.get(_q("draw", "stroke")) == "none"  # a pure fill draws no border


def test_rect_fill_opacity_below_one_emits_percentage():
    gs = _GraphicStyles()
    gs.name_for_fill("#000000", opacity=0.5)
    props = parse_fragment(gs.xml()).find(".//style:graphic-properties", NS)
    assert props.get(_q("draw", "opacity")) == "50%"
    # full opacity omits the attribute entirely
    solid = _GraphicStyles()
    solid.name_for_fill("#000000")
    props = parse_fragment(solid.xml()).find(".//style:graphic-properties", NS)
    assert props.get(_q("draw", "opacity")) is None


def test_rounded_rect_emits_corner_radius_only_when_positive():
    gs = _GraphicStyles()
    name = gs.name_for_fill("#FFFFFF")
    rounded = parse_fragment(
        _rect_xml(0, 0, 4, 3, fill="#FFFFFF", corner_radius_cm=0.4, style_name=name)
    ).find("draw:rect", NS)
    assert rounded.get(_q("draw", "corner-radius")) == "0.4cm"
    sharp = parse_fragment(
        _rect_xml(0, 0, 4, 3, fill="#FFFFFF", style_name=name)
    ).find("draw:rect", NS)
    assert sharp.get(_q("draw", "corner-radius")) is None


def test_line_xml_emits_draw_line_and_stroke_style():
    gs = _GraphicStyles()
    name = gs.name_for_stroke("#3DD6E6", 2.0)
    root = parse_fragment(
        _line_xml(1, 1, 10, 1, color="#3DD6E6", width_pt=2.0, style_name=name)
        + gs.xml()
    )
    line = root.find("draw:line", NS)
    assert line is not None
    assert line.get(_q("draw", "style-name")) == name
    assert line.get(_q("svg", "x1")) == "1cm"
    assert line.get(_q("svg", "y1")) == "1cm"
    assert line.get(_q("svg", "x2")) == "10cm"
    assert line.get(_q("svg", "y2")) == "1cm"
    props = root.find(".//style:graphic-properties", NS)
    assert props.get(_q("draw", "stroke")) == "solid"
    assert props.get(_q("svg", "stroke-color")) == "#3DD6E6"
    assert props.get(_q("svg", "stroke-width")) == "2pt"
    assert props.get(_q("draw", "fill")) == "none"  # a pure stroke has no fill


def test_dark_theme_styles_use_gradient_background():
    root = etree.fromstring(build_styles_xml(THEMES["dark"]).encode("utf-8"))
    grads = root.findall(".//draw:gradient", NS)
    assert grads, "dark preset must define a <draw:gradient>"
    grad_name = grads[0].get("{%s}name" % NS["draw"])
    assert grad_name
    dpp = root.findall(".//style:drawing-page-properties", NS)
    assert dpp
    assert all(p.get("{%s}fill" % NS["draw"]) == "gradient" for p in dpp)
    assert all(
        p.get("{%s}fill-gradient-name" % NS["draw"]) == grad_name for p in dpp
    )


def test_light_themes_keep_solid_background():
    for preset in ("academic", "minimal"):
        root = etree.fromstring(build_styles_xml(THEMES[preset]).encode("utf-8"))
        assert not root.findall(".//draw:gradient", NS)
        dpp = root.findall(".//style:drawing-page-properties", NS)
        assert dpp
        assert all(p.get("{%s}fill" % NS["draw"]) == "solid" for p in dpp)
        assert all(
            p.get("{%s}fill-color" % NS["draw"]) == THEMES[preset].bg for p in dpp
        )


def test_dark_theme_content_page_style_uses_gradient():
    """The per-page drawing-page style in content.xml overrides the master's, so
    the dark gradient must be wired there too or it never paints a slide."""
    p = Presentation(title="t", theme="dark",
                     slides=[Slide(layout="title", title="T")])
    croot = etree.fromstring(build_content_xml(p, THEMES["dark"]).encode("utf-8"))
    # the gradient content.xml references must be the one styles.xml defines
    sroot = etree.fromstring(build_styles_xml(THEMES["dark"]).encode("utf-8"))
    grad_name = sroot.findall(".//draw:gradient", NS)[0].get(_q("draw", "name"))
    dpp = croot.findall(".//style:drawing-page-properties", NS)
    assert dpp
    assert all(pr.get(_q("draw", "fill")) == "gradient" for pr in dpp)
    assert all(
        pr.get(_q("draw", "fill-gradient-name")) == grad_name for pr in dpp
    )
    # and every page references that (sole) drawing-page automatic style
    dp_styles = [
        s for s in croot.findall(".//style:style", NS)
        if s.get(_q("style", "family")) == "drawing-page"
    ]
    assert len(dp_styles) == 1
    dp_name = dp_styles[0].get(_q("style", "name"))
    for page in croot.findall(".//draw:page", NS):
        assert page.get(_q("draw", "style-name")) == dp_name


def test_light_theme_content_page_style_stays_solid():
    for preset in ("academic", "minimal"):
        p = Presentation(title="t", theme=preset,
                         slides=[Slide(layout="title", title="T")])
        croot = etree.fromstring(
            build_content_xml(p, THEMES[preset]).encode("utf-8")
        )
        dpp = croot.findall(".//style:drawing-page-properties", NS)
        assert dpp
        assert all(pr.get(_q("draw", "fill")) == "solid" for pr in dpp)
        assert all(
            pr.get(_q("draw", "fill-color")) == THEMES[preset].bg for pr in dpp
        )


# ---------------------------------------------------------------------------
# Task 13.2: semantic text:list + paragraph typography
# ---------------------------------------------------------------------------

from odforge.render.odp import (  # noqa: E402
    _LIST_STYLE_NAME,
    _kicker_paragraph_xml,
    _list_style_xml,
    _list_xml,
    _ParagraphStyles,
)


def _style_by_name(root, name):
    """Return the <style:style> automatic style with style:name == ``name``."""
    return next(
        s for s in root.findall(".//style:style", NS)
        if s.get(_q("style", "name")) == name
    )


# ① bullets -> <text:list> wrapping <text:list-item> (each holding a text:p)
def test_bullets_render_as_semantic_text_list(tmp_path):
    p = Presentation(title="t", slides=[
        Slide(layout="title-content", title="T", bullets=["甲", "乙", "丙"])])
    root = content_root(render_odp(p, tmp_path / "p.odp"))
    lists = root.findall(".//text:list", NS)
    assert lists, "bullets must render as a <text:list>, not bare <text:p>"
    tl = lists[0]
    assert tl.get(_q("text", "style-name")) == _LIST_STYLE_NAME
    items = tl.findall("text:list-item", NS)
    assert len(items) == 3
    assert [it.find("text:p", NS).text for it in items] == ["甲", "乙", "丙"]


# ② automatic-styles carries a <text:list-style> with bullet-char = accent-coloured
def test_list_style_has_bullet_char_in_accent_colour():
    theme = THEMES["academic"]
    root = parse_fragment(_list_style_xml(_LIST_STYLE_NAME, theme))
    ls = root.find("text:list-style", NS)
    assert ls is not None
    assert ls.get(_q("style", "name")) == _LIST_STYLE_NAME
    lvl1 = ls.find("text:list-level-style-bullet", NS)
    assert lvl1.get(_q("text", "bullet-char")) == theme.bullet_char
    tp = lvl1.find("style:text-properties", NS)
    assert tp.get(_q("fo", "color")) == theme.accent


def test_list_style_is_registered_in_content_automatic_styles():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[
        Slide(layout="title-content", title="T", bullets=["甲"])])
    croot = etree.fromstring(build_content_xml(p, theme).encode("utf-8"))
    autos = croot.find("office:automatic-styles", NS)
    assert autos.find("text:list-style", NS) is not None


# ③ nested items render two levels; list-style defines both with deeper indent
def test_nested_items_render_two_level_list():
    styles = _ParagraphStyles("Noto Sans TC")
    items = [("父", ["子一", "子二"]), "另一父"]
    root = parse_fragment(
        _list_xml(items, styles, size_pt=18, color="#111111",
                  style_name=_LIST_STYLE_NAME)
    )
    outer = root.find("text:list", NS)
    assert outer.get(_q("text", "style-name")) == _LIST_STYLE_NAME
    top_items = outer.findall("text:list-item", NS)
    assert [it.find("text:p", NS).text for it in top_items] == ["父", "另一父"]
    nested = top_items[0].find("text:list", NS)
    assert nested is not None, "children must nest a second <text:list>"
    # nested list inherits the level from its position, no repeated style-name
    assert nested.get(_q("text", "style-name")) is None
    assert [ci.find("text:p", NS).text
            for ci in nested.findall("text:list-item", NS)] == ["子一", "子二"]


def test_list_style_defines_two_levels_with_deeper_indent():
    root = parse_fragment(_list_style_xml(_LIST_STYLE_NAME, THEMES["academic"]))
    bullets = root.findall(".//text:list-level-style-bullet", NS)
    by_level = {b.get(_q("text", "level")): b for b in bullets}
    assert set(by_level) >= {"1", "2"}

    def space_cm(b):
        lp = b.find("style:list-level-properties", NS)
        return float(lp.get(_q("text", "space-before")).rstrip("cm"))

    assert space_cm(by_level["2"]) > space_cm(by_level["1"])
    for b in bullets:  # every level's bullet char is accent-coloured
        assert (b.find("style:text-properties", NS).get(_q("fo", "color"))
                == THEMES["academic"].accent)


# ④ bullet paragraphs carry fo:line-height 145% + fo:margin-bottom
def test_bullet_paragraph_has_line_height_and_margin_bottom():
    p = Presentation(title="t", slides=[
        Slide(layout="title-content", title="T", bullets=["甲", "乙"])])
    croot = etree.fromstring(build_content_xml(p, THEMES["academic"]).encode("utf-8"))
    li_p = croot.find(".//text:list/text:list-item/text:p", NS)
    style = _style_by_name(croot, li_p.get(_q("text", "style-name")))
    pp = style.find("style:paragraph-properties", NS)
    assert pp.get(_q("fo", "line-height")) == "145%"
    assert pp.get(_q("fo", "margin-bottom")) == "0.35cm"


def test_non_bullet_paragraphs_keep_tight_typography():
    # Title/subtitle stay bare centred text:p with no injected line-height —
    # be conservative: only bullet/body paragraphs get the loose typography.
    p = Presentation(title="t", slides=[
        Slide(layout="title", title="標題", subtitle="副標")])
    croot = etree.fromstring(build_content_xml(p, THEMES["academic"]).encode("utf-8"))
    bare = croot.findall(".//draw:text-box/text:p", NS)  # direct = non-list
    assert bare
    for tp in bare:
        style = _style_by_name(croot, tp.get(_q("text", "style-name")))
        pp = style.find("style:paragraph-properties", NS)
        assert pp.get(_q("fo", "line-height")) is None
        assert pp.get(_q("fo", "margin-bottom")) is None


# ⑤ kicker paragraph style carries fo:letter-spacing
def test_kicker_style_emits_letter_spacing():
    styles = _ParagraphStyles("Noto Sans TC")
    name = styles.name_for(14, True, False, "#1A4B8C", letter_spacing="0.15cm")
    root = parse_fragment(styles.xml())
    tp = _style_by_name(root, name).find("style:text-properties", NS)
    assert tp.get(_q("fo", "letter-spacing")) == "0.15cm"


def test_kicker_paragraph_helper_references_letter_spacing_style():
    styles = _ParagraphStyles("Noto Sans TC")
    frag = _kicker_paragraph_xml("重點摘要", styles, THEMES["academic"])
    root = parse_fragment(frag + styles.xml())
    kp = root.find("text:p", NS)
    assert kp.text == "重點摘要"
    tp = _style_by_name(root, kp.get(_q("text", "style-name"))).find(
        "style:text-properties", NS)
    assert tp.get(_q("fo", "letter-spacing")) == "0.15cm"


# de-dup key gains the new typography dimensions
def test_paragraph_style_key_includes_typography_dimensions():
    s = _ParagraphStyles("Noto Sans TC")
    a = s.name_for(18, False, False, "#111111")
    b = s.name_for(18, False, False, "#111111", line_height="145%")
    c = s.name_for(18, False, False, "#111111", line_height="145%")
    d = s.name_for(18, False, False, "#111111", letter_spacing="0.15cm")
    assert a != b, "line_height must split the de-dup key"
    assert b == c, "identical dimensions must still de-dup"
    assert a != d and b != d, "letter_spacing must split the de-dup key"


# new/changed text styles keep the CJK three-track font-size rule
def test_bullet_style_keeps_cjk_three_track_font_size():
    p = Presentation(title="t", slides=[
        Slide(layout="title-content", title="T", bullets=["甲"])])
    croot = etree.fromstring(build_content_xml(p, THEMES["academic"]).encode("utf-8"))
    li_p = croot.find(".//text:list/text:list-item/text:p", NS)
    tp = _style_by_name(croot, li_p.get(_q("text", "style-name"))).find(
        "style:text-properties", NS)
    assert tp.get(_q("fo", "font-size")) == "18pt"
    assert tp.get(_q("style", "font-size-asian")) == "18pt"
    assert tp.get(_q("style", "font-size-complex")) == "18pt"


# two-column content also becomes a semantic list (consistent with bullets)
def test_two_col_columns_render_as_lists(tmp_path):
    p = Presentation(title="t", slides=[
        Slide(layout="two-col", title="比較",
              left=["傳統", "耗時"], right=["ODForge", "自動"])])
    root = content_root(render_odp(p, tmp_path / "p.odp"))
    lists = root.findall(".//text:list", NS)
    assert len(lists) == 2  # one per column


# ---------------------------------------------------------------------------
# Task 13.3: master pages + inverted section + accent system
# ---------------------------------------------------------------------------


def _styles_root(theme, title="簡報標題"):
    return etree.fromstring(build_styles_xml(theme, title).encode("utf-8"))


def _master_named(root, name):
    return next(
        m for m in root.findall(".//style:master-page", NS)
        if m.get(_q("style", "name")) == name
    )


def _content_root(p, theme):
    return etree.fromstring(build_content_xml(p, theme).encode("utf-8"))


def _text_p_with(page, text):
    return next(x for x in page.findall(".//text:p", NS) if x.text == text)


# ① styles.xml has TWO <style:master-page> (Standard/Plain); Standard has the
#    page-number frame; Plain has no furniture.
def test_styles_has_two_master_pages_standard_and_plain():
    root = _styles_root(THEMES["academic"])
    names = {m.get(_q("style", "name")) for m in root.findall(".//style:master-page", NS)}
    assert names == {"Standard", "Plain"}


def test_standard_master_contains_page_number_frame():
    root = _styles_root(THEMES["academic"])
    standard = _master_named(root, "Standard")
    pn_frames = [
        f for f in standard.findall(".//draw:frame", NS)
        if f.get(_q("presentation", "class")) == "page-number"
    ]
    assert pn_frames, "Standard master must contain a page-number frame"
    assert pn_frames[0].find(".//text:page-number", NS) is not None


def test_standard_master_has_footer_line_and_kicker_title():
    root = _styles_root(THEMES["academic"], "我的簡報")
    standard = _master_named(root, "Standard")
    assert standard.findall(".//draw:line", NS), "footer line expected on Standard"
    # kicker text is the presentation title
    assert "我的簡報" in "".join(standard.itertext())


def test_plain_master_has_no_furniture():
    root = _styles_root(THEMES["academic"])
    plain = _master_named(root, "Plain")
    assert plain.findall(".//draw:frame", NS) == []
    assert plain.findall(".//draw:line", NS) == []


# LibreOffice only paints master-page shapes when a <draw:layer-set> is declared
# AND each shape carries draw:layer — guard that contract (regression: without
# it the footer line + kicker silently stop rendering).
def test_master_furniture_declares_layer_set_and_layer():
    for preset in ("academic", "minimal", "dark"):
        root = _styles_root(THEMES[preset])
        assert root.findall(".//draw:layer-set", NS), preset
        standard = _master_named(root, "Standard")
        shapes = (standard.findall(".//draw:line", NS)
                  + standard.findall(".//draw:frame", NS))
        assert shapes
        for shape in shapes:
            assert shape.get(_q("draw", "layer")) == "backgroundobjects"


def test_master_page_assignment_by_layout():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[
        Slide(layout="title", title="T", subtitle="s"),
        Slide(layout="title-content", title="C", bullets=["a"]),
        Slide(layout="section", title="S"),
        Slide(layout="two-col", title="X", left=["l"], right=["r"]),
        Slide(layout="big-fact", fact="9"),
    ])
    pages = _content_root(p, theme).findall(".//draw:page", NS)
    masters = [pg.get(_q("draw", "master-page-name")) for pg in pages]
    assert masters == ["Plain", "Standard", "Plain", "Standard", "Standard"]


# master furniture text keeps the CJK three-track font-size rule
def test_master_furniture_text_uses_cjk_three_track():
    root = _styles_root(THEMES["academic"])
    para_styles = [
        s for s in root.findall(".//style:style", NS)
        if s.get(_q("style", "family")) == "paragraph"
    ]
    assert para_styles
    for s in para_styles:
        tp = s.find("style:text-properties", NS)
        if tp is not None and tp.get(_q("fo", "font-size")) is not None:
            assert tp.get(_q("style", "font-size-asian")) is not None
            assert tp.get(_q("style", "font-size-complex")) is not None


# ② section page's drawing-page style fill == accent AND its title style color == bg
def test_section_page_inverted_accent_fill_and_bg_title():
    theme = THEMES["academic"]
    p = Presentation(title="t", theme="academic",
                     slides=[Slide(layout="section", title="研究方法")])
    croot = _content_root(p, theme)
    page = croot.find(".//draw:page", NS)
    dp = _style_by_name(croot, page.get(_q("draw", "style-name")))
    props = dp.find("style:drawing-page-properties", NS)
    assert props.get(_q("draw", "fill")) == "solid"
    # Full-bleed section fill is the accent deepened toward the ink (60-30-10):
    # a raw mid-saturation accent flooded edge-to-edge reads cheap.
    assert props.get(_q("draw", "fill-color")) == _section_fill(theme)
    title_p = _text_p_with(page, "研究方法")
    tstyle = _style_by_name(croot, title_p.get(_q("text", "style-name")))
    assert tstyle.find("style:text-properties", NS).get(_q("fo", "color")) == theme.bg
    assert page.get(_q("draw", "master-page-name")) == "Plain"


def test_section_giant_chapter_number_is_two_digit_ordinal():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[
        Slide(layout="section", title="第一節"),
        Slide(layout="title-content", title="內容", bullets=["x"]),
        Slide(layout="section", title="第二節"),
    ])
    pages = _content_root(p, theme).findall(".//draw:page", NS)
    assert "01" in "".join(pages[0].itertext())
    assert "02" in "".join(pages[2].itertext())


def test_section_giant_number_keeps_cjk_three_track():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[Slide(layout="section", title="S")])
    croot = _content_root(p, theme)
    num_p = _text_p_with(croot.find(".//draw:page", NS), "01")
    tp = _style_by_name(croot, num_p.get(_q("text", "style-name"))).find(
        "style:text-properties", NS)
    assert tp.get(_q("fo", "font-size")) == "96pt"
    assert tp.get(_q("style", "font-size-asian")) == "96pt"
    assert tp.get(_q("style", "font-size-complex")) == "96pt"


# section-only decks add exactly one extra (accent) drawing-page style
def test_section_deck_defines_accent_drawing_page_style():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[
        Slide(layout="title", title="T"),
        Slide(layout="section", title="S"),
    ])
    croot = _content_root(p, theme)
    dp_styles = [
        s for s in croot.findall(".//style:style", NS)
        if s.get(_q("style", "family")) == "drawing-page"
    ]
    fills = {
        s.find("style:drawing-page-properties", NS).get(_q("draw", "fill-color"))
        for s in dp_styles
    }
    assert _section_fill(theme) in fills


# ③ content pages carry the vertical accent bar rect (w ≈ 0.18cm)
def test_content_page_has_vertical_accent_bar():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[
        Slide(layout="title-content", title="大綱", bullets=["A", "B"])])
    croot = _content_root(p, theme)
    page = croot.find(".//draw:page", NS)
    bars = [
        r for r in page.findall(".//draw:rect", NS)
        if r.get(_q("svg", "width")) == "0.18cm"
    ]
    assert bars, "content page must have a vertical accent bar rect w=0.18cm"
    barstyle = _style_by_name(croot, bars[0].get(_q("draw", "style-name")))
    props = barstyle.find("style:graphic-properties", NS)
    assert props.get(_q("draw", "fill-color")) == theme.accent
    assert page.get(_q("draw", "master-page-name")) == "Standard"


def test_title_page_has_no_accent_bar():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[Slide(layout="title", title="T", subtitle="s")])
    page = _content_root(p, theme).find(".//draw:page", NS)
    assert page.findall(".//draw:rect", NS) == []


# ④ big-fact fact style font-size == display_pt and colour == accent; bullets muted
def test_big_fact_fact_style_uses_display_pt_and_accent():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[
        Slide(layout="big-fact", fact="99%", bullets=["涵蓋率支撐說明"])])
    croot = _content_root(p, theme)
    page = croot.find(".//draw:page", NS)
    fact_p = _text_p_with(page, "99%")
    ftp = _style_by_name(croot, fact_p.get(_q("text", "style-name"))).find(
        "style:text-properties", NS)
    assert ftp.get(_q("fo", "font-size")) == f"{theme.display_pt}pt"
    assert ftp.get(_q("fo", "color")) == theme.accent
    supp_p = _text_p_with(page, "涵蓋率支撐說明")
    stp = _style_by_name(croot, supp_p.get(_q("text", "style-name"))).find(
        "style:text-properties", NS)
    assert stp.get(_q("fo", "color")) == theme.muted


# ---------------------------------------------------------------------------
# Task 13.4: SVG decorations on title pages + rounded surface cards on two-col
# ---------------------------------------------------------------------------

from odforge.render.odp import _DECO_HREF, _svg_decoration, _wrap_width  # noqa: E402


# ① The engine-generated decoration is deterministic, accent-coloured, small.
def test_svg_decoration_is_deterministic_accent_and_small():
    theme = THEMES["academic"]
    a = _svg_decoration(theme)
    b = _svg_decoration(theme)
    assert isinstance(a, bytes)
    assert a == b, "decoration must be deterministic (reproducible builds)"
    assert len(a) < 10 * 1024, "decoration must stay small (<10KB)"
    root = etree.fromstring(a)  # well-formed SVG
    assert etree.QName(root).localname == "svg"
    assert theme.accent in a.decode("utf-8"), "decoration takes colour from accent"
    # Different accent → different bytes (engine, not a fixed blob).
    assert _svg_decoration(THEMES["dark"]) != a


# ① title deck: zip has Pictures/*.svg + manifest media-type + draw:image ref.
def test_title_page_embeds_svg_decoration(tmp_path):
    p = Presentation(title="t", slides=[
        Slide(layout="title", title="標題", subtitle="副標")])
    out = render_odp(p, tmp_path / "p.odp")
    with zipfile.ZipFile(out) as z:
        svgs = [n for n in z.namelist()
                if n.startswith("Pictures/") and n.endswith(".svg")]
        assert svgs, "title deck must embed a Pictures/*.svg part"
        man = etree.fromstring(z.read("META-INF/manifest.xml"))
        entry = next(
            e for e in man.findall(".//manifest:file-entry", NS)
            if e.get(_q("manifest", "full-path")) == svgs[0]
        )
        assert entry.get(_q("manifest", "media-type")) == "image/svg+xml"
        croot = etree.fromstring(z.read("content.xml"))
    images = croot.findall(".//draw:image", NS)
    assert images, "content.xml must reference the decoration via <draw:image>"
    assert images[0].get(_q("xlink", "href")) in svgs
    assert images[0].get(_q("xlink", "href")) == _DECO_HREF


def test_non_title_deck_embeds_no_svg(tmp_path):
    p = Presentation(title="t", slides=[
        Slide(layout="title-content", title="C", bullets=["x"])])
    out = render_odp(p, tmp_path / "p.odp")
    with zipfile.ZipFile(out) as z:
        assert not [n for n in z.namelist() if n.endswith(".svg")]
        croot = etree.fromstring(z.read("content.xml"))
    assert croot.findall(".//draw:image", NS) == []


# ② two-col columns each get a rounded surface-coloured card.
def test_two_col_columns_get_rounded_surface_cards():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[
        Slide(layout="two-col", title="比較",
              left=["傳統", "耗時"], right=["ODForge", "自動"])])
    croot = _content_root(p, theme)
    page = croot.find(".//draw:page", NS)
    cards = [
        r for r in page.findall(".//draw:rect", NS)
        if r.get(_q("draw", "corner-radius")) == "0.3cm"
    ]
    assert len(cards) == 2, "each column needs one rounded card"
    for card in cards:
        style = _style_by_name(croot, card.get(_q("draw", "style-name")))
        props = style.find("style:graphic-properties", NS)
        assert props.get(_q("draw", "fill-color")) == theme.surface


# ② cards sit behind the column text (emitted before the text frame → z-order).
def test_two_col_cards_are_behind_column_text():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[
        Slide(layout="two-col", title="比較",
              left=["a", "b"], right=["c", "d"])])
    page = _content_root(p, theme).find(".//draw:page", NS)
    rect_tag, frame_tag = _q("draw", "rect"), _q("draw", "frame")
    children = [el for el in page if el.tag in (rect_tag, frame_tag)]
    card_idx = [
        i for i, el in enumerate(children)
        if el.tag == rect_tag and el.get(_q("draw", "corner-radius"))
    ]
    col_frame_idx = [
        i for i, el in enumerate(children)
        if el.tag == frame_tag and el.find(".//text:list", NS) is not None
    ]
    assert len(card_idx) == 2 and len(col_frame_idx) == 2
    for card_pos, frame_pos in zip(card_idx, col_frame_idx):
        assert card_pos < frame_pos, "card must precede its column text frame"


# ---------------------------------------------------------------------------
# Task 13.5: shape-drawn horizontal bar charts (render side; layout in 14.1)
# ---------------------------------------------------------------------------


def _render_chart(chart, theme=None, area=(1.5, 3.0, 15.0, 10.0)):
    """Render a ChartSpec via ``_chart_xml`` + its graphic styles.

    Returns ``(root, bar_rects)`` where ``root`` wraps the chart fragment and
    the registered graphic styles so a bar's referenced fill can be resolved.
    """
    from odforge.render.odp import _chart_xml, _ParagraphStyles

    theme = theme or THEMES["academic"]
    graphics = _GraphicStyles()
    para = _ParagraphStyles(theme.font)
    frag = _chart_xml(
        chart, area[0], area[1], area[2], area[3], theme, graphics, para
    )
    root = parse_fragment(frag + graphics.xml())
    return root, root.findall(".//draw:rect", NS)


def _cm_value(text):
    assert text.endswith("cm")
    return float(text[:-2])


# ① a chart with N values renders N bar rects whose widths are proportional.
def test_chart_renders_proportional_bars():
    from odforge.ir import ChartSpec

    chart = ChartSpec(labels=["甲", "乙", "丙", "丁"],
                      values=[10, 20, 40, 30], unit="%", highlight=2)
    root, rects = _render_chart(chart)
    assert len(rects) == 4
    widths = [_cm_value(r.get(_q("svg", "width"))) for r in rects]
    max_w, max_v = max(widths), max(chart.values)
    for w, v in zip(widths, chart.values):
        assert abs(w / max_w - v / max_v) < 1e-6


# ② the highlighted bar's fill == accent; every other bar's fill != accent.
def test_chart_highlight_bar_is_accent():
    from odforge.ir import ChartSpec

    theme = THEMES["academic"]
    chart = ChartSpec(labels=["a", "b", "c"], values=[5, 9, 3], highlight=1)
    root, rects = _render_chart(chart, theme)

    def fill_of(rect):
        style = _style_by_name(root, rect.get(_q("draw", "style-name")))
        props = style.find("style:graphic-properties", NS)
        return props.get(_q("draw", "fill-color"))

    assert fill_of(rects[1]) == theme.accent
    assert fill_of(rects[0]) != theme.accent
    assert fill_of(rects[2]) != theme.accent


# each bar carries its label text and its value+unit text.
def test_chart_renders_labels_and_values():
    from odforge.ir import ChartSpec

    chart = ChartSpec(labels=["營收", "成本"], values=[80, 20], unit="萬")
    root, _ = _render_chart(chart)
    text = "".join(root.itertext())
    assert "營收" in text and "成本" in text
    assert "80萬" in text and "20萬" in text


# zero-value edge: no ZeroDivisionError; N bars still render, all zero width.
def test_chart_all_zero_values_have_zero_width():
    from odforge.ir import ChartSpec

    chart = ChartSpec(labels=["x", "y", "z"], values=[0, 0, 0])
    root, rects = _render_chart(chart)
    assert len(rects) == 3
    for r in rects:
        assert _cm_value(r.get(_q("svg", "width"))) == 0.0


# ---------------------------------------------------------------------------
# Task 14.1: five new page-role layouts + kicker + resolve_design wiring
# ---------------------------------------------------------------------------

from odforge.ir import BulletItem  # noqa: E402


def test_new_layouts_registered_and_in_bounds():
    # ② the five new keys exist; the parameterized boundary test above already
    #    proves every frame (including the new ones) sits within 28×15.75.
    for key in ("quote", "agenda", "comparison", "chart", "closing"):
        assert LAYOUTS.get(key)


# --- quote -----------------------------------------------------------------
def test_quote_page_renders_quote_attribution_and_mark():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[
        Slide(layout="quote", quote="知識就是力量", attribution="— 培根")])
    croot = _content_root(p, theme)
    page = croot.find(".//draw:page", NS)
    text = "".join(page.itertext())
    assert "知識就是力量" in text
    assert "— 培根" in text
    # decorative quote mark glyph present (deterministic engine text, not LLM).
    assert "“" in text
    # quote text is h1_pt, centred; attribution is caption_pt, muted, centred.
    qp = _text_p_with(page, "知識就是力量")
    qs = _style_by_name(croot, qp.get(_q("text", "style-name")))
    assert qs.find("style:text-properties", NS).get(_q("fo", "font-size")) == \
        f"{theme.h1_pt}pt"
    assert qs.find("style:paragraph-properties", NS).get(_q("fo", "text-align")) == \
        "center"
    ap = _text_p_with(page, "— 培根")
    as_ = _style_by_name(croot, ap.get(_q("text", "style-name")))
    atp = as_.find("style:text-properties", NS)
    assert atp.get(_q("fo", "font-size")) == f"{theme.caption_pt}pt"
    assert atp.get(_q("fo", "color")) == theme.muted


# --- agenda ----------------------------------------------------------------
def test_agenda_numbers_are_accent_colored():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[
        Slide(layout="agenda", title="議程", bullets=["背景", "方法", "成果"])])
    croot = _content_root(p, theme)
    page = croot.find(".//draw:page", NS)
    text = "".join(page.itertext())
    assert "01" in text and "02" in text and "03" in text
    span = next(s for s in page.findall(".//text:span", NS) if s.text == "01")
    stp = _style_by_name(croot, span.get(_q("text", "style-name"))).find(
        "style:text-properties", NS)
    assert stp.get(_q("fo", "color")) == theme.accent
    # numbers keep CJK three-track sizing at body_pt.
    assert stp.get(_q("fo", "font-size")) == f"{theme.body_pt}pt"
    assert stp.get(_q("style", "font-size-asian")) == f"{theme.body_pt}pt"


# --- comparison ------------------------------------------------------------
def test_comparison_headers_bold_accent_body_pt_and_cards():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[
        Slide(layout="comparison", title="比較",
              left=["傳統", "慢", "貴"], right=["ODForge", "快", "省"])])
    croot = _content_root(p, theme)
    page = croot.find(".//draw:page", NS)
    for header in ("傳統", "ODForge"):
        hp = _text_p_with(page, header)
        tp = _style_by_name(croot, hp.get(_q("text", "style-name"))).find(
            "style:text-properties", NS)
        assert tp.get(_q("fo", "color")) == theme.accent
        assert tp.get(_q("fo", "font-weight")) == "bold"
        assert tp.get(_q("fo", "font-size")) == f"{theme.body_pt}pt"
    # remaining items render as bullet lists (one per column).
    assert len(page.findall(".//text:list", NS)) == 2
    # cards still appear automatically via the left/right roles (13.4 gating).
    cards = [r for r in page.findall(".//draw:rect", NS)
             if r.get(_q("draw", "corner-radius")) == "0.3cm"]
    assert len(cards) == 2


# --- chart -----------------------------------------------------------------
def test_chart_layout_renders_bars_and_insights():
    from odforge.ir import ChartSpec

    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[
        Slide(layout="chart", title="成長",
              chart=ChartSpec(labels=["Q1", "Q2", "Q3"], values=[10, 20, 40],
                              unit="萬", highlight=2),
              bullets=["洞見一", "洞見二"])])
    croot = _content_root(p, theme)
    page = croot.find(".//draw:page", NS)
    text = "".join(page.itertext())
    assert "Q1" in text and "40萬" in text
    assert "洞見一" in text and "洞見二" in text
    # three data bars (bar rects have the chart corner radius 0.08cm).
    bars = [r for r in page.findall(".//draw:rect", NS)
            if r.get(_q("draw", "corner-radius")) == "0.08cm"]
    assert len(bars) == 3
    # insights render at caption_pt.
    ip = _text_p_with(page, "洞見一")
    itp = _style_by_name(croot, ip.get(_q("text", "style-name"))).find(
        "style:text-properties", NS)
    assert itp.get(_q("fo", "font-size")) == f"{theme.caption_pt}pt"


# --- closing ---------------------------------------------------------------
def test_closing_inverted_no_watermark_with_subtitle():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[
        Slide(layout="section", title="第一節"),
        Slide(layout="closing", title="謝謝聆聽", subtitle="Q&A")])
    croot = _content_root(p, theme)
    pages = croot.findall(".//draw:page", NS)
    closing = pages[1]
    assert closing.get(_q("draw", "master-page-name")) == "Plain"
    dp = _style_by_name(croot, closing.get(_q("draw", "style-name")))
    props = dp.find("style:drawing-page-properties", NS)
    assert props.get(_q("draw", "fill")) == "solid"
    assert props.get(_q("draw", "fill-color")) == _section_fill(theme)
    mp = _text_p_with(closing, "謝謝聆聽")
    mtp = _style_by_name(croot, mp.get(_q("text", "style-name"))).find(
        "style:text-properties", NS)
    assert mtp.get(_q("fo", "color")) == theme.bg
    # subtitle rendered as the message's second line.
    assert "Q&A" in "".join(closing.itertext())
    # NO giant ordinal watermark on the closing page.
    ct = "".join(closing.itertext())
    assert "01" not in ct and "02" not in ct
    # closing must NOT consume the section ordinal counter.
    assert "01" in "".join(pages[0].itertext())


def test_closing_renders_action_bullets_as_surface_cards():
    theme = THEMES["academic"]
    p = Presentation(
        title="t",
        slides=[
            Slide(
                layout="closing",
                title="核准 90 天試行",
                bullets=["確認試行範圍", "指定跨職類負責人", "每週檢視瓶頸"],
            )
        ],
    )
    croot = _content_root(p, theme)
    page = croot.find(".//draw:page", NS)
    text = "".join(page.itertext())
    assert "確認試行範圍" in text
    assert "指定跨職類負責人" in text
    assert "每週檢視瓶頸" in text
    for ordinal in ("01", "02", "03"):
        assert ordinal in text

    surface_cards = []
    for rect in page.findall(".//draw:rect", NS):
        style = _style_by_name(croot, rect.get(_q("draw", "style-name")))
        props = style.find("style:graphic-properties", NS)
        if props is not None and props.get(_q("draw", "fill-color")) == theme.surface:
            surface_cards.append(rect)
    assert len(surface_cards) == 3


def test_section_ordinals_skip_closing_pages():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[
        Slide(layout="section", title="第一"),
        Slide(layout="closing", title="結束"),
        Slide(layout="section", title="第二")])
    pages = _content_root(p, theme).findall(".//draw:page", NS)
    assert "01" in "".join(pages[0].itertext())
    assert "02" in "".join(pages[2].itertext())


def test_closing_without_section_still_paints_accent_bg():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[Slide(layout="closing", title="謝謝")])
    croot = _content_root(p, theme)
    page = croot.find(".//draw:page", NS)
    dp = _style_by_name(croot, page.get(_q("draw", "style-name")))
    assert dp.find("style:drawing-page-properties", NS).get(
        _q("draw", "fill-color")) == _section_fill(theme)


def test_closing_has_no_section_watermark_shape():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[Slide(layout="closing", title="尾聲")])
    page = _content_root(p, theme).find(".//draw:page", NS)
    # the giant watermark is a 96pt text frame; closing must have none.
    for tp in page.findall(".//text:p", NS):
        assert tp.text != "01"


# --- kicker ----------------------------------------------------------------
def test_kicker_renders_above_title_with_letter_spacing():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[
        Slide(layout="title-content", title="大綱", kicker="第一部分",
              bullets=["a"])])
    croot = _content_root(p, theme)
    page = croot.find(".//draw:page", NS)
    kp = _text_p_with(page, "第一部分")
    ktp = _style_by_name(croot, kp.get(_q("text", "style-name"))).find(
        "style:text-properties", NS)
    assert ktp.get(_q("fo", "letter-spacing")) == "0.15cm"
    assert ktp.get(_q("fo", "color")) == theme.accent
    # kicker paragraph precedes the title paragraph inside the same frame.
    title_frame = next(
        f for f in page.findall(".//draw:frame", NS)
        if any(x.text == "大綱" for x in f.findall(".//text:p", NS)))
    ps = title_frame.findall(".//text:p", NS)
    assert ps[0].text == "第一部分" and ps[1].text == "大綱"


def test_no_kicker_leaves_title_frame_unchanged():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[
        Slide(layout="title-content", title="大綱", bullets=["a"])])
    page = _content_root(p, theme).find(".//draw:page", NS)
    title_frame = next(
        f for f in page.findall(".//draw:frame", NS)
        if any(x.text == "大綱" for x in f.findall(".//text:p", NS)))
    assert len(title_frame.findall(".//text:p", NS)) == 1


# --- nested BulletItem renders a two-level list -----------------------------
def test_nested_bulletitem_renders_two_level_list(tmp_path):
    p = Presentation(title="t", slides=[
        Slide(layout="title-content", title="T",
              bullets=[BulletItem(text="父", children=["子一", "子二"]), "葉"])])
    root = content_root(render_odp(p, tmp_path / "p.odp"))
    outer = root.find(".//text:list", NS)
    top = outer.findall("text:list-item", NS)
    assert top[0].find("text:p", NS).text == "父"
    assert top[0].find("text:list", NS) is not None
    assert top[1].find("text:p", NS).text == "葉"


# --- resolve_design wiring --------------------------------------------------
def test_custom_designspec_renders_with_palette_colors(tmp_path):
    design = DesignSpec(
        palette=Palette(bg="#0B1020", surface="#1B2340", text="#F0F3FF",
                        muted="#9AA6D0", accent="#5AD0E0"),
        fonts=FontPair(display="Noto Serif TC", body="Noto Sans TC"))
    p = Presentation(title="設計", design=design, slides=[
        Slide(layout="title-content", title="標題", bullets=["內容"])])
    out = render_odp(p, tmp_path / "p.odp")
    with zipfile.ZipFile(out) as z:
        styles = z.read("styles.xml").decode("utf-8")
        content = z.read("content.xml").decode("utf-8")
    assert "#0B1020" in styles          # custom bg reaches styles.xml
    assert "#0B1020" in content         # ... and the per-page fill in content.xml
    assert "#5AD0E0" in content         # custom accent (bullet char / accent bar)
    assert "#F0F3FF" in content         # custom text colour


# ---------------------------------------------------------------------------
# big-fact fit-to-width + caption never overlaps (fix: long fact overlapping
# the muted caption). Deterministic engine guarantees, not prompt advice.
# ---------------------------------------------------------------------------

from odforge.textmetrics import PT_TO_CM, estimate_height_cm, fact_font_size_pt  # noqa: E402


def _fact_size_pt(croot, fact_text):
    """Rendered font-size (pt, int) of the big-fact fact paragraph."""
    page = croot.find(".//draw:page", NS)
    fact_p = _text_p_with(page, fact_text)
    tp = _style_by_name(croot, fact_p.get(_q("text", "style-name"))).find(
        "style:text-properties", NS)
    return int(tp.get(_q("fo", "font-size")).removesuffix("pt"))


def _frame_y_cm_of_text(page, text):
    """svg:y (cm, float) of the draw:frame enclosing the text:p holding ``text``."""
    p = _text_p_with(page, text)
    frame = p.getparent().getparent()  # text:p -> draw:text-box -> draw:frame
    return float(frame.get(_q("svg", "y")).removesuffix("cm"))


# ① short fact renders at display_pt (regression — no needless shrinking).
def test_big_fact_short_fact_keeps_display_pt():
    theme = THEMES["academic"]
    p = Presentation(title="t", slides=[
        Slide(layout="big-fact", fact="99%", bullets=["涵蓋率"])])
    croot = _content_root(p, theme)
    assert _fact_size_pt(croot, "99%") == theme.display_pt


# ② a long mixed CJK/ASCII fact is shrunk below display_pt but not past h1_pt.
def test_big_fact_long_fact_shrinks_between_h1_and_display():
    theme = THEMES["academic"]
    long_fact = "人工智慧模型推論速度提升約3倍並降低成本"  # 20+ chars, mixed
    p = Presentation(title="t", slides=[
        Slide(layout="big-fact", fact=long_fact, bullets=["說明"])])
    croot = _content_root(p, theme)
    size = _fact_size_pt(croot, long_fact)
    assert theme.h1_pt <= size < theme.display_pt, size


# ③ an extreme fact still wraps at the h1_pt floor → caption drops below the
#    fact's estimated bottom (the two frames never intersect).
def test_big_fact_extreme_fact_pushes_caption_clear():
    theme = THEMES["academic"]
    extreme = "字" * 60  # forces multiple lines even at the h1_pt floor
    caption = "這是下方的輔助說明文字"
    p = Presentation(title="t", slides=[
        Slide(layout="big-fact", fact=extreme, bullets=[caption])])
    croot = _content_root(p, theme)
    page = croot.find(".//draw:page", NS)
    fact_frame = LAYOUTS["big-fact"][0]
    fitted = fact_font_size_pt(extreme, fact_frame.w, theme.display_pt, theme.h1_pt)
    fact_bottom = fact_frame.y + estimate_height_cm(extreme, fitted, fact_frame.w)
    caption_y = _frame_y_cm_of_text(page, caption)
    # Non-overlap invariant: caption top sits at or below the fact's real bottom.
    assert caption_y >= fact_bottom, (caption_y, fact_bottom)
    # And it genuinely moved off its static y (10cm) because the fact overran.
    assert caption_y > LAYOUTS["big-fact"][1].y


# ---------------------------------------------------------------------------
# Shape-rendered visual storytelling layouts
# ---------------------------------------------------------------------------


def _visual_cards(page):
    return [
        rect
        for rect in page.findall(".//draw:rect", NS)
        if rect.get(_q("draw", "corner-radius")) == "0.28cm"
    ]


def test_process_layout_renders_connected_numbered_cards():
    theme = THEMES["academic"]
    p = Presentation(
        title="t",
        slides=[
            Slide(
                layout="process",
                title="交付流程",
                steps=[
                    {"title": "盤點", "detail": "確認目標與限制"},
                    {"title": "設計", "detail": "建立資訊架構"},
                    {"title": "驗收", "detail": "依結果調整"},
                ],
            )
        ],
    )
    page = _content_root(p, theme).find(".//draw:page", NS)
    text = "".join(page.itertext())
    assert all(word in text for word in ("盤點", "設計", "驗收", "1", "3"))
    assert len(_visual_cards(page)) == 3
    assert len(page.findall(".//draw:ellipse", NS)) == 3
    assert len(page.findall(".//draw:line", NS)) == 1


def test_timeline_layout_renders_nodes_stems_and_event_cards():
    theme = THEMES["academic"]
    p = Presentation(
        title="t",
        slides=[
            Slide(
                layout="timeline",
                title="產品演進",
                events=[
                    {"label": "Q1", "title": "研究", "detail": "確認問題"},
                    {"label": "Q2", "title": "試辦", "detail": "蒐集回饋"},
                    {"label": "Q3", "title": "上線", "detail": "全面推廣"},
                ],
            )
        ],
    )
    page = _content_root(p, theme).find(".//draw:page", NS)
    text = "".join(page.itertext())
    assert all(word in text for word in ("Q1", "研究", "Q3", "上線"))
    assert len(_visual_cards(page)) == 3
    assert len(page.findall(".//draw:ellipse", NS)) == 3
    assert len(page.findall(".//draw:line", NS)) == 4


def test_metrics_layout_renders_large_value_cards():
    theme = THEMES["academic"]
    p = Presentation(
        title="t",
        slides=[
            Slide(
                layout="metrics",
                title="關鍵成果",
                metrics=[
                    {"value": "42%", "label": "轉換率", "detail": "較上期提升"},
                    {"value": "3.2x", "label": "處理速度", "detail": "流程自動化"},
                    {"value": "18h", "label": "節省工時", "detail": "每週平均"},
                ],
            )
        ],
    )
    croot = _content_root(p, theme)
    page = croot.find(".//draw:page", NS)
    text = "".join(page.itertext())
    assert all(word in text for word in ("42%", "3.2x", "18h"))
    assert len(_visual_cards(page)) == 3
    value_p = _text_p_with(page, "42%")
    value_style = _style_by_name(
        croot, value_p.get(_q("text", "style-name"))
    ).find("style:text-properties", NS)
    assert value_style.get(_q("style", "font-name")) == theme.font_display
    assert value_style.get(_q("fo", "color")) == theme.accent


def test_cards_layout_turns_parallel_ideas_into_grid():
    theme = THEMES["academic"]
    p = Presentation(
        title="t",
        slides=[
            Slide(
                layout="cards",
                title="四大支柱",
                bullets=["策略清楚", "流程順暢", "資料可信", "持續改善"],
            )
        ],
    )
    page = _content_root(p, theme).find(".//draw:page", NS)
    text = "".join(page.itertext())
    assert all(word in text for word in ("策略清楚", "流程順暢", "持續改善"))
    assert len(_visual_cards(page)) == 4
    assert "01" in text and "04" in text


def _card_heights(page):
    return [
        float(card.get(_q("svg", "height")).removesuffix("cm"))
        for card in _visual_cards(page)
    ]


def _cards_page(bullets, theme=None):
    theme = theme or THEMES["academic"]
    p = Presentation(
        title="t",
        slides=[Slide(layout="cards", title="標題", bullets=bullets)],
    )
    return _content_root(p, theme).find(".//draw:page", NS)


# A one-line card used to be drawn at a flat 6.8cm — twice the height of its
# content — leaving the bottom 60% of the box empty. Cards are sized to what
# they actually hold, and stay uniform across the row.
def test_short_cards_are_sized_to_their_content_not_the_full_area():
    page = _cards_page(["跨平台相容", "長期可讀", "零授權成本"])
    heights = _card_heights(page)
    assert len(heights) == 3
    assert len(set(heights)) == 1, "cards in a row must stay equal height"
    assert heights[0] < 4.5, f"one-line card should not sprawl: {heights[0]}cm"
    assert heights[0] >= 3.0, f"card must still read as a container: {heights[0]}cm"


def test_wrapping_card_titles_grow_the_card():
    short = _card_heights(_cards_page(["策略", "流程", "資料"]))[0]
    longer = _card_heights(
        _cards_page(
            [
                "策略清楚並且能夠讓每一個部門都對齊同一份年度目標與衡量指標",
                "流程",
                "資料",
            ]
        )
    )[0]
    assert longer > short, "a title that wraps must not be clipped by a fixed box"


def test_cards_with_children_get_room_for_them():
    plain = _card_heights(_cards_page(["策略清楚", "流程順暢"]))[0]
    nested = _card_heights(
        _cards_page(
            [
                BulletItem(text="策略清楚", children=["季度校準", "指標公開"]),
                BulletItem(text="流程順暢", children=["自動化"]),
            ]
        )
    )[0]
    assert nested > plain


def test_card_title_frame_is_tall_enough_for_its_wrapped_text():
    # The title box used to be a flat 1.65cm; a three-line CJK title overran it.
    long_title = "策略清楚並且能夠讓每一個部門都對齊同一份年度目標與共同衡量指標"
    page = _cards_page([long_title, "流程", "資料"])
    card = _visual_cards(page)[0]
    card_h = float(card.get(_q("svg", "height")).removesuffix("cm"))
    card_y = float(card.get(_q("svg", "y")).removesuffix("cm"))
    frames = [
        f
        for f in page.findall(".//draw:frame", NS)
        if long_title in "".join(f.itertext())
    ]
    assert frames, "the wrapped title must still be rendered"
    frame = frames[0]
    top = float(frame.get(_q("svg", "y")).removesuffix("cm"))
    height = float(frame.get(_q("svg", "height")).removesuffix("cm"))
    width = float(frame.get(_q("svg", "width")).removesuffix("cm"))
    needed = estimate_height_cm(long_title, min(THEMES["academic"].body_pt, 18), width, 1.2)
    assert height >= needed - 0.01, f"title box {height}cm < wrapped text {needed}cm"
    assert top + height <= card_y + card_h + 0.01, "title must stay inside its card"


def test_diagram_layout_renders_nodes_edges_and_labels():
    theme = THEMES["academic"]
    p = Presentation(
        title="t",
        slides=[
            Slide(
                layout="diagram",
                title="服務架構",
                diagram={
                    "kind": "hub",
                    "nodes": [
                        {"id": "core", "title": "核心 API", "emphasis": True},
                        {"id": "web", "title": "前端"},
                        {"id": "data", "title": "資料層"},
                        {"id": "ops", "title": "監控"},
                    ],
                    "edges": [
                        {"source": "core", "target": "web", "label": "提供"},
                        {"source": "core", "target": "data", "label": "讀寫"},
                        {"source": "core", "target": "ops", "label": "回報"},
                    ],
                },
            )
        ],
    )
    page = _content_root(p, theme).find(".//draw:page", NS)
    text = "".join(page.itertext())
    assert all(word in text for word in ("核心 API", "前端", "資料層", "監控"))
    assert all(word in text for word in ("提供", "讀寫", "回報"))
    assert len(_visual_cards(page)) == 4
    assert len(page.findall(".//draw:line", NS)) == 3


def test_sources_render_as_clickable_footer_links():
    theme = THEMES["academic"]
    p = Presentation(
        title="t",
        slides=[
            Slide(
                layout="title-content",
                title="研究發現",
                bullets=["重點"],
                sources=[
                    {
                        "label": "World Bank 2025",
                        "url": "https://example.com/report?a=1&b=2",
                    },
                    {"label": "內部訪談"},
                ],
            )
        ],
    )
    page = _content_root(p, theme).find(".//draw:page", NS)
    text = "".join(page.itertext())
    assert "來源" in text
    assert "World Bank 2025" in text
    assert "內部訪談" in text
    links = page.findall(".//text:a", NS)
    assert len(links) == 1
    assert links[0].get(_q("xlink", "href")) == "https://example.com/report?a=1&b=2"


def test_content_titles_use_display_font():
    theme = THEMES["academic"]
    p = Presentation(
        title="t",
        slides=[Slide(layout="title-content", title="研究發現", bullets=["重點"])],
    )
    croot = _content_root(p, theme)
    page = croot.find(".//draw:page", NS)
    title_p = _text_p_with(page, "研究發現")
    props = _style_by_name(
        croot, title_p.get(_q("text", "style-name"))
    ).find("style:text-properties", NS)
    assert props.get(_q("style", "font-name")) == theme.font_display


def test_image_focus_embeds_raster_as_native_package_part(tmp_path):
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000a49444154789c6360000002000154a24f3b0000000049454e44ae426082"
    )
    presentation = Presentation(
        title="圖片簡報",
        slides=[
            Slide(
                layout="image-focus",
                title="現場證據",
                image={
                    "src": "asset://hero",
                    "alt": "醫療團隊合作",
                    "caption": "改善後的跨職類協作",
                    "credit": "內部影像",
                },
            )
        ],
    )
    out = tmp_path / "image.odp"
    render_odp(presentation, out, assets={"hero": png})

    with zipfile.ZipFile(out) as package:
        picture_names = [
            name for name in package.namelist() if name.startswith("Pictures/image-")
        ]
        assert len(picture_names) == 1
        assert package.read(picture_names[0]) == png
        content = package.read("content.xml").decode("utf-8")
        manifest = package.read("META-INF/manifest.xml").decode("utf-8")
    assert f'xlink:href="{picture_names[0]}"' in content
    assert "改善後的跨職類協作 · 內部影像" in content
    assert 'manifest:media-type="image/png"' in manifest


def test_missing_image_asset_renders_honest_placeholder():
    presentation = Presentation(
        title="圖片簡報",
        slides=[
            Slide(
                layout="image-split",
                title="情境",
                bullets=["觀察一", "觀察二"],
                image={"src": "asset://missing", "alt": "候診區現場"},
            )
        ],
    )
    xml = build_content_xml(presentation, THEMES["academic"])
    assert "圖片待補" in xml
    assert "候診區現場" in xml


def test_generated_image_is_materialized_once_for_repeat_renders(tmp_path):
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000a49444154789c6360000002000154a24f3b0000000049454e44ae426082"
    )

    class Provider:
        calls = 0

        def generate(self, prompt):
            self.calls += 1
            return AssetBlob(png, "image/png", ".png", 1, 1)

    provider = Provider()
    assets = {}
    presentation = Presentation(
        title="生成圖",
        slides=[
            Slide(
                layout="image-focus",
                title="概念",
                image={"prompt": "editorial concept", "alt": "概念圖"},
            )
        ],
    )
    render_odp(
        presentation,
        tmp_path / "first.odp",
        assets=assets,
        image_provider=provider,
    )
    render_odp(
        presentation,
        tmp_path / "second.odp",
        assets=assets,
        image_provider=provider,
    )
    assert provider.calls == 1
    assert presentation.slides[0].image.src.startswith("asset://generated-")
    assert len(assets) == 1


# ===========================================================================
# Shape-rendered layouts must not stack text on top of text.
#
# timeline/process/cards each drew a fixed-height title box with the detail
# pinned at a fixed offset below it. A title that wrapped to two lines overran
# its box and printed straight through the detail beneath — 「資料結構與演算法」
# landing on top of 「核心基礎」 in a live run. Each frame is sized to its own
# measured text now, and the ones after it move down to make room.
# ===========================================================================

from odforge.ir import ProcessStep, TimelineEvent  # noqa: E402


def _page_text_frames(page):
    """(x, y, w, h, text) for every positioned text frame carrying words."""
    out = []
    for frame in page.findall(".//draw:frame", NS):
        text = "".join(frame.itertext()).strip()
        if not text:
            continue
        out.append(
            (
                float(frame.get(_q("svg", "x")).removesuffix("cm")),
                float(frame.get(_q("svg", "y")).removesuffix("cm")),
                float(frame.get(_q("svg", "width")).removesuffix("cm")),
                float(frame.get(_q("svg", "height")).removesuffix("cm")),
                text,
            )
        )
    return out


def _overflowing_text(root, page, tol: float = 0.05):
    """Frames whose measured text is taller than the box drawn to hold it.

    Box intersection is the wrong test for this defect: the timeline's title box
    ended at ``card_y+1.90`` and its detail began at ``card_y+2.05``, so the two
    boxes never touched — while a two-line title spilled straight out of the
    first and printed over the second. What overlaps is the *ink*, so what has
    to be measured is the text against the box that is supposed to contain it.
    """
    spills = []
    for frame in page.findall(".//draw:frame", NS):
        text = "".join(frame.itertext()).strip()
        if not text or "\n" in text:
            continue
        paragraph = frame.find(".//text:p", NS)
        if paragraph is None:
            continue
        props = _style_by_name(
            root, paragraph.get(_q("text", "style-name"))
        ).find("style:text-properties", NS)
        if props is None or props.get(_q("fo", "font-size")) is None:
            continue
        size_pt = float(props.get(_q("fo", "font-size")).removesuffix("pt"))
        para_props = _style_by_name(
            root, paragraph.get(_q("text", "style-name"))
        ).find("style:paragraph-properties", NS)
        line_height = 1.35
        if para_props is not None and para_props.get(_q("fo", "line-height")):
            line_height = (
                float(para_props.get(_q("fo", "line-height")).removesuffix("%")) / 100
            )
        # LibreOffice insets the box on both sides, so the text has less room
        # than the frame is wide; measuring against the full width would let a
        # box that actually wraps look like it fits.
        width = _wrap_width(
            float(frame.get(_q("svg", "width")).removesuffix("cm"))
        )
        height = float(frame.get(_q("svg", "height")).removesuffix("cm"))
        needed = estimate_height_cm(text, size_pt, width, line_height)
        # Only wrapping causes the damage. A one-line label in a deliberately
        # tight box (a badge digit) centres harmlessly; a title that needs a
        # second line is the one that prints into whatever sits below it.
        one_line = size_pt * PT_TO_CM * line_height
        if needed > one_line * 1.5 and needed > height + tol:
            spills.append((text[:16], round(needed, 2), round(height, 2)))
    return spills


_WRAPPING_TITLE = "資料結構與演算法"


def _timeline_root(theme=None):
    theme = theme or THEMES["academic"]
    p = Presentation(
        title="t",
        slides=[
            Slide(
                layout="timeline",
                title="學習路徑",
                events=[
                    TimelineEvent(label="大一", title="程式設計入門", detail="C 與 Python"),
                    TimelineEvent(label="大二", title=_WRAPPING_TITLE, detail="核心基礎"),
                    TimelineEvent(label="大三", title="專題與實習", detail="動手做"),
                    TimelineEvent(label="大四", title="畢業專題", detail="成果發表"),
                ],
            )
        ],
    )
    root = _content_root(p, theme)
    return root, root.find(".//draw:page", NS)


def _process_root(theme=None):
    theme = theme or THEMES["academic"]
    p = Presentation(
        title="t",
        slides=[
            Slide(
                layout="process",
                title="導入流程",
                steps=[
                    ProcessStep(title="盤點現況", detail="釐清痛點"),
                    ProcessStep(title=_WRAPPING_TITLE, detail="核心基礎"),
                    ProcessStep(title="評估成效", detail="用數字驗證"),
                ],
            )
        ],
    )
    root = _content_root(p, theme)
    return root, root.find(".//draw:page", NS)


def test_timeline_wrapping_title_does_not_print_through_the_detail():
    spills = _overflowing_text(*_timeline_root())
    assert spills == [], f"text spilling out of its box: {spills}"


def test_process_wrapping_title_does_not_print_through_the_detail():
    spills = _overflowing_text(*_process_root())
    assert spills == [], f"text spilling out of its box: {spills}"


def test_cards_wrapping_title_does_not_print_through_the_children():
    theme = THEMES["academic"]
    p = Presentation(
        title="t",
        slides=[
            Slide(
                layout="cards",
                title="標題",
                bullets=[
                    BulletItem(text=_WRAPPING_TITLE + "與其應用", children=["季度校準"]),
                    BulletItem(text="流程順暢", children=["自動化"]),
                ],
            )
        ],
    )
    root = _content_root(p, theme)
    spills = _overflowing_text(root, root.find(".//draw:page", NS))
    assert spills == [], f"text spilling out of its box: {spills}"


def _closing_root(actions, theme=None):
    theme = theme or THEMES["academic"]
    p = Presentation(
        title="t",
        slides=[Slide(layout="closing", title="下一步", bullets=actions)],
    )
    root = _content_root(p, theme)
    return root, root.find(".//draw:page", NS)


# A live run put 「節約用電:隨手關燈,減少待機耗電」 on a closing card drawn at a
# flat 3.35cm: the third line landed on the inverted background in near-black
# ink — unreadable, on the last page anyone looks at.
_SPILLING_ACTION = "節約用電：隨手關燈，減少待機耗電"


def test_closing_action_cards_hold_a_three_line_action():
    root, page = _closing_root(
        [_SPILLING_ACTION, "減少食物浪費：吃多少煮多少", "綠色運輸：多走路、騎單車或搭公車"]
    )
    spills = _overflowing_text(root, page)
    assert spills == [], f"action text spilling onto the inverted page: {spills}"


def test_closing_action_cards_grow_with_their_text():
    # Four across is where the cards get narrow enough to wrap — and it is the
    # count both live failures had.
    short = _card_heights(
        _closing_root(["確認範圍", "指定負責人", "每週檢視", "啟動預約"])[1]
    )
    longer = _card_heights(
        _closing_root(
            [
                _SPILLING_ACTION,
                "減少食物浪費：吃多少煮多少",
                "綠色運輸：多走路、騎單車或搭公車",
                "你的選擇，決定地球的未來",
            ]
        )[1]
    )
    assert len(set(short)) == 1 and len(set(longer)) == 1, "a row must read even"
    assert longer[0] > short[0], "a wrapping action must grow its card"


def test_closing_short_actions_keep_the_compact_card():
    heights = _card_heights(_closing_root(["確認範圍", "指定負責人", "每週檢視"])[1])
    assert heights[0] == pytest.approx(3.35, abs=0.01), (
        f"short actions should keep today's compact card: {heights[0]}cm"
    )


def test_timeline_cards_stay_equal_height():
    _, page = _timeline_root()
    heights = {card.get(_q("svg", "height")) for card in _visual_cards(page)}
    assert len(heights) == 1, f"a timeline row must read even: {heights}"


def test_process_cards_are_sized_to_their_content():
    _, page = _process_root()
    heights = _card_heights(page)
    assert len(set(heights)) == 1, "process cards must stay equal height"
    assert heights[0] < 6.5, f"a two-line step should not sprawl: {heights[0]}cm"


# ===========================================================================
# Diagram connectors must stay visible.
#
# Hub edges were drawn centre-to-centre and left it to the node cards to mask
# the overshoot. The label then sat on the midpoint inside a fixed 2.8cm chip —
# wider than the 1.9cm gap between two cards, so the chip covered every pixel of
# line that was not already under a card. The rendered page showed three boxes
# and three floating words with no connectors at all.
# ===========================================================================

_HUB = {
    "kind": "hub",
    "nodes": [
        {"id": "core", "title": "核心 API", "emphasis": True},
        {"id": "web", "title": "前端介面"},
        {"id": "data", "title": "資料層"},
        {"id": "ops", "title": "監控告警"},
    ],
    "edges": [
        {"source": "core", "target": "web", "label": "提供"},
        {"source": "core", "target": "data", "label": "讀寫"},
        {"source": "core", "target": "ops", "label": "回報"},
    ],
}


def _hub_page():
    p = Presentation(
        title="t",
        slides=[Slide(layout="diagram", title="服務架構", diagram=_HUB)],
    )
    return _content_root(p, THEMES["academic"]).find(".//draw:page", NS)


def _lines(page):
    out = []
    for line in page.findall(".//draw:line", NS):
        out.append(
            tuple(
                float(line.get(_q("svg", k)).removesuffix("cm"))
                for k in ("x1", "y1", "x2", "y2")
            )
        )
    return out


def _node_rects(page):
    """The four node cards: filled rects big enough to hold a node title."""
    return [
        (
            float(r.get(_q("svg", "x")).removesuffix("cm")),
            float(r.get(_q("svg", "y")).removesuffix("cm")),
            float(r.get(_q("svg", "width")).removesuffix("cm")),
            float(r.get(_q("svg", "height")).removesuffix("cm")),
        )
        for r in page.findall(".//draw:rect", NS)
        if float(r.get(_q("svg", "width")).removesuffix("cm")) >= 4.0
        and float(r.get(_q("svg", "height")).removesuffix("cm")) >= 2.0
    ]


def _inside_any(px, py, rects, tol=0.02):
    return any(
        x - tol <= px <= x + w + tol and y - tol <= py <= y + h + tol
        for x, y, w, h in rects
    )


def test_hub_edges_are_trimmed_to_the_node_boundaries():
    page = _hub_page()
    rects = _node_rects(page)
    assert len(rects) == 4
    for x1, y1, x2, y2 in _lines(page):
        # A trimmed edge touches the card's edge; an untrimmed one runs to the
        # centre, which is deep inside the card.
        for px, py in ((x1, y1), (x2, y2)):
            deep = any(
                x + 0.2 < px < x + w - 0.2 and y + 0.2 < py < y + h - 0.2
                for x, y, w, h in rects
            )
            assert not deep, f"edge endpoint ({px}, {py}) buried inside a node"


def test_every_hub_edge_keeps_a_visible_run_of_line():
    page = _hub_page()
    rects = _node_rects(page)
    # Label chips: small filled rects that are not node cards.
    chips = [
        (
            float(r.get(_q("svg", "x")).removesuffix("cm")),
            float(r.get(_q("svg", "y")).removesuffix("cm")),
            float(r.get(_q("svg", "width")).removesuffix("cm")),
            float(r.get(_q("svg", "height")).removesuffix("cm")),
        )
        for r in page.findall(".//draw:rect", NS)
        if float(r.get(_q("svg", "height")).removesuffix("cm")) < 1.0
        and float(r.get(_q("svg", "width")).removesuffix("cm")) >= 0.5
    ]
    for x1, y1, x2, y2 in _lines(page):
        samples = 200
        visible = 0
        for i in range(samples + 1):
            t = i / samples
            px, py = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
            if _inside_any(px, py, rects) or _inside_any(px, py, chips):
                continue
            visible += 1
        length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        shown = length * visible / (samples + 1)
        assert shown >= 0.5, (
            f"edge ({x1:.2f},{y1:.2f})->({x2:.2f},{y2:.2f}) only {shown:.2f}cm "
            "of line is actually visible"
        )


# ===========================================================================
# IR 上限長度的內容不得把字印出卡片外。
#
# 系統性缺陷:卡高被 max_h/available 夾住,但卡內文字框仍照「未夾住」的量測高度
# 與偏移擺放 —— IR 上限長度的 CJK 內容就把字印出卡外、印過鄰卡(ODF z-order =
# 文件順序,後畫的卡矩形直接蓋掉前面的字)、甚至印出頁面底邊。修法是共用的
# _fit_stacked_texts:先降 title 字級(到 12pt)、再降 detail 字級(到 10pt)、
# 最後截斷加「…」——LibreOffice 不裁字,看得見的刪節號勝過印在卡外的墨水。
# ===========================================================================


def _card_bound_escapes(page, tol=0.05):
    """文字框左上角落在哪張卡,框的底邊就必須留在那張卡內。

    LibreOffice 不把文字裁在框內,但 _fit_stacked_texts 保證框足以容納量測後
    的文字(墨水對框的檢查交給 _overflowing_text);因此「框留在卡內」加上
    「墨水留在框內」合起來就是「墨水留在卡內」。回傳違規清單。
    """
    cards = [
        (
            float(c.get(_q("svg", "x")).removesuffix("cm")),
            float(c.get(_q("svg", "y")).removesuffix("cm")),
            float(c.get(_q("svg", "width")).removesuffix("cm")),
            float(c.get(_q("svg", "height")).removesuffix("cm")),
        )
        for c in _visual_cards(page)
    ]
    escapes = []
    for x, y, w, h, text in _page_text_frames(page):
        for cx, cy, cw, ch in cards:
            if cx - tol <= x <= cx + cw and cy - tol <= y <= cy + ch:
                if y + h > cy + ch + tol:
                    escapes.append((text[:12], round(y + h, 2), round(cy + ch, 2)))
                break
    return escapes


# process:5 步 × 24 字標題 + 56 字說明(皆為 IR 上限)。舊版卡高被 7.0cm 的
# max_h 夾住,detail 卻照未夾住的偏移擺 —— 說明文字一路印過頁面底邊,而
# process-area 在 budget gate 回傳 None,沒有任何防線。
def test_process_ir_max_content_stays_on_card_and_page():
    theme = THEMES["academic"]
    p = Presentation(
        title="t",
        slides=[
            Slide(
                layout="process",
                title="流程",
                steps=[
                    ProcessStep(title="步" * 24, detail="說" * 56) for _ in range(5)
                ],
            )
        ],
    )
    root = _content_root(p, theme)
    page = root.find(".//draw:page", NS)
    spills = _overflowing_text(root, page)
    assert spills == [], f"text spilling out of its box: {spills}"
    escapes = _card_bound_escapes(page)
    assert escapes == [], f"text frame escaping its card: {escapes}"
    for x, y, w, h, text in _page_text_frames(page):
        assert y + h <= PAGE_H + 0.05, f"「{text[:12]}」printed past the page bottom"


# timeline:上排卡的 detail_offset 由未夾住的 title_h 推出,IR 上限內容讓上排
# 說明文字剛好落在下排卡上,再被後畫的下排卡矩形蓋掉。框留在卡內即不可能相撞
# (上排卡底 = 軸線-莖長,恆在下排卡頂之上)。
def test_timeline_ir_max_content_does_not_land_on_the_bottom_row():
    theme = THEMES["academic"]
    p = Presentation(
        title="t",
        slides=[
            Slide(
                layout="timeline",
                title="歷程",
                events=[
                    TimelineEvent(label=f"第{i}期", title="事" * 24, detail="詳" * 48)
                    for i in range(1, 6)
                ],
            )
        ],
    )
    root = _content_root(p, theme)
    page = root.find(".//draw:page", NS)
    spills = _overflowing_text(root, page)
    assert spills == [], f"text spilling out of its box: {spills}"
    escapes = _card_bound_escapes(page)
    assert escapes == [], f"top-row text landing on the bottom row: {escapes}"


# cards 2×2:約 60 字的標題把 child_y_offset 推到被夾住的卡外,子項落到第二排
# 卡的位置,還被後畫的第二排卡蓋掉。
def test_cards_grid_long_title_keeps_children_on_their_own_card():
    theme = THEMES["academic"]
    p = Presentation(
        title="t",
        slides=[
            Slide(
                layout="cards",
                title="標題",
                bullets=[
                    BulletItem(text="想" * 60, children=["子項一", "子項二"]),
                    BulletItem(text="流程順暢", children=["自動化"]),
                    BulletItem(text="資料可信", children=["治理"]),
                    BulletItem(text="持續改善", children=["回饋"]),
                ],
            )
        ],
    )
    root = _content_root(p, theme)
    page = root.find(".//draw:page", NS)
    spills = _overflowing_text(root, page)
    assert spills == [], f"text spilling out of its box: {spills}"
    escapes = _card_bound_escapes(page)
    assert escapes == [], f"children landing on the second row: {escapes}"


# closing:children 字串沒有 IR 長度上限,合併後的說明在 12pt 下限仍外溢到反白
# 背景;極端時 detail_offset 超過 card_h,整個說明框掉到卡片下方。舊 while 迴圈
# 只縮 title,detail 量一次就定案。
def test_closing_long_children_details_stay_on_their_card():
    root, page = _closing_root(
        [
            BulletItem(text="行動" * 8, children=["承諾" * 20, "檢核" * 20]),
            BulletItem(text="第二項行動", children=["說明" * 25]),
            BulletItem(text="第三項行動", children=["補充" * 25]),
        ]
    )
    spills = _overflowing_text(root, page)
    assert spills == [], f"detail spilling onto the inverted page: {spills}"
    escapes = _card_bound_escapes(page)
    assert escapes == [], f"detail frame dropping below its card: {escapes}"


# closing:動作數沒有 IR 上限,count ≥ 20 時 text_w 變成負數(svg:width="-0.03cm"
# 是非法幾何),count ≥ 10 起卡片早已不堪用。上限之外是大綱階段該擋的內容錯誤,
# 但 renderer 無論如何不得輸出負寬。
def test_closing_many_actions_never_emit_negative_geometry():
    _, page = _closing_root([f"行動項目{i:02d}" for i in range(1, 21)])
    for element in page.iter():
        for attr in ("width", "height"):
            value = element.get(_q("svg", attr))
            if value is not None and value.endswith("cm"):
                assert float(value.removesuffix("cm")) > 0, (
                    f"negative geometry: svg:{attr}={value}"
                )
    # 只畫前五張;多出來的是內容錯誤,不是幾何錯誤。
    assert len(_visual_cards(page)) == 5


# metrics:label/value/detail 原用固定 y 偏移(0.75/2.65/3.65)假設各佔一行;
# 14+ 字的 label(IR 上限 24)換行就印進 detail,且 fact_font_size_pt 被餵
# 未扣除 2×0.25 內縮的寬度,量出「一行」的字級實際上會換行。
def test_metrics_ir_max_label_never_prints_into_the_detail():
    theme = THEMES["academic"]
    p = Presentation(
        title="t",
        slides=[
            Slide(
                layout="metrics",
                title="指標",
                metrics=[
                    {"value": "值" * 18, "label": "標" * 24, "detail": "說" * 56}
                    for _ in range(4)
                ],
            )
        ],
    )
    root = _content_root(p, theme)
    page = root.find(".//draw:page", NS)
    spills = _overflowing_text(root, page)
    assert spills == [], f"label/value/detail spilling: {spills}"
    escapes = _card_bound_escapes(page)
    assert escapes == [], f"metric text escaping its card: {escapes}"
    # 同一張卡上 label 框與 detail 框不得相交(偏移由量測高度推出)。
    frames = _page_text_frames(page)

    def _inside(card, fx, fy):
        cx, cy, cw, ch = card
        return cx - 0.05 <= fx <= cx + cw and cy - 0.05 <= fy <= cy + ch

    cards = [
        (
            float(c.get(_q("svg", "x")).removesuffix("cm")),
            float(c.get(_q("svg", "y")).removesuffix("cm")),
            float(c.get(_q("svg", "width")).removesuffix("cm")),
            float(c.get(_q("svg", "height")).removesuffix("cm")),
        )
        for c in _visual_cards(page)
    ]
    for card in cards:
        labels = [(y, h) for x, y, w, h, t in frames if t.startswith("標") and _inside(card, x, y)]
        details = [(y, h) for x, y, w, h, t in frames if t.startswith("說") and _inside(card, x, y)]
        assert len(labels) == 1 and len(details) == 1
        (ly, lh), (dy, _dh) = labels[0], details[0]
        assert ly + lh <= dy + 0.01, (
            f"label bottom {ly + lh:.2f} crosses detail top {dy:.2f}"
        )


# diagram:節點標題原用原始 estimate + 錯的換行寬(w-0.7 而非 _wrap_width(w-0.7)),
# LibreOffice 實際多折一行,第四行印進下方 detail;節點盒在 12pt 下限仍裝不下時
# 應截斷加「…」而非印穿。
def test_diagram_ir_max_node_title_meets_detail_cleanly():
    theme = THEMES["academic"]
    p = Presentation(
        title="t",
        slides=[
            Slide(
                layout="diagram",
                title="架構",
                diagram={
                    "kind": "hub",
                    "nodes": [
                        {"id": f"n{i}", "title": "節" * 24, "detail": "述" * 48}
                        for i in range(4)
                    ],
                    "edges": [
                        {"source": "n0", "target": f"n{i}"} for i in range(1, 4)
                    ],
                },
            )
        ],
    )
    root = _content_root(p, theme)
    page = root.find(".//draw:page", NS)
    spills = _overflowing_text(root, page)
    assert spills == [], f"node text spilling: {spills}"
    escapes = _card_bound_escapes(page)
    assert escapes == [], f"node text escaping its box: {escapes}"
    # 標題塞不下即截斷 —— 頁面上要看得到刪節號,而非印穿 detail 的墨水。
    assert "…" in "".join(page.itertext())


def test_timeline_long_label_truncates_to_one_line():
    # 標籤框只有一行高,標題緊貼其下:會換行的標籤直接印進標題。日期/季度
    # 這類標籤截斷無損語意,換行才是災難。
    from odforge.ir import TimelineEvent

    long_label = "二〇二四年首季至次季之間的過渡期間"  # 18 字,IR 上限的合法輸入
    p = Presentation(
        title="t",
        slides=[
            Slide(
                layout="timeline",
                title="演進",
                events=[
                    TimelineEvent(label=long_label, title="起點", detail="d"),
                    TimelineEvent(label="Q2", title="中點", detail="d"),
                ],
            )
        ],
    )
    root = _content_root(p, THEMES["academic"])
    texts = [t for t in root.itertext()]
    assert not any(long_label in t for t in texts), "full label must not render"
    assert any("…" in t for t in texts), "truncated label keeps a visible ellipsis"


def test_cards_budget_warns_only_when_truncation_is_inevitable():
    # 自救優先、回饋殿後:字級還降得下去就不該警告;降到底仍要截斷才警告,
    # 讓 retry loop 換來更短的內容而不是成品裡的省略號。
    from odforge.textmetrics import check_budget

    fits = Slide(layout="cards", title="重點", bullets=["短句一", "短句二"])
    assert check_budget(fits, THEMES["academic"]) == []

    huge = "這是一段極長的卡片標題文字" * 12  # ~150+ 字,2×2 卡在字級地板也放不下
    overflow = Slide(
        layout="cards",
        title="重點",
        bullets=[huge, huge, huge, huge],
    )
    messages = check_budget(overflow, THEMES["academic"])
    assert messages, "floor-exhausted cards must warn"
    assert any("截斷" in m for m in messages)


def test_chart_long_labels_shrink_to_one_line_and_never_cross_rows():
    # 溝槽每列只有一行的位置(8 根條時列距 ~1.4cm),標籤沒有 IR 長度上限:
    # 換行的第二行會直接印在鄰列的字上。整欄取同一個縮小後字級,地板仍放不下
    # 的截成單行 — 欄位讀起來才像同一欄。
    from odforge.ir import ChartSpec

    long_label = "行政院國家科學及技術委員會補助專題"
    p = Presentation(
        title="t",
        slides=[
            Slide(
                layout="chart",
                title="比較",
                chart=ChartSpec(
                    labels=[long_label] + [f"項目{i}" for i in range(7)],
                    values=[80.0, 10, 20, 30, 40, 50, 60, 70],
                    unit="百萬新臺幣",
                ),
            )
        ],
    )
    root = _content_root(p, THEMES["academic"])
    page = root.find(".//draw:page", NS)
    spills = _overflowing_text(root, page)
    assert spills == [], f"chart gutter text crossing rows: {spills}"


@pytest.mark.skipif(find_soffice() is None, reason="LibreOffice not installed")
def test_a_blank_bulleted_page_cannot_reach_libreoffice(tmp_path):
    """R2-02, end to end: the deck LibreOffice happily opened and nobody could read.

    Before the strip-based invariants this rendered, passed all three format
    gates, and opened cleanly — a heading with nothing under it, certified.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Presentation(
            title="空白頁",
            slides=[Slide(layout="title-content", title="標題", bullets=["   "])],
        )


# ---------------------------------------------------------------------------
# Cover branding — 封面署名與校徽 (ir.Branding)
#
# The byline and the logo are app-owned furniture: the user typed and uploaded
# them, so unlike page content they must appear verbatim, on exactly the pages
# the user chose, and their absence must never be able to fail a render.
# ---------------------------------------------------------------------------

_LOGO_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000154a24f3b0000000049454e44ae426082"
)


def _branded_deck(**branding):
    return Presentation(
        title="測試簡報",
        slides=[
            Slide(layout="title", title="封面", subtitle="副標"),
            Slide(layout="title-content", title="內頁", bullets=["甲", "乙"]),
            Slide(layout="section", title="章節"),
            Slide(layout="closing", title="結語", bullets=["下一步"]),
        ],
        branding=branding,
    )


def _logo_frames(root):
    return [
        image
        for image in root.findall(".//draw:image", NS)
        if (image.get(_q("xlink", "href")) or "").startswith("Pictures/logo-")
    ]


def _logo_asset(width: int = 240, height: int = 80) -> AssetBlob:
    """The uploaded bytes, as ``render_odp`` receives them."""
    return AssetBlob(_LOGO_PNG, "image/png", ".png", width, height)


def _logo_part(width: int = 240, height: int = 80) -> _LogoAsset:
    """The packaged part, as ``build_content_xml`` receives it."""
    return _LogoAsset("Pictures/logo-test.png", width, height)


def test_byline_is_drawn_on_the_cover_verbatim():
    deck = _branded_deck(byline="輔仁大學資訊工程學系 · 王小明 · 2026/08")
    root = _content_root(deck, THEMES["academic"])
    pages = root.findall(".//draw:page", NS)
    assert _text_p_with(pages[0], "輔仁大學資訊工程學系 · 王小明 · 2026/08") is not None
    # Only the cover carries it — a署名 repeated on every page is a watermark.
    for page in pages[1:]:
        assert not [x for x in page.findall(".//text:p", NS) if x.text and "王小明" in x.text]


def test_no_branding_renders_exactly_as_before():
    plain = _branded_deck()
    plain = Presentation(title=plain.title, slides=plain.slides)
    with_empty = Presentation(
        title=plain.title, slides=plain.slides, branding={"byline": "", "logo": ""}
    )
    assert build_content_xml(with_empty, THEMES["academic"]) == build_content_xml(
        plain, THEMES["academic"]
    )


@pytest.mark.parametrize(
    "placement,expected",
    [("cover", 1), ("cover-closing", 2), ("all", 4)],
)
def test_logo_placement_decides_which_pages_carry_the_mark(
    tmp_path, placement, expected
):
    deck = _branded_deck(logo="asset://logo", placement=placement)
    out = render_odp(deck, tmp_path / "p.odp", assets={"logo": _logo_asset()})
    with zipfile.ZipFile(out) as z:
        root = etree.fromstring(z.read("content.xml"))
        parts = [n for n in z.namelist() if n.startswith("Pictures/logo-")]
    assert len(_logo_frames(root)) == expected
    # One packaged copy no matter how many pages reference it.
    assert len(parts) == 1


def test_logo_keeps_its_aspect_ratio():
    deck = _branded_deck(logo="asset://logo", placement="cover")
    xml = build_content_xml(deck, THEMES["academic"], None, _logo_part(240, 80))
    root = etree.fromstring(xml.encode("utf-8"))
    frame = _logo_frames(root)[0].getparent()
    width = float(frame.get(_q("svg", "width")).removesuffix("cm"))
    height = float(frame.get(_q("svg", "height")).removesuffix("cm"))
    assert width / height == pytest.approx(3.0, rel=0.01)
    # …and it stays inside the box the cover reserves for it.
    assert width <= 5.0 and height <= 1.6


def test_a_tall_logo_is_bounded_by_height_not_width():
    deck = _branded_deck(logo="asset://logo", placement="cover")
    xml = build_content_xml(deck, THEMES["academic"], None, _logo_part(80, 240))
    root = etree.fromstring(xml.encode("utf-8"))
    frame = _logo_frames(root)[0].getparent()
    height = float(frame.get(_q("svg", "height")).removesuffix("cm"))
    assert height <= 1.6


def test_inverted_pages_get_a_plate_behind_the_mark():
    # section/closing paint the accent edge to edge; a dark crest on that ground
    # would simply vanish, so it sits on a bg-coloured plate.
    theme = THEMES["academic"]
    deck = _branded_deck(logo="asset://logo", placement="all")
    root = etree.fromstring(
        build_content_xml(deck, theme, None, _logo_part()).encode("utf-8")
    )
    pages = root.findall(".//draw:page", NS)
    styles = {
        style.get(_q("style", "name")): style
        for style in root.findall(".//style:style", NS)
    }

    def has_bg_plate(page):
        for rect in page.findall(".//draw:rect", NS):
            style = styles.get(rect.get(_q("draw", "style-name")))
            props = (
                style.find(".//style:graphic-properties", NS)
                if style is not None
                else None
            )
            if props is not None and props.get(_q("draw", "fill-color")) == theme.bg:
                return True
        return False

    assert has_bg_plate(pages[2])  # section
    assert has_bg_plate(pages[3])  # closing
    assert not has_bg_plate(pages[1])  # ordinary content page needs no plate


def test_an_unresolvable_logo_never_fails_the_render(tmp_path):
    deck = _branded_deck(byline="系上公版", logo="asset://logo")
    out = render_odp(deck, tmp_path / "p.odp", assets={})  # upload went missing
    with zipfile.ZipFile(out) as z:
        assert not [n for n in z.namelist() if n.startswith("Pictures/logo-")]
        assert "系上公版" in z.read("content.xml").decode("utf-8")


def test_a_logo_reference_must_be_an_asset():
    # A model-authored deck naming https:// here would turn every render into an
    # outbound fetch. Branding is app-owned; only asset:// is accepted.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _branded_deck(logo="https://example.org/crest.png")
