"""Edit an ``.odp`` in place: the operations behind the web editor.

The editor sends small operations — move this frame, replace that text, add a
page — and this module applies them to the document's ``content.xml`` and
writes the package back to disk. Only what an operation names is touched;
everything else in the file (styles, fonts, pictures, speaker notes, elements
the editor cannot draw) passes through byte for byte.

Undo keeps whole copies of ``content.xml`` rather than inverse operations: a
slide deck's content is a few hundred kilobytes, and a snapshot can never be
subtly wrong the way a hand-derived inverse can.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import threading
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from . import docfonts
from .scene import (
    ANNOTATION,
    NS,
    Styles,
    _paragraph_style,
    _text_style,
    annotations,
    build_scene,
    cm,
    drawables,
    q,
)
from .xmlsafe import safe_fromstring

MAX_HISTORY = 200
MAX_IMAGE_BYTES = 15 * 1024 * 1024

_TEXT_STYLE = "OEdP1"
_FRAME_STYLE = "OEdG1"
_RECT_STYLE = "OEdG2"


class EditError(ValueError):
    """An operation that cannot be applied (shown to the user as is)."""


def _len(value: float) -> str:
    return f"{round(value, 4):g}cm"


class Document:
    """One ``.odp`` open for editing. Thread-safe; every method saves to disk."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self.version = 0
        self._load()

    def _stamp(self) -> Tuple[int, int]:
        stat = self.path.stat()
        return stat.st_mtime_ns, stat.st_size

    def _load(self) -> None:
        """(Re)read the package from disk; history starts over."""
        self._members: List[Tuple[zipfile.ZipInfo, bytes]] = []
        self._disk = self._stamp()
        with zipfile.ZipFile(self.path) as package:
            for info in package.infolist():
                self._members.append((info, package.read(info.filename)))
        names = [info.filename for info, _ in self._members]
        if "content.xml" not in names:
            raise EditError("這不是可以編輯的 ODF 簡報(缺少 content.xml)")
        self.content = safe_fromstring(self._member("content.xml"))
        styles = self._member("styles.xml")
        self.styles = (
            safe_fromstring(styles)
            if styles is not None
            else etree.Element(q("office", "document-styles"))
        )
        if self._presentation() is None:
            raise EditError("這份檔案不是簡報(.odp)")
        self.undo_stack: List[bytes] = []
        self.redo_stack: List[bytes] = []

    def _sync(self) -> None:
        """Pick up changes someone else (an agent, another tool) wrote to the file.

        Undo history belongs to the version it was recorded against, so it is
        dropped: undoing back across another writer's edit would silently revert
        their work.
        """
        try:
            stamp = self._stamp()
        except OSError:
            return
        if stamp == self._disk:
            return
        try:
            self._load()
        except (OSError, zipfile.BadZipFile, etree.XMLSyntaxError, EditError):
            # Caught mid-write: keep what we have and try again next time.
            return
        self.version += 1

    def poll(self) -> int:
        """The current version, after picking up outside changes (cheap when none)."""
        with self._lock:
            self._sync()
            return self.version

    # -- package ----------------------------------------------------------------

    def _member(self, name: str) -> Optional[bytes]:
        for info, data in self._members:
            if info.filename == name:
                return data
        return None

    def member(self, name: str) -> Optional[bytes]:
        """A file inside the package (fonts and pictures for the browser)."""
        if ".." in name or name.startswith("/"):
            return None
        with self._lock:
            return self._member(name)

    def _serialise(self) -> bytes:
        return etree.tostring(self.content, xml_declaration=True, encoding="UTF-8")

    def _save(self) -> None:
        content = self._serialise()
        self._members = [
            (info, content if info.filename == "content.xml" else data)
            for info, data in self._members
        ]
        fd, tmp = tempfile.mkstemp(prefix=".odforge-edit-", suffix=".odp", dir=self.path.parent)
        os.close(fd)
        try:
            with zipfile.ZipFile(tmp, "w") as out:
                for info, data in self._members:
                    # mimetype must stay first and stored; every other member keeps
                    # the compression it came with.
                    out.writestr(info, data, compress_type=info.compress_type)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        self._disk = self._stamp()
        self.version += 1

    # -- reading ----------------------------------------------------------------

    def _presentation(self) -> Optional[etree._Element]:
        return self.content.find(f".//{q('office', 'presentation')}")

    def _pages(self) -> List[etree._Element]:
        presentation = self._presentation()
        return [] if presentation is None else presentation.findall(q("draw", "page"))

    def state(self) -> Dict[str, Any]:
        with self._lock:
            self._sync()
            return self._state()

    def _state(self, created: Optional[List[str]] = None) -> Dict[str, Any]:
        scene = build_scene(self.content, self.styles)
        for font in scene["fonts"]:
            data = self._member(font["href"])
            # A re-subset font keeps its path; the revision makes browsers refetch it.
            font["rev"] = hashlib.sha1(data).hexdigest()[:10] if data else ""
        presentation = self._presentation()
        return {
            "name": self.path.name,
            "version": self.version,
            "scene": scene,
            "session": presentation.get(q("odforge", "session"), "open"),
            "theme": _theme_summary(self.content, self.styles),
            "fontChoices": docfonts.choices([self.content, self.styles]),
            "canUndo": bool(self.undo_stack),
            "canRedo": bool(self.redo_stack),
            "created": created or [],
        }

    def _page(self, index: Any) -> etree._Element:
        pages = self._pages()
        try:
            i = int(index)
        except (TypeError, ValueError) as exc:
            raise EditError(f"頁碼不正確:{index!r}") from exc
        if not 0 <= i < len(pages):
            raise EditError(f"沒有第 {i + 1} 頁")
        return pages[i]

    def _element(self, element_id: Any) -> Tuple[int, int, etree._Element]:
        """``"7/2"`` is the third drawable of page 8; ``"7/2.1"`` the second inside that group."""
        try:
            page_part, path = str(element_id).split("/")
            page_i = int(page_part)
            indices = [int(part) for part in path.split(".")]
        except ValueError as exc:
            raise EditError(f"元素代號不正確:{element_id!r}") from exc
        container = self._page(page_i)
        el = container
        for depth, index in enumerate(indices):
            if depth and el.tag != q("draw", "g"):
                raise EditError(f"找不到元素 {element_id}")
            items = drawables(el)
            if not 0 <= index < len(items):
                raise EditError(f"找不到元素 {element_id}")
            el = items[index]
        return page_i, indices[0], el

    # -- history ----------------------------------------------------------------

    def apply(self, ops: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Apply a batch atomically: all of it lands, or none of it does."""
        with self._lock:
            self._sync()
            before = self._serialise()
            created: List[str] = []
            try:
                for op in ops:
                    created.extend(self._apply_one(op))
            except EditError:
                self.content = safe_fromstring(before)
                raise
            except Exception as exc:
                self.content = safe_fromstring(before)
                raise EditError(f"無法套用這個修改:{type(exc).__name__}: {exc}") from exc
            if self._serialise() == before:
                return self._state(created)
            self._refresh_fonts()
            self.undo_stack.append(before)
            del self.undo_stack[:-MAX_HISTORY]
            self.redo_stack.clear()
            self._save()
            return self._state(created)

    def undo(self) -> Dict[str, Any]:
        with self._lock:
            self._sync()
            if self.undo_stack:
                self.redo_stack.append(self._serialise())
                self.content = safe_fromstring(self.undo_stack.pop())
                self._save()
            return self._state()

    def redo(self) -> Dict[str, Any]:
        with self._lock:
            self._sync()
            if self.redo_stack:
                self.undo_stack.append(self._serialise())
                self.content = safe_fromstring(self.redo_stack.pop())
                self._save()
            return self._state()

    # -- operations -------------------------------------------------------------

    def _apply_one(self, op: Dict[str, Any]) -> List[str]:
        kind = op.get("op")
        handler = getattr(self, f"_op_{kind}", None) if isinstance(kind, str) else None
        if handler is None:
            raise EditError(f"不認得的操作:{kind!r}")
        return handler(op) or []

    def _op_geom(self, op: Dict[str, Any]) -> None:
        _, _, el = self._element(op.get("id"))
        if el.tag == q("draw", "g"):
            # A group has no box of its own: move and scale what is inside it.
            ox, oy, ow, oh = _group_box(el)
            nx, ny = float(op.get("x", ox)), float(op.get("y", oy))
            nw, nh = float(op.get("w", ow)), float(op.get("h", oh))
            if nw < 0.05 or nh < 0.05:
                raise EditError("寬度和高度至少要 0.05 cm")
            _transform(el, ox, oy, nx, ny, nw / ow if ow else 1.0, nh / oh if oh else 1.0)
            return
        if el.tag == q("draw", "line"):
            for key in ("x1", "y1", "x2", "y2"):
                if key in op:
                    el.set(q("svg", key), _len(float(op[key])))
            return
        for key, attr in (("x", "x"), ("y", "y"), ("w", "width"), ("h", "height")):
            if key in op:
                value = float(op[key])
                if key in ("w", "h") and value < 0.05:
                    raise EditError("寬度和高度至少要 0.05 cm")
                el.set(q("svg", attr), _len(value))

    def _text_root(self, el: etree._Element) -> etree._Element:
        if el.tag == q("draw", "frame"):
            box = el.find(q("draw", "text-box"))
            if box is None:
                raise EditError("這個元素不是文字框")
            return box
        return el

    def _op_text(self, op: Dict[str, Any]) -> None:
        _, _, el = self._element(op.get("id"))
        paragraphs = op.get("paragraphs")
        if not isinstance(paragraphs, list) or not all(
            isinstance(p, str) or (isinstance(p, dict) and isinstance(p.get("runs", []), list))
            for p in paragraphs
        ):
            raise EditError("文字內容格式不正確")
        styles = Styles((self.content, self.styles))
        root = self._text_root(el)
        existing = list(root.iter(q("text", "p"), q("text", "h")))
        if not paragraphs:
            paragraphs = [""]
        if not existing:
            template = etree.SubElement(root, q("text", "p"))
            style = self._ensure_text_style()
            template.set(q("text", "style-name"), style)
            existing = [template]

        def write(paragraph: etree._Element, value: Any) -> None:
            if isinstance(value, str):
                _set_paragraph_text(paragraph, value)
            else:
                self._set_paragraph_rich(paragraph, value, styles)

        for i, text in enumerate(paragraphs):
            if i < len(existing):
                if not isinstance(text, str) or _paragraph_text(existing[i]) != text:
                    write(existing[i], text)
            else:
                last = existing[-1]
                holder = last.getparent()
                if holder is not None and holder.tag == q("text", "list-item"):
                    clone = copy.deepcopy(holder)
                    holder.addnext(clone)
                    paragraph = clone.find(q("text", "p"))
                else:
                    paragraph = copy.deepcopy(last)
                    last.addnext(paragraph)
                write(paragraph, text)
                existing.append(paragraph)
        for paragraph in existing[len(paragraphs) :]:
            holder = paragraph.getparent()
            if holder is not None and holder.tag == q("text", "list-item") and len(holder) == 1:
                holder.getparent().remove(holder)
            elif holder is not None:
                holder.remove(paragraph)

    # -- fonts -------------------------------------------------------------------

    def _font_decls(self) -> etree._Element:
        decls = self.content.find(q("office", "font-face-decls"))
        if decls is None:
            decls = etree.Element(q("office", "font-face-decls"))
            anchor = self.content.find(q("office", "automatic-styles"))
            if anchor is None:
                anchor = self.content.find(q("office", "body"))
            anchor.addprevious(decls)
        return decls

    def _ensure_font(self, family: str) -> str:
        """The font-face name for ``family``, declaring it when the document has none."""
        for face in docfonts.font_faces([self.content, self.styles]):
            if docfonts._family_of(face) == family:
                return face.get(q("style", "name"))
        face = etree.SubElement(self._font_decls(), q("style", "font-face"))
        face.set(q("style", "name"), family)
        face.set(q("svg", "font-family"), f"'{family}'")
        face.set(q("style", "font-pitch"), "variable")
        return family

    def _refresh_fonts(self) -> None:
        """Re-embed fonts whose subsets lack characters the text now uses."""
        roots = [self.content, self.styles]
        text = docfonts.document_text(roots)
        needed = docfonts.needed_chars(text)
        for family in docfonts.referenced_families(roots):
            face = next(
                (
                    f
                    for f in docfonts.font_faces([self.content])
                    if docfonts._family_of(f) == family
                ),
                None,
            )
            if face is None:
                continue
            parts = [
                uri.get(q("xlink", "href"), "") for uri in face.iter(q("svg", "font-face-uri"))
            ]
            stale = not parts
            for part in parts:
                data = self._member(part)
                covered = docfonts.coverage(data) if data else None
                if covered is None or not needed <= covered:
                    stale = True
            if not stale:
                continue
            fonts = docfonts.rebuild(family, text)
            if not fonts:
                continue
            for font in fonts:
                self._put_member(font.part, font.data, "application/x-font-otf")
            for child in list(face):
                face.remove(child)
            src = etree.SubElement(face, q("svg", "font-face-src"))
            for font in fonts:
                uri = etree.SubElement(src, q("svg", "font-face-uri"))
                uri.set(q("xlink", "href"), font.part)
                uri.set(q("xlink", "type"), "simple")
                uri.set(q("loext", "font-style"), "normal")
                uri.set(q("loext", "font-weight"), "bold" if font.bold else "normal")
                fmt = etree.SubElement(uri, q("svg", "font-face-format"))
                fmt.set(q("svg", "string"), font.fmt)

    def _put_member(self, path: str, data: bytes, media: str) -> None:
        for i, (info, _) in enumerate(self._members):
            if info.filename == path:
                self._members[i] = (info, data)
                return
        info = zipfile.ZipInfo(path)
        info.compress_type = zipfile.ZIP_DEFLATED
        self._members.append((info, data))
        self._add_manifest_entry(path, media)

    # -- rich text ---------------------------------------------------------------

    def _set_paragraph_rich(
        self, paragraph: etree._Element, value: Dict[str, Any], styles: Styles
    ) -> None:
        """Rewrite a paragraph from styled runs; only differences from its style become spans."""
        name = paragraph.get(q("text", "style-name"))
        base = _text_style(styles.props("paragraph", name, "text-properties"), styles.fonts)
        for child in list(paragraph):
            paragraph.remove(child)
        paragraph.text = None
        for run in value.get("runs", []):
            if not isinstance(run, dict):
                raise EditError("文字內容格式不正確")
            text = str(run.get("text", ""))
            if not text:
                continue
            diff = _run_diff(run, base)
            if diff:
                span = etree.SubElement(paragraph, q("text", "span"))
                span.set(q("text", "style-name"), self._span_style(diff))
                _write_lines(span, text)
            else:
                _append_lines(paragraph, text)
        align = value.get("align")
        if align:
            current = _paragraph_style(styles.props("paragraph", name, "paragraph-properties"))
            if current.get("align", "left") != align:
                paragraph.set(q("text", "style-name"), self._aligned_style(name, align))

    def _span_style(self, diff: Dict[str, Any]) -> str:
        digest = hashlib.sha1(json.dumps(diff, sort_keys=True).encode()).hexdigest()[:8]
        name = f"OEdT{digest}"
        if not self._has_style(name):
            style = etree.SubElement(self._automatic_styles(), q("style", "style"))
            style.set(q("style", "name"), name)
            style.set(q("style", "family"), "text")
            props = etree.SubElement(style, q("style", "text-properties"))
            if "weight" in diff:
                value = "bold" if diff["weight"] >= 600 else "normal"
                for attr in (
                    q("fo", "font-weight"),
                    q("style", "font-weight-asian"),
                    q("style", "font-weight-complex"),
                ):
                    props.set(attr, value)
            if "italic" in diff:
                value = "italic" if diff["italic"] else "normal"
                for attr in (
                    q("fo", "font-style"),
                    q("style", "font-style-asian"),
                    q("style", "font-style-complex"),
                ):
                    props.set(attr, value)
            if "size" in diff:
                value = f"{float(diff['size']):g}pt"
                for attr in (
                    q("fo", "font-size"),
                    q("style", "font-size-asian"),
                    q("style", "font-size-complex"),
                ):
                    props.set(attr, value)
            if "color" in diff:
                props.set(q("fo", "color"), str(diff["color"]))
            if "family" in diff:
                face = self._ensure_font(str(diff["family"]))
                for attr in ("font-name", "font-name-asian", "font-name-complex"):
                    props.set(q("style", attr), face)
            if "underline" in diff:
                props.set(
                    q("style", "text-underline-style"), "solid" if diff["underline"] else "none"
                )
                if diff["underline"]:
                    props.set(q("style", "text-underline-width"), "auto")
                    props.set(q("style", "text-underline-color"), "font-color")
        return name

    def _aligned_style(self, name: Optional[str], align: str) -> str:
        if align not in ("left", "center", "right", "justify"):
            raise EditError(f"不認得的對齊方式:{align!r}")
        odf_align = {"left": "start", "right": "end"}.get(align, align)
        digest = hashlib.sha1(f"{name}|{align}".encode()).hexdigest()[:8]
        new_name = f"OEdA{digest}"
        if self._has_style(new_name):
            return new_name
        auto = self._automatic_styles()
        original = (
            next(
                (
                    el
                    for el in auto.iter(q("style", "style"))
                    if el.get(q("style", "name")) == name
                    and el.get(q("style", "family")) == "paragraph"
                ),
                None,
            )
            if name
            else None
        )
        if original is not None:
            style = copy.deepcopy(original)
            auto.append(style)
        else:
            style = etree.SubElement(auto, q("style", "style"))
            style.set(q("style", "family"), "paragraph")
            if name:
                style.set(q("style", "parent-style-name"), name)
        style.set(q("style", "name"), new_name)
        props = style.find(q("style", "paragraph-properties"))
        if props is None:
            props = etree.Element(q("style", "paragraph-properties"))
            style.insert(0, props)
        props.set(q("fo", "text-align"), odf_align)
        return new_name

    # -- ready-made parts (元素) -------------------------------------------------

    def _op_add_component(self, op: Dict[str, Any]) -> List[str]:
        from .components import KINDS, build, infer_theme

        kind = op.get("kind")
        if kind not in KINDS:
            raise EditError(f"不認得的元素:{kind!r}")
        page_i = int(op.get("page", 0))
        page = self._page(page_i)
        theme = infer_theme(self.content, self.styles)
        specs = build(kind, float(op.get("x", 2)), float(op.get("y", 2)), theme)
        elements = [self._from_spec(spec) for spec in specs]
        if len(elements) == 1:
            node = elements[0]
        else:
            node = etree.Element(q("draw", "g"))
            for el in elements:
                node.append(el)
        self._insert_on_top(page, node)
        return [f"{page_i}/{drawables(page).index(node)}"]

    def _from_spec(self, spec: Dict[str, Any]) -> etree._Element:
        tag = spec["tag"]
        if tag == "line":
            el = etree.Element(q("draw", "line"))
            el.set(q("draw", "style-name"), self._graphic_style(stroke=spec.get("stroke")))
            for key in ("x1", "y1", "x2", "y2"):
                el.set(q("svg", key), _len(spec[key]))
            return el
        if tag == "text":
            el = etree.Element(q("draw", "frame"))
            el.set(q("draw", "style-name"), self._graphic_style(valign=spec.get("valign", "top")))
            box = etree.SubElement(el, q("draw", "text-box"))
            self._styled_paragraph(box, spec)
        else:
            el = etree.Element(q("draw", "rect" if tag == "rect" else "ellipse"))
            style = spec.get("style") or self._graphic_style(
                fill=spec.get("fill"), stroke=spec.get("stroke")
            )
            el.set(q("draw", "style-name"), style)
            if spec.get("radius") and tag == "rect":
                el.set(q("draw", "corner-radius"), _len(spec["radius"]))
            if spec.get("label"):
                self._styled_paragraph(el, spec["label"])
        for key, attr in (("x", "x"), ("y", "y"), ("w", "width"), ("h", "height")):
            el.set(q("svg", attr), _len(spec[key]))
        return el

    def _styled_paragraph(self, parent: etree._Element, spec: Dict[str, Any]) -> None:
        paragraph = etree.SubElement(parent, q("text", "p"))
        paragraph.set(q("text", "style-name"), self._paragraph_style(spec))
        _append_lines(paragraph, spec["text"])

    def _paragraph_style(self, spec: Dict[str, Any]) -> str:
        key = {
            k: spec.get(k)
            for k in ("size", "color", "weight", "font", "align", "italic", "spacing")
        }
        name = "OEdP" + hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:8]
        if self._has_style(name):
            return name
        style = etree.SubElement(self._automatic_styles(), q("style", "style"))
        style.set(q("style", "name"), name)
        style.set(q("style", "family"), "paragraph")
        para = etree.SubElement(style, q("style", "paragraph-properties"))
        para.set(
            q("fo", "text-align"), {"left": "start", "right": "end"}.get(key["align"], key["align"])
        )
        text = etree.SubElement(style, q("style", "text-properties"))
        for attr in (
            q("fo", "font-size"),
            q("style", "font-size-asian"),
            q("style", "font-size-complex"),
        ):
            text.set(attr, f"{float(key['size']):g}pt")
        text.set(q("fo", "color"), key["color"])
        if key["weight"] and key["weight"] >= 600:
            for attr in (
                q("fo", "font-weight"),
                q("style", "font-weight-asian"),
                q("style", "font-weight-complex"),
            ):
                text.set(attr, "bold")
        if key["italic"]:
            text.set(q("fo", "font-style"), "italic")
        if key["spacing"]:
            text.set(q("fo", "letter-spacing"), _len(key["spacing"]))
        if key["font"]:
            text.set(q("style", "font-name"), key["font"])
            text.set(q("style", "font-name-asian"), key["font"])
        return name

    def _graphic_style(
        self, fill: Optional[str] = None, stroke: Optional[str] = None, valign: Optional[str] = None
    ) -> str:
        key = json.dumps([fill, stroke, valign])
        name = "OEdG" + hashlib.sha1(key.encode()).hexdigest()[:8]
        if self._has_style(name):
            return name
        style = etree.SubElement(self._automatic_styles(), q("style", "style"))
        style.set(q("style", "name"), name)
        style.set(q("style", "family"), "graphic")
        props = etree.SubElement(style, q("style", "graphic-properties"))
        props.set(q("draw", "fill"), "solid" if fill else "none")
        if fill:
            props.set(q("draw", "fill-color"), fill)
        props.set(q("draw", "stroke"), "solid" if stroke else "none")
        if stroke:
            props.set(q("svg", "stroke-color"), stroke)
            props.set(q("svg", "stroke-width"), "0.75pt")
        if valign:
            props.set(q("draw", "textarea-vertical-align"), valign)
            props.set(q("draw", "auto-grow-height"), "true")
            for side in ("top", "right", "bottom", "left"):
                props.set(q("fo", f"padding-{side}"), "0cm")
        return name

    def _op_group(self, op: Dict[str, Any]) -> List[str]:
        """Wrap top-level elements of one page into a ``draw:g``, where the topmost was."""
        ids = op.get("ids")
        if not isinstance(ids, list) or len(ids) < 2:
            raise EditError("至少要選兩個物件才能組成群組")
        found = [self._element(i) for i in ids]
        pages = {page_i for page_i, _, _ in found}
        if len(pages) != 1 or any("." in str(i) for i in ids):
            raise EditError("只能把同一頁、不在其他群組裡的物件組成群組")
        page = self._page(pages.pop())
        members = sorted((index, el) for _, index, el in found)
        group = etree.Element(q("draw", "g"))
        members[-1][1].addnext(group)
        for _, el in members:
            group.append(el)
        page_i = self._pages().index(page)
        return [f"{page_i}/{drawables(page).index(group)}"]

    def _op_ungroup(self, op: Dict[str, Any]) -> List[str]:
        page_i, _, group = self._element(op.get("id"))
        if group.tag != q("draw", "g") or "." in str(op.get("id")):
            raise EditError("這不是可以解散的群組")
        parent = group.getparent()
        children = list(drawables(group))
        for child in children:
            group.addprevious(child)
        parent.remove(group)
        page = self._page(page_i)
        items = drawables(page)
        return [f"{page_i}/{items.index(child)}" for child in children]

    def _op_delete(self, op: Dict[str, Any]) -> None:
        _, _, el = self._element(op.get("id"))
        parent = el.getparent()
        parent.remove(el)
        # A group emptied by the delete goes too; it would be an invisible box.
        while parent.tag == q("draw", "g") and not drawables(parent):
            grand = parent.getparent()
            grand.remove(parent)
            parent = grand

    def _op_duplicate(self, op: Dict[str, Any]) -> List[str]:
        page_i, _, el = self._element(op.get("id"))
        clone = copy.deepcopy(el)
        dx, dy = float(op.get("dx", 0.5)), float(op.get("dy", 0.5))
        _offset(clone, dx, dy)
        clone.attrib.pop(q("draw", "name"), None)
        clone.attrib.pop(q("presentation", "class"), None)
        page = self._page(page_i)
        self._insert_on_top(page, clone)
        return [f"{page_i}/{drawables(page).index(clone)}"]

    def _insert_on_top(self, page: etree._Element, el: etree._Element) -> None:
        # Above every drawable, below comments and speaker notes (which are not drawn).
        anchor = next((c for c in page if c.tag in (ANNOTATION, q("presentation", "notes"))), None)
        if anchor is not None:
            anchor.addprevious(el)
        else:
            page.append(el)

    def _op_order(self, op: Dict[str, Any]) -> List[str]:
        page_i, _, el = self._element(op.get("id"))
        page = self._page(page_i)
        to = op.get("to")
        items = drawables(page)
        if to == "front":
            page.remove(el)
            self._insert_on_top(page, el)
        elif to == "back":
            page.remove(el)
            first = drawables(page)
            if first:
                first[0].addprevious(el)
            else:
                page.insert(0, el)
        elif to == "forward":
            i = items.index(el)
            if i + 1 < len(items):
                items[i + 1].addnext(el)
        elif to == "backward":
            i = items.index(el)
            if i > 0:
                items[i - 1].addprevious(el)
        else:
            raise EditError(f"不認得的圖層方向:{to!r}")
        return [f"{page_i}/{drawables(page).index(el)}"]

    def _op_add_text(self, op: Dict[str, Any]) -> List[str]:
        page_i = int(op.get("page", 0))
        page = self._page(page_i)
        frame = etree.Element(q("draw", "frame"))
        frame.set(q("draw", "style-name"), self._ensure_frame_style())
        for key, attr, default in (
            ("x", "x", 2),
            ("y", "y", 2),
            ("w", "width", 10),
            ("h", "height", 1.5),
        ):
            frame.set(q("svg", attr), _len(float(op.get(key, default))))
        box = etree.SubElement(frame, q("draw", "text-box"))
        paragraph = etree.SubElement(box, q("text", "p"))
        paragraph.set(q("text", "style-name"), self._ensure_text_style())
        paragraph.text = str(op.get("text", ""))
        self._insert_on_top(page, frame)
        return [f"{page_i}/{drawables(page).index(frame)}"]

    # -- comments ----------------------------------------------------------------

    def _op_comment_add(self, op: Dict[str, Any]) -> List[str]:
        """A comment on an element (``id``) or on a spot of a page (``page``, ``x``, ``y``)."""
        import datetime
        import uuid

        text = str(op.get("text", "")).strip()
        if not text:
            raise EditError("留言不能是空的")
        target_name = None
        if op.get("id"):
            page_i, _, el = self._element(op["id"])
            target_name = el.get(q("draw", "name"))
            if not target_name:
                target_name = f"odforge-{uuid.uuid4().hex[:8]}"
                el.set(q("draw", "name"), target_name)
            if el.tag == q("draw", "line"):
                x = max(cm(el.get(q("svg", "x1"))), cm(el.get(q("svg", "x2"))))
                y = min(cm(el.get(q("svg", "y1"))), cm(el.get(q("svg", "y2"))))
            else:
                x = cm(el.get(q("svg", "x"))) + cm(el.get(q("svg", "width")))
                y = cm(el.get(q("svg", "y")))
        else:
            page_i = int(op.get("page", 0))
            x, y = float(op.get("x", 1)), float(op.get("y", 1))
        page = self._page(page_i)
        note = etree.Element(ANNOTATION)
        note.set(q("svg", "x"), _len(x))
        note.set(q("svg", "y"), _len(y))
        if target_name:
            note.set(q("odforge", "target"), target_name)
        creator = etree.SubElement(note, q("dc", "creator"))
        creator.text = str(op.get("author") or "我")
        date = etree.SubElement(note, q("dc", "date"))
        date.text = datetime.datetime.now().replace(microsecond=0).isoformat()
        for line in text.split("\n"):
            etree.SubElement(note, q("text", "p")).text = line
        notes = page.find(q("presentation", "notes"))
        if notes is not None:
            notes.addprevious(note)
        else:
            page.append(note)
        return [f"c{page_i}/{annotations(page).index(note)}"]

    def _op_comments_submit(self, op: Dict[str, Any]) -> List[str]:
        """Hand every draft comment to the AI (``wait_for_feedback`` picks them up)."""
        submitted = []
        for page_i, page in enumerate(self._pages()):
            for k, note in enumerate(annotations(page)):
                if note.get(q("odforge", "state"), "draft") == "draft":
                    note.set(q("odforge", "state"), "submitted")
                    submitted.append(f"c{page_i}/{k}")
        if not submitted:
            raise EditError("沒有要交給 AI 的留言")
        return submitted

    def _op_session(self, op: Dict[str, Any]) -> None:
        """``{"op": "session", "state": "done" | "open"}``: end (or resume) the AI session."""
        presentation = self._presentation()
        state = op.get("state")
        if state == "done":
            presentation.set(q("odforge", "session"), "done")
        elif state == "open":
            presentation.attrib.pop(q("odforge", "session"), None)
        else:
            raise EditError(f"不認得的協作狀態:{state!r}")

    def _op_comment_delete(self, op: Dict[str, Any]) -> None:
        try:
            page_part, index_part = str(op.get("comment", "")).lstrip("c").split("/")
            page_i, k = int(page_part), int(index_part)
        except ValueError as exc:
            raise EditError(f"留言代號不正確:{op.get('comment')!r}") from exc
        notes = annotations(self._page(page_i))
        if not 0 <= k < len(notes):
            raise EditError(f"找不到留言 {op.get('comment')}")
        notes[k].getparent().remove(notes[k])

    # -- clipboard ---------------------------------------------------------------

    def clip(self, ids: List[str]) -> List[str]:
        """The XML of elements, for pasting later (on any page of this document)."""
        with self._lock:
            self._sync()
            return [
                etree.tostring(self._element(element_id)[2], encoding="unicode")
                for element_id in ids
            ]

    def _op_paste(self, op: Dict[str, Any]) -> List[str]:
        page_i = int(op.get("page", 0))
        page = self._page(page_i)
        items = op.get("items")
        if not isinstance(items, list) or not items:
            raise EditError("沒有可以貼上的內容")
        dx, dy = float(op.get("dx", 0)), float(op.get("dy", 0))
        created: List[str] = []
        for item in items:
            try:
                el = safe_fromstring(str(item).encode("utf-8"))
            except etree.XMLSyntaxError as exc:
                raise EditError("剪貼簿的內容無法貼上") from exc
            if etree.QName(el).namespace != NS["draw"] or etree.QName(el).localname == "page":
                raise EditError("剪貼簿的內容不是投影片上的物件")
            el.attrib.pop(q("presentation", "class"), None)
            el.attrib.pop(q("draw", "name"), None)
            _offset(el, dx, dy)
            self._insert_on_top(page, el)
            created.append(el)
        return [f"{page_i}/{drawables(page).index(el)}" for el in created]

    # -- pictures ------------------------------------------------------------------

    def _op_add_image(self, op: Dict[str, Any]) -> List[str]:
        import base64

        page_i = int(op.get("page", 0))
        page = self._page(page_i)
        try:
            data = base64.b64decode(str(op.get("data", "")), validate=True)
        except ValueError as exc:
            raise EditError("圖片資料不正確") from exc
        if len(data) > MAX_IMAGE_BYTES:
            raise EditError("圖片太大(上限 15 MB)")
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            ext, media = "png", "image/png"
        elif data.startswith(b"\xff\xd8\xff"):
            ext, media = "jpg", "image/jpeg"
        else:
            raise EditError("只支援 PNG 或 JPEG 圖片")
        href = f"Pictures/{hashlib.sha1(data).hexdigest()[:16]}.{ext}"
        if self._member(href) is None:
            info = zipfile.ZipInfo(href)
            info.compress_type = zipfile.ZIP_STORED
            self._members.append((info, data))
            self._add_manifest_entry(href, media)
        frame = etree.Element(q("draw", "frame"))
        frame.set(q("draw", "style-name"), self._ensure_frame_style())
        for key, attr, default in (
            ("x", "x", 2),
            ("y", "y", 2),
            ("w", "width", 8),
            ("h", "height", 6),
        ):
            frame.set(q("svg", attr), _len(float(op.get(key, default))))
        image = etree.SubElement(frame, q("draw", "image"))
        image.set(q("xlink", "href"), href)
        image.set(q("xlink", "type"), "simple")
        image.set(q("xlink", "show"), "embed")
        image.set(q("xlink", "actuate"), "onLoad")
        self._insert_on_top(page, frame)
        return [f"{page_i}/{drawables(page).index(frame)}"]

    def _add_manifest_entry(self, path: str, media: str) -> None:
        manifest_ns = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
        raw = self._member("META-INF/manifest.xml")
        if raw is None:
            return
        manifest = safe_fromstring(raw)
        entry = etree.SubElement(manifest, f"{{{manifest_ns}}}file-entry")
        entry.set(f"{{{manifest_ns}}}full-path", path)
        entry.set(f"{{{manifest_ns}}}media-type", media)
        data = etree.tostring(manifest, xml_declaration=True, encoding="UTF-8")
        self._members = [
            (info, data if info.filename == "META-INF/manifest.xml" else body)
            for info, body in self._members
        ]

    def _op_add_rect(self, op: Dict[str, Any]) -> List[str]:
        page_i = int(op.get("page", 0))
        page = self._page(page_i)
        rect = etree.Element(q("draw", "rect"))
        rect.set(q("draw", "style-name"), self._ensure_rect_style())
        for key, attr, default in (
            ("x", "x", 4),
            ("y", "y", 4),
            ("w", "width", 6),
            ("h", "height", 4),
        ):
            rect.set(q("svg", attr), _len(float(op.get(key, default))))
        self._insert_on_top(page, rect)
        return [f"{page_i}/{drawables(page).index(rect)}"]

    # -- pages --------------------------------------------------------------------

    def _unique_page_name(self) -> str:
        names = {p.get(q("draw", "name")) for p in self._pages()}
        n = len(names) + 1
        while f"page{n}" in names:
            n += 1
        return f"page{n}"

    def _op_page_add(self, op: Dict[str, Any]) -> List[str]:
        pages = self._pages()
        after = int(op.get("after", len(pages) - 1))
        anchor = self._page(after)
        page = etree.Element(q("draw", "page"))
        for attr in (
            q("draw", "style-name"),
            q("draw", "master-page-name"),
            q("presentation", "presentation-page-layout-name"),
        ):
            if anchor.get(attr):
                page.set(attr, anchor.get(attr))
        page.set(q("draw", "name"), self._unique_page_name())
        anchor.addnext(page)
        return [f"page:{after + 1}"]

    def _op_page_duplicate(self, op: Dict[str, Any]) -> List[str]:
        index = int(op.get("page", 0))
        source = self._page(index)
        clone = copy.deepcopy(source)
        clone.set(q("draw", "name"), self._unique_page_name())
        source.addnext(clone)
        return [f"page:{index + 1}"]

    def _op_page_delete(self, op: Dict[str, Any]) -> None:
        if len(self._pages()) <= 1:
            raise EditError("簡報至少要留一頁")
        page = self._page(op.get("page"))
        page.getparent().remove(page)

    def _op_page_move(self, op: Dict[str, Any]) -> List[str]:
        pages = self._pages()
        page = self._page(op.get("page"))
        to = int(op.get("to", 0))
        if not 0 <= to < len(pages):
            raise EditError(f"沒有第 {to + 1} 頁")
        others = [p for p in pages if p is not page]
        page.getparent().remove(page)
        if to < len(others):
            others[to].addprevious(page)
        else:
            others[-1].addnext(page)
        return [f"page:{to}"]

    # -- styles for new elements -------------------------------------------------

    def _automatic_styles(self) -> etree._Element:
        auto = self.content.find(q("office", "automatic-styles"))
        if auto is None:
            auto = etree.Element(q("office", "automatic-styles"))
            body = self.content.find(q("office", "body"))
            body.addprevious(auto)
        return auto

    def _has_style(self, name: str) -> bool:
        return any(
            el.get(q("style", "name")) == name
            for el in self._automatic_styles().iter(q("style", "style"))
        )

    def _ensure_text_style(self) -> str:
        if not self._has_style(_TEXT_STYLE):
            font = self._common_font()
            style = etree.SubElement(self._automatic_styles(), q("style", "style"))
            style.set(q("style", "name"), _TEXT_STYLE)
            style.set(q("style", "family"), "paragraph")
            text = etree.SubElement(style, q("style", "text-properties"))
            for attr in (
                q("fo", "font-size"),
                q("style", "font-size-asian"),
                q("style", "font-size-complex"),
            ):
                text.set(attr, "18pt")
            text.set(q("fo", "color"), "#1F2733")
            if font:
                text.set(q("style", "font-name"), font)
                text.set(q("style", "font-name-asian"), font)
        return _TEXT_STYLE

    def _ensure_frame_style(self) -> str:
        if not self._has_style(_FRAME_STYLE):
            style = etree.SubElement(self._automatic_styles(), q("style", "style"))
            style.set(q("style", "name"), _FRAME_STYLE)
            style.set(q("style", "family"), "graphic")
            props = etree.SubElement(style, q("style", "graphic-properties"))
            props.set(q("draw", "fill"), "none")
            props.set(q("draw", "stroke"), "none")
            props.set(q("draw", "auto-grow-height"), "true")
            for side in ("top", "right", "bottom", "left"):
                props.set(q("fo", f"padding-{side}"), "0cm")
        return _FRAME_STYLE

    def _ensure_rect_style(self) -> str:
        if not self._has_style(_RECT_STYLE):
            style = etree.SubElement(self._automatic_styles(), q("style", "style"))
            style.set(q("style", "name"), _RECT_STYLE)
            style.set(q("style", "family"), "graphic")
            props = etree.SubElement(style, q("style", "graphic-properties"))
            props.set(q("draw", "fill"), "solid")
            props.set(q("draw", "fill-color"), "#E8ECF4")
            props.set(q("draw", "stroke"), "none")
        return _RECT_STYLE

    def _common_font(self) -> Optional[str]:
        counts: Counter[str] = Counter()
        for el in self.content.iter(q("style", "text-properties")):
            name = el.get(q("style", "font-name"))
            if name:
                counts[name] += 1
        return counts.most_common(1)[0][0] if counts else None


def _paragraph_text(paragraph: etree._Element) -> str:
    parts: List[str] = []

    def walk(el: etree._Element) -> None:
        parts.append(el.text or "")
        for child in el:
            if child.tag == q("text", "s"):
                parts.append(" " * int(child.get(q("text", "c"), "1")))
            elif child.tag == q("text", "tab"):
                parts.append("\t")
            elif child.tag == q("text", "line-break"):
                parts.append("\n")
            else:
                walk(child)
            parts.append(child.tail or "")

    walk(paragraph)
    return "".join(parts)


def _set_paragraph_text(paragraph: etree._Element, text: str) -> None:
    """Replace a paragraph's text, keeping its paragraph style and first span's look."""
    span_style = None
    first_span = paragraph.find(q("text", "span"))
    if first_span is not None and len(paragraph) == 1 and not (paragraph.text or "").strip():
        span_style = first_span.get(q("text", "style-name"))
    for child in list(paragraph):
        paragraph.remove(child)
    paragraph.text = None
    target = paragraph
    if span_style:
        target = etree.SubElement(paragraph, q("text", "span"))
        target.set(q("text", "style-name"), span_style)
    lines = text.split("\n")
    target.text = lines[0]
    for line in lines[1:]:
        br = etree.SubElement(target, q("text", "line-break"))
        br.tail = line


_RUN_DEFAULTS: Dict[str, Any] = {"weight": 400, "italic": False, "underline": False}


def _run_diff(run: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, Any]:
    """The run's look where it differs from its paragraph's own style."""
    diff: Dict[str, Any] = {}
    for key in ("weight", "italic", "underline", "color", "size", "family"):
        if key not in run or run[key] is None:
            continue
        value = run[key]
        if key == "family":
            current_family = (base.get("family") or [None])[0]
            if str(value) != current_family:
                diff["family"] = str(value)
            continue
        if key == "weight":
            value = 700 if float(value) >= 600 else 400
        elif key == "size":
            value = round(float(value) * 2) / 2
        elif key == "color":
            value = str(value).upper()
        current = base.get(key, _RUN_DEFAULTS.get(key))
        if key == "weight" and current is not None:
            current = 700 if float(current) >= 600 else 400
        if key == "color" and current:
            current = str(current).upper()
        if key == "size" and current is not None:
            current = round(float(current) * 2) / 2
        if value != current:
            diff[key] = value
    return diff


def _append_lines(parent: etree._Element, text: str) -> None:
    """Append text (with ``\n`` as line breaks) after the parent's last child."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if i:
            etree.SubElement(parent, q("text", "line-break"))
        if not line:
            continue
        if len(parent):
            parent[-1].tail = (parent[-1].tail or "") + line
        else:
            parent.text = (parent.text or "") + line


def _write_lines(target: etree._Element, text: str) -> None:
    target.text = None
    _append_lines(target, text)


def _theme_summary(content: etree._Element, styles: etree._Element) -> Dict[str, Any]:
    from .components import infer_theme

    theme = infer_theme(content, styles)
    return {"accent": theme.accent, "ink": theme.ink, "muted": theme.muted}


def _group_box(group: etree._Element) -> Tuple[float, float, float, float]:
    xs: List[float] = []
    ys: List[float] = []
    for el in group.iter():
        if el is group or not isinstance(el.tag, str):
            continue
        if el.get(q("svg", "x1")) is not None:
            xs += [cm(el.get(q("svg", "x1"))), cm(el.get(q("svg", "x2")))]
            ys += [cm(el.get(q("svg", "y1"))), cm(el.get(q("svg", "y2")))]
        elif el.get(q("svg", "x")) is not None and el.get(q("svg", "width")) is not None:
            x, y = cm(el.get(q("svg", "x"))), cm(el.get(q("svg", "y")))
            xs += [x, x + cm(el.get(q("svg", "width")))]
            ys += [y, y + cm(el.get(q("svg", "height")))]
    if not xs:
        raise EditError("空的群組")
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def _transform(
    group: etree._Element, ox: float, oy: float, nx: float, ny: float, sx: float, sy: float
) -> None:
    """Map every child box from the old group box onto the new one."""
    for el in group.iter():
        if el is group or not isinstance(el.tag, str):
            continue
        for x_attr, y_attr in (("x", "y"), ("x1", "y1"), ("x2", "y2")):
            if el.get(q("svg", x_attr)) is not None:
                el.set(q("svg", x_attr), _len(nx + (cm(el.get(q("svg", x_attr))) - ox) * sx))
            if el.get(q("svg", y_attr)) is not None:
                el.set(q("svg", y_attr), _len(ny + (cm(el.get(q("svg", y_attr))) - oy) * sy))
        if el.get(q("svg", "width")) is not None:
            el.set(q("svg", "width"), _len(max(0.05, cm(el.get(q("svg", "width"))) * sx)))
        if el.get(q("svg", "height")) is not None:
            el.set(q("svg", "height"), _len(max(0.05, cm(el.get(q("svg", "height"))) * sy)))


def _offset(el: etree._Element, dx: float, dy: float) -> None:
    from .scene import cm

    for attr, delta in (("x", dx), ("y", dy), ("x1", dx), ("x2", dx), ("y1", dy), ("y2", dy)):
        value = el.get(q("svg", attr))
        if value is not None:
            el.set(q("svg", attr), _len(cm(value) + delta))


__all__ = ["NS", "Document", "EditError"]
