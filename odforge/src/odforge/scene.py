"""An ``.odp`` as a scene the browser can draw and edit (網頁編輯器的資料來源).

The web editor does not show pictures of slides; it draws each slide from the
document itself — every rectangle, line, image and text frame at its own place,
with its own fill, stroke, font and paragraph settings — so that a person can
grab a frame and move it, or click into text and type. This module reads an
``.odp`` package and returns that description as plain JSON-able data.

Element ids are positions: ``"<page>/<index>"``, the index counting the page's
drawable children in document (paint) order. They are stable across edits that
only change geometry or text, and the editor re-reads the scene after anything
structural (add, delete, reorder), so positions are all an id needs to be.

Anything this module cannot faithfully draw (custom shapes, tables, groups,
rotated objects, embedded objects) still appears, as an ``unsupported`` box at
its declared place: the editor shows it, can move it, and never touches its
content.
"""

from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from lxml import etree

from .xmlsafe import safe_fromstring

NS = {
    "office": "urn:oasis:names:tc:opendocument:xmlns:office:1.0",
    "style": "urn:oasis:names:tc:opendocument:xmlns:style:1.0",
    "text": "urn:oasis:names:tc:opendocument:xmlns:text:1.0",
    "draw": "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0",
    "fo": "urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0",
    "svg": "urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0",
    "xlink": "http://www.w3.org/1999/xlink",
    "presentation": "urn:oasis:names:tc:opendocument:xmlns:presentation:1.0",
    "loext": "urn:org:documentfoundation:names:experimental:office:xmlns:loext:1.0",
    # Impress stores slide comments as officeooo:annotation inside draw:page.
    "officeooo": "http://openoffice.org/2009/office",
    "dc": "http://purl.org/dc/elements/1.1/",
    # Which element a comment is about (by draw:name); ODF has no word for it.
    "odforge": "urn:odforge:editor:1",
}


def q(prefix: str, local: str) -> str:
    """Clark-notation name: ``q("draw", "frame")`` → ``{urn…drawing…}frame``."""
    return f"{{{NS[prefix]}}}{local}"


PT_TO_CM = 2.54 / 72

# Drawable children of a page, by what the editor can do with them.
_SHAPES = {
    q("draw", "rect"): "rect",
    q("draw", "ellipse"): "ellipse",
    q("draw", "circle"): "ellipse",
}
_LINE = q("draw", "line")
_FRAME = q("draw", "frame")
_NOTES = q("presentation", "notes")
_FORMS = q("office", "forms")
ANNOTATION = q("officeooo", "annotation")


def cm(value: Optional[str], default: float = 0.0) -> float:
    """An ODF length in centimetres (``"6.8cm"``, ``"12pt"``, ``"3mm"``, ``"1in"``)."""
    if not value:
        return default
    match = re.match(r"^\s*(-?[0-9.]+)\s*([a-z%]*)\s*$", value)
    if not match:
        return default
    number, unit = float(match.group(1)), match.group(2)
    return {
        "cm": number,
        "": number,
        "mm": number / 10,
        "in": number * 2.54,
        "pt": number * PT_TO_CM,
        "pc": number * 12 * PT_TO_CM,
        "px": number * 2.54 / 96,
    }.get(unit, default)


def pt(value: Optional[str]) -> Optional[float]:
    """A font size in points; ``None`` for relative (``%``) or missing sizes."""
    if not value:
        return None
    match = re.match(r"^\s*([0-9.]+)\s*(pt|cm|mm|in|px)\s*$", value)
    if not match:
        return None
    if match.group(2) == "pt":
        return float(match.group(1))
    return cm(value) / PT_TO_CM


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------


