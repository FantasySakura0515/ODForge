import re

from odforge.themes import LAYOUTS, THEMES, PAGE_W, PAGE_H, Frame, Theme


def test_all_five_layouts_exist():
    assert set(LAYOUTS) == {"title", "title-content", "two-col", "section", "big-fact"}


def test_frames_within_page_bounds():
    for name, frames in LAYOUTS.items():
        for f in frames:
            assert f.x >= 0 and f.y >= 0, (name, f.role)
            assert f.x + f.w <= PAGE_W, (name, f.role)
            assert f.y + f.h <= PAGE_H, (name, f.role)


def test_all_three_themes_exist():
    assert set(THEMES) == {"academic", "minimal", "dark"}


def test_theme_colors_are_valid_hex():
    hexre = re.compile(r"^#[0-9A-Fa-f]{6}$")
    for t in THEMES.values():
        for c in (t.bg, t.title_color, t.text_color, t.accent):
            assert hexre.match(c), c


def test_frames_are_frozen():
    import pytest, dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        LAYOUTS["title"][0].x = 0


def test_layout_roles_match_expectation():
    assert [f.role for f in LAYOUTS["two-col"]] == ["title", "left", "right"]
    assert [f.role for f in LAYOUTS["big-fact"]] == ["fact", "bullets"]


# ---------------------------------------------------------------------------
# Task 4.2: .odp renderer tests
# ---------------------------------------------------------------------------

import zipfile
import lxml.etree as etree
import pytest
from odforge.render.odp import (
    render_odp,
    build_content_xml,
    build_styles_xml,
    _rect_xml,
    _line_xml,
    _GraphicStyles,
)
from odforge.render import render
from odforge.ir import Presentation, Slide
from odforge.package import ODP_MIMETYPE

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

from odforge.themes import SCALES, resolve_design
from odforge.ir import contrast_ratio, DesignSpec, Palette, FontPair


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
    return etree.fromstring(f"<root {decls}>{fragment}</root>".encode("utf-8"))


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
    _ParagraphStyles,
    _list_xml,
    _list_style_xml,
    _kicker_paragraph_xml,
    _LIST_STYLE_NAME,
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
    assert props.get(_q("draw", "fill-color")) == theme.accent
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
    assert theme.accent in fills


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

from odforge.render.odp import _svg_decoration, _DECO_HREF  # noqa: E402


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