class Styles:
    """Every named style in the package, resolved through ``parent-style-name``.

    Lookups merge a style's property sets with its ancestors' and the family's
    default style, nearest first — what LibreOffice does when it lays a page out.
    """

    _KINDS = (
        "graphic-properties",
        "paragraph-properties",
        "text-properties",
        "drawing-page-properties",
        "list-level-properties",
    )

    def __init__(self, roots: Iterable[etree._Element]) -> None:
        self.styles: Dict[Tuple[str, str], etree._Element] = {}
        self.defaults: Dict[str, etree._Element] = {}
        self.list_styles: Dict[str, etree._Element] = {}
        self.fonts: Dict[str, str] = {}
        for root in roots:
            for el in root.iter(q("style", "style")):
                name = el.get(q("style", "name"))
                family = el.get(q("style", "family"), "")
                if name:
                    self.styles.setdefault((family, name), el)
            for el in root.iter(q("style", "default-style")):
                self.defaults.setdefault(el.get(q("style", "family"), ""), el)
            for el in root.iter(q("text", "list-style")):
                name = el.get(q("style", "name"))
                if name:
                    self.list_styles.setdefault(name, el)
            for el in root.iter(q("style", "font-face")):
                name = el.get(q("style", "name"))
                family = el.get(q("svg", "font-family"), name or "")
                if name:
                    self.fonts[name] = family.strip("'\"")

    def _chain(self, family: str, name: Optional[str]) -> List[etree._Element]:
        chain: List[etree._Element] = []
        seen = set()
        while name and (family, name) not in seen:
            seen.add((family, name))
            el = self.styles.get((family, name))
            if el is None:
                break
            chain.append(el)
            name = el.get(q("style", "parent-style-name"))
        if family in self.defaults:
            chain.append(self.defaults[family])
        return chain

    def props(self, family: str, name: Optional[str], kind: str) -> Dict[str, str]:
        """Merged ``{"ns:attr": value}`` of one property set, nearest style wins."""
        merged: Dict[str, str] = {}
        for el in reversed(self._chain(family, name)):
            for props in el.findall(q("style", kind)):
                for key, value in props.attrib.items():
                    merged[_short(key)] = value
        return merged


def _short(clark: str) -> str:
    """``{urn…fo…}color`` → ``fo:color``."""
    uri, _, local = clark[1:].partition("}")
    for prefix, value in NS.items():
        if value == uri:
            return f"{prefix}:{local}"
    return local


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------


def _text_style(props: Dict[str, str], fonts: Dict[str, str]) -> Dict[str, Any]:
    """CSS-ready text settings from merged ``text-properties``."""
    out: Dict[str, Any] = {}
    size = pt(props.get("fo:font-size")) or pt(props.get("style:font-size-asian"))
    if size:
        out["size"] = size
    if "fo:color" in props:
        out["color"] = props["fo:color"]
    weight = props.get("fo:font-weight") or props.get("style:font-weight-asian")
    if weight:
        out["weight"] = 700 if weight == "bold" else 400 if weight == "normal" else int(weight)
    if props.get("fo:font-style") == "italic":
        out["italic"] = True
    families = []
    for key in ("style:font-name", "style:font-name-asian"):
        name = props.get(key)
        if name:
            families.append(fonts.get(name, name))
    if not families and props.get("fo:font-family"):
        families.append(props["fo:font-family"].strip("'\""))
    if families:
        out["family"] = list(dict.fromkeys(families))
    spacing = props.get("fo:letter-spacing")
    if spacing and spacing != "normal":
        out["letterSpacing"] = cm(spacing)
    if props.get("style:text-underline-style", "none") != "none":
        out["underline"] = True
    return out


def _paragraph_style(props: Dict[str, str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    align = props.get("fo:text-align")
    if align:
        out["align"] = {"start": "left", "end": "right"}.get(align, align)
    height = props.get("fo:line-height")
    if height and height != "normal":
        if height.endswith("%"):
            out["lineHeight"] = float(height[:-1]) / 100
        else:
            out["lineHeightCm"] = cm(height)
    for key, name in (
        ("fo:margin-top", "marginTop"),
        ("fo:margin-bottom", "marginBottom"),
        ("fo:margin-left", "marginLeft"),
        ("fo:text-indent", "indent"),
    ):
        if key in props:
            out[name] = cm(props[key])
    return out


@dataclass
class _Run:
    text: str
    style: Dict[str, Any] = field(default_factory=dict)


def _runs(
    paragraph: etree._Element, styles: Styles, base: Dict[str, str], page_no: int
) -> List[_Run]:
    """The paragraph's text as styled runs; line breaks become ``"\\n"``."""
    runs: List[_Run] = []

    def add(text: str, props: Dict[str, str]) -> None:
        if not text:
            return
        style = _text_style(props, styles.fonts)
        if runs and runs[-1].style == style:
            runs[-1].text += text
        else:
            runs.append(_Run(text, style))

    def walk(el: etree._Element, props: Dict[str, str]) -> None:
        add(el.text or "", props)
        for child in el:
            tag = child.tag
            if tag == q("text", "span"):
                merged = {
                    **props,
                    **styles.props("text", child.get(q("text", "style-name")), "text-properties"),
                }
                walk(child, merged)
            elif tag == q("text", "s"):
                add(" " * int(child.get(q("text", "c"), "1")), props)
            elif tag == q("text", "tab"):
                add("\t", props)
            elif tag == q("text", "line-break"):
                add("\n", props)
            elif tag == q("text", "page-number"):
                add(str(page_no), props)
            else:
                walk(child, props)
            add(child.tail or "", props)

    walk(paragraph, base)
    return runs


def _bullet(
    list_style: Optional[etree._Element], level: int, styles: Styles
) -> Optional[Dict[str, Any]]:
    if list_style is None:
        return {"char": "•", "indent": 0.6 * level, "width": 0.6}
    for child in list_style:
        if child.get(q("text", "level")) != str(level):
            continue
        props: Dict[str, str] = {}
        for p in child.findall(q("style", "list-level-properties")):
            props.update({_short(k): v for k, v in p.attrib.items()})
        before = cm(props.get("text:space-before"))
        width = cm(props.get("text:min-label-width"), 0.6)
        if child.tag == q("text", "list-level-style-bullet"):
            return {
                "char": child.get(q("text", "bullet-char"), "•"),
                "indent": before,
                "width": width,
            }
        if child.tag == q("text", "list-level-style-number"):
            return {
                "char": "#",
                "number": True,
                "suffix": child.get(q("style", "num-suffix"), "."),
                "indent": before,
                "width": width,
            }
        return None
    return {"char": "•", "indent": 0.6 * level, "width": 0.6}


def text_paragraphs(box: etree._Element, styles: Styles, page_no: int) -> List[Dict[str, Any]]:
    """Every paragraph of a text box in document order, lists included."""
    out: List[Dict[str, Any]] = []

    def visit(
        el: etree._Element, list_style: Optional[etree._Element], level: int, counters: List[int]
    ) -> None:
        for child in el:
            if child.tag in (q("text", "p"), q("text", "h")):
                name = child.get(q("text", "style-name"))
                para = _paragraph_style(styles.props("paragraph", name, "paragraph-properties"))
                base = styles.props("paragraph", name, "text-properties")
                runs = _runs(child, styles, base, page_no)
                entry: Dict[str, Any] = {
                    **para,
                    "runs": [{"text": r.text, **r.style} for r in runs],
                    "style": _text_style(base, styles.fonts),
                }
                if level:
                    bullet = _bullet(list_style, level, styles)
                    if bullet is not None:
                        if bullet.get("number"):
                            counters[level - 1] += 1
                            bullet = {**bullet, "char": f"{counters[level - 1]}{bullet['suffix']}"}
                        entry["bullet"] = bullet
                out.append(entry)
            elif child.tag == q("text", "list"):
                name = child.get(q("text", "style-name"))
                nested = styles.list_styles.get(name) if name else list_style
                visit(
                    child, nested, level + 1, [*counters, 0] if len(counters) <= level else counters
                )
            elif child.tag in (q("text", "list-item"), q("text", "list-header")):
                visit(child, list_style, level, counters)

    visit(box, None, 0, [])
    return out


def plain_text(paragraph: Dict[str, Any]) -> str:
    return "".join(run["text"] for run in paragraph["runs"])


# ---------------------------------------------------------------------------
# Shapes
# ---------------------------------------------------------------------------


def _box(el: etree._Element) -> Dict[str, float]:
    return {
        "x": cm(el.get(q("svg", "x"))),
        "y": cm(el.get(q("svg", "y"))),
        "w": cm(el.get(q("svg", "width"))),
        "h": cm(el.get(q("svg", "height"))),
    }


def _paint(props: Dict[str, str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    fill = props.get("draw:fill", "solid" if "draw:fill-color" in props else "none")
    if fill == "solid":
        out["fill"] = props.get("draw:fill-color", "#729fcf")
        opacity = props.get("draw:opacity")
        if opacity and opacity.endswith("%"):
            out["fillOpacity"] = float(opacity[:-1]) / 100
    elif fill == "gradient":
        out["fill"] = props.get("draw:fill-color", "#cccccc")
    stroke = props.get("draw:stroke", "solid" if "svg:stroke-color" in props else "none")
    if stroke != "none":
        out["stroke"] = props.get("svg:stroke-color", "#3465a4")
        out["strokeWidth"] = cm(props.get("svg:stroke-width"), 0.0)
        if stroke == "dash":
            out["dashed"] = True
    return out


# LibreOffice's built-in text-area insets, used when no style in the chain sets
# padding: a quarter centimetre left and right, an eighth top and bottom.
_DEFAULT_PADDING = {"top": 0.125, "right": 0.25, "bottom": 0.125, "left": 0.25}


def _text_frame_settings(props: Dict[str, str]) -> Dict[str, Any]:
    pad = props.get("fo:padding")
    out: Dict[str, Any] = {
        "padding": {
            side: cm(props.get(f"fo:padding-{side}", pad), _DEFAULT_PADDING[side])
            if props.get(f"fo:padding-{side}", pad) is not None
            else _DEFAULT_PADDING[side]
            for side in ("top", "right", "bottom", "left")
        },
        "valign": props.get("draw:textarea-vertical-align", "top"),
    }
    if props.get("draw:auto-grow-height") == "false":
        out["clip"] = True
    return out


def element(el: etree._Element, styles: Styles, page_no: int, element_id: str) -> Dict[str, Any]:
    """One drawable element of a page as JSON-able data."""
    style_name = el.get(q("draw", "style-name")) or el.get(q("presentation", "style-name"))
    family = (
        "presentation"
        if el.get(q("presentation", "style-name")) and not el.get(q("draw", "style-name"))
        else "graphic"
    )
    graphic = styles.props(family, style_name, "graphic-properties")
    out: Dict[str, Any] = {"id": element_id}
    name = el.get(q("draw", "name"))
    if name:
        out["name"] = name
    if el.get(q("draw", "transform")):
        out.update(kind="unsupported", reason="旋轉或變形的物件", **_box(el))
        return out

    if el.tag in _SHAPES:
        out.update(kind=_SHAPES[el.tag], **_box(el), **_paint(graphic))
        radius = el.get(q("draw", "corner-radius"))
        if radius:
            out["radius"] = cm(radius)
        paragraphs = text_paragraphs(el, styles, page_no)
        if paragraphs:
            out["text"] = {"paragraphs": paragraphs, **_text_frame_settings(graphic)}
            out["text"]["valign"] = graphic.get("draw:textarea-vertical-align", "middle")
        return out

    if el.tag == _LINE:
        out.update(
            kind="line",
            x1=cm(el.get(q("svg", "x1"))),
            y1=cm(el.get(q("svg", "y1"))),
            x2=cm(el.get(q("svg", "x2"))),
            y2=cm(el.get(q("svg", "y2"))),
            **_paint({**graphic, "draw:fill": "none"}),
        )
        return out

    if el.tag == _FRAME:
        out.update(_box(el))
        box = el.find(q("draw", "text-box"))
        image = el.find(q("draw", "image"))
        if box is not None:
            out["kind"] = "text"
            out["text"] = {
                "paragraphs": text_paragraphs(box, styles, page_no),
                **_text_frame_settings(graphic),
            }
            out.update(_paint(graphic))
            placeholder = el.get(q("presentation", "class"))
            if placeholder:
                out["placeholder"] = placeholder
            return out
        if image is not None:
            href = image.get(q("xlink", "href"), "")
            if href and not href.startswith(("http:", "https:", "/")) and ".." not in href:
                out.update(kind="image", href=href.lstrip("./"))
                return out
        out.update(kind="unsupported", reason="表格、圖表或內嵌物件")
        return out

    out.update(kind="unsupported", reason="自訂圖形或群組", **_box(el))
    return out


def drawables(page: etree._Element) -> List[etree._Element]:
    """The page's drawable children in paint order (speaker notes and forms excluded)."""
    return [
        child
        for child in page
        if isinstance(child.tag, str) and child.tag not in (_NOTES, _FORMS, ANNOTATION)
    ]


def annotations(page: etree._Element) -> List[etree._Element]:
    return [child for child in page if child.tag == ANNOTATION]


def comments_of(page: etree._Element, index: int) -> List[Dict[str, Any]]:
    """The page's comments, each tied to the element it names when that still exists."""
    names = {
        el.get(q("draw", "name")): f"{index}/{j}"
        for j, el in enumerate(drawables(page))
        if el.get(q("draw", "name"))
    }
    out = []
    for k, note in enumerate(annotations(page)):
        text = "\n".join("".join(p.itertext()) for p in note.iter(q("text", "p")))
        creator = note.find(q("dc", "creator"))
        date = note.find(q("dc", "date"))
        out.append(
            {
                "id": f"c{index}/{k}",
                "text": text,
                "author": (creator.text or "") if creator is not None else "",
                "date": (date.text or "") if date is not None else "",
                "x": cm(note.get(q("svg", "x"))),
                "y": cm(note.get(q("svg", "y"))),
                "target": names.get(note.get(q("odforge", "target"))),
                # draft: still being written; submitted: handed to the AI to act on.
                "state": note.get(q("odforge", "state"), "draft"),
            }
        )
    return out


# ---------------------------------------------------------------------------
# The scene
# ---------------------------------------------------------------------------


def _page_settings(page: etree._Element, styles: Styles) -> Dict[str, Any]:
    props = styles.props(
        "drawing-page", page.get(q("draw", "style-name")), "drawing-page-properties"
    )
    out: Dict[str, Any] = {}
    if props.get("draw:fill", "solid" if "draw:fill-color" in props else "none") != "none":
        out["background"] = props.get("draw:fill-color", "#ffffff")
    if "presentation:display-page-number" in props:
        out["showPageNumber"] = props["presentation:display-page-number"] == "true"
    return out


def build_scene(content: etree._Element, styles_root: etree._Element) -> Dict[str, Any]:
    """The whole presentation as the editor needs it."""
    styles = Styles((content, styles_root))

    width, height = 28.0, 15.75
    layouts = {el.get(q("style", "name")): el for el in styles_root.iter(q("style", "page-layout"))}
    masters: Dict[str, etree._Element] = {
        el.get(q("style", "name")): el for el in styles_root.iter(q("style", "master-page"))
    }
    first_master = next(iter(masters.values()), None)
    if first_master is not None:
        layout = layouts.get(first_master.get(q("style", "page-layout-name")))
        if layout is not None:
            props = layout.find(q("style", "page-layout-properties"))
            if props is not None:
                width = cm(props.get(q("fo", "page-width")), width)
                height = cm(props.get(q("fo", "page-height")), height)

    pages: List[Dict[str, Any]] = []
    for index, page in enumerate(content.iter(q("draw", "page"))):
        page_no = index + 1
        master_name = page.get(q("draw", "master-page-name"))
        master = masters.get(master_name) if master_name else first_master
        settings = _page_settings(page, styles)
        if "background" not in settings and master is not None:
            master_settings = _page_settings(master, styles)
            settings.setdefault("background", master_settings.get("background"))
            settings.setdefault("showPageNumber", master_settings.get("showPageNumber", False))
        background_elements: List[Dict[str, Any]] = []
        if master is not None:
            for j, el in enumerate(drawables(master)):
                if el.get(q("presentation", "class")) == "page-number" and not settings.get(
                    "showPageNumber"
                ):
                    continue
                if (
                    el.get(q("presentation", "class"))
                    and el.get(q("presentation", "class")) != "page-number"
                ):
                    # Title/outline placeholders on a master are prompts, not content.
                    continue
                background_elements.append(element(el, styles, page_no, f"m{index}/{j}"))
        pages.append(
            {
                "id": str(index),
                "name": page.get(q("draw", "name"), f"page{page_no}"),
                "background": settings.get("background") or "#ffffff",
                "master": background_elements,
                "elements": [
                    element(el, styles, page_no, f"{index}/{j}")
                    for j, el in enumerate(drawables(page))
                ],
                "comments": comments_of(page, index),
            }
        )

    fonts = []
    seen_hrefs = set()
    faces = [*content.iter(q("style", "font-face")), *styles_root.iter(q("style", "font-face"))]
    for face in faces:
        family = face.get(q("svg", "font-family"), face.get(q("style", "name"), "")).strip("'\"")
        for uri in face.iter(q("svg", "font-face-uri")):
            href = uri.get(q("xlink", "href"), "")
            if href.startswith("Fonts/") and ".." not in href and href not in seen_hrefs:
                seen_hrefs.add(href)
                fonts.append(
                    {
                        "family": family,
                        "href": href,
                        "weight": 700 if uri.get(q("loext", "font-weight")) == "bold" else 400,
                        "italic": uri.get(q("loext", "font-style")) == "italic",
                    }
                )
    return {"width": width, "height": height, "pages": pages, "fonts": fonts}


def read_package(path: Path) -> Tuple[etree._Element, etree._Element]:
    with zipfile.ZipFile(path) as package:
        content = safe_fromstring(package.read("content.xml"))
        try:
            styles = safe_fromstring(package.read("styles.xml"))
        except KeyError:
            styles = etree.Element(q("office", "document-styles"))
    return content, styles


def scene_of(path: Path) -> Dict[str, Any]:
    """Convenience: the scene of an ``.odp`` on disk."""
    content, styles = read_package(Path(path))
    return build_scene(content, styles)
