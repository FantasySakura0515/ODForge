"""Task 8.2: human-readable ODF check reports.

Two entry points, both returning clean markdown (never raising - problems come
back as report text or ``error:`` strings):

- :func:`check_odf` runs the three-gate validator over an ODF file and adds a
  style-reference integrity pass, rendering the outcome as a markdown report.
- :func:`diff_docx_odt` converts a ``.docx`` to ``.odt`` via LibreOffice and
  reports a heuristic structural comparison of the two.

Depends only on the stdlib, lxml and :mod:`odforge.validate`. It never imports
``render/*`` or the IR: it consumes a :class:`~pathlib.Path` and nothing more.
The markdown it emits doubles as competition evidence ("ODF 優化建議"), so the
formatting is kept tidy and deterministic.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

from .validate import find_soffice, run_soffice_convert, validate_odf
from .xmlsafe import safe_fromstring
from .zipguard import (
    MAX_XML_MEMBER,
    ArchiveLimitError,
    inspect_archive,
    read_xml_member,
)

# ---------------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------------

_TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_DRAW_NS = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
_TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_STYLE_NS = "urn:oasis:names:tc:opendocument:xmlns:style:1.0"

# The style-reference attributes we treat as "uses a named style".
_STYLE_REF_ATTRS = frozenset(
    {
        f"{{{_TEXT_NS}}}style-name",
        f"{{{_DRAW_NS}}}style-name",
        f"{{{_TABLE_NS}}}style-name",
    }
)
# Attribute that declares a named style (in either automatic-styles or styles.xml).
_STYLE_NAME_ATTR = f"{{{_STYLE_NS}}}name"

# Built-in / producer-supplied styles that ODF applications resolve without an
# explicit definition inside the package. odfdo and LibreOffice routinely
# reference these, so an unresolved reference to one of them is not a defect.
STYLE_REFERENCE_WHITELIST = frozenset(
    {
        "Default",
        "Standard",
        "Text_20_body",
        "Heading",
        "Caption",
        "List",
        "Table_20_Contents",
        "Table_20_Heading",
        "Graphics",
        "Frame",
        "OLE",
        "Formula",
        "Illustration",
        "Drawing",
        "Footnote",
        "Endnote",
        "Header",
        "Footer",
        "Index",
        "Contents_20_Heading",
        "Numbering_20_Symbols",
        "Bullet_20_Symbols",
        "List_20_Paragraph",
    }
)

_SOFFICE_TIMEOUT = 120

# docx (OOXML wordprocessing) namespaces.
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# ---------------------------------------------------------------------------
# check_odf
# ---------------------------------------------------------------------------


def _read_part(path: Path, name: str) -> bytes | None:
    """Return the bytes of zip member ``name``, or ``None`` if absent."""
    try:
        with zipfile.ZipFile(path) as z:
            infos = inspect_archive(z)
            if name not in {info.filename for info in infos}:
                return None
            return read_xml_member(z, name, max_bytes=MAX_XML_MEMBER)
    except (ArchiveLimitError, zipfile.BadZipFile, OSError):
        return None


def _style_reference_issues(path: Path) -> tuple[list[str], str | None]:
    """Return (undefined-style-references, error-message).

    Collects every ``text:``/``draw:``/``table:style-name`` reference in
    content.xml and checks each against the styles defined in content.xml's
    automatic-styles plus styles.xml. Whitelisted built-ins are allowed through.
    On a parse/read failure returns ``([], message)``.
    """
    content = _read_part(path, "content.xml")
    if content is None:
        return [], "content.xml 缺失或無法讀取"

    defined: set[str] = set()
    refs: set[str] = set()

    try:
        # Untrusted input: hardened parse (no external-entity resolution).
        content_root = safe_fromstring(content)
    except etree.XMLSyntaxError as exc:
        return [], f"content.xml 無法解析:{exc}"

    for el in content_root.iter():
        name = el.get(_STYLE_NAME_ATTR)
        if name:
            defined.add(name)
        for attr in _STYLE_REF_ATTRS:
            val = el.get(attr)
            if val:
                refs.add(val)

    styles = _read_part(path, "styles.xml")
    if styles is not None:
        try:
            # Untrusted input: hardened parse (no external-entity resolution).
            styles_root = safe_fromstring(styles)
        except etree.XMLSyntaxError:
            styles_root = None
        if styles_root is not None:
            for el in styles_root.iter():
                name = el.get(_STYLE_NAME_ATTR)
                if name:
                    defined.add(name)

    undefined = sorted(
        ref
        for ref in refs
        if ref not in defined and ref not in STYLE_REFERENCE_WHITELIST
    )
    return undefined, None


def _part_listing(path: Path) -> list[str]:
    """Return ``"- name (N bytes)"`` lines for each package part, or an error."""
    try:
        with zipfile.ZipFile(path) as z:
            return [
                f"- {info.filename} ({info.file_size} bytes)"
                for info in inspect_archive(z)
            ]
    except (ArchiveLimitError, zipfile.BadZipFile, OSError) as exc:
        return [f"- (無法列出部件:{exc})"]


def check_odf(path: Path) -> str:
    """Validate an ODF file and return a markdown check report.

    Never raises. A missing file yields ``"error: file not found: {path}"``;
    any unexpected exception while building the report becomes an ``error:``
    string (same outer guard as validate.py's gates).
    """
    return check_odf_verdict(path)[0]


def check_odf_verdict(path: Path) -> tuple[str, bool]:
    """``(report, failed)`` — the verdict comes from the structured gate results.

    The report's free text echoes the file name, part names and style names, so
    a caller that greps it for "FAIL" flips its exit code on a perfectly valid
    file that merely *contains* that substring (``q4-FAIL-review.odt``). The
    boolean is derived from the gates themselves and immune to that.
    """
    path = Path(path)
    if not path.exists():
        return f"error: file not found: {path}", True
    try:
        return _check_odf_report(path)
    except Exception as exc:
        return f"error: check failed: {type(exc).__name__}: {exc}", True


def _check_odf_report(path: Path) -> tuple[str, bool]:
    """Build the markdown report for an existing file (may raise; guarded)."""
    report = validate_odf(path, with_soffice=find_soffice() is not None)

    lines: list[str] = [f"# ODF 檢測報告:{path.name}", ""]

    failed = False
    lines.append("## 驗證結果")
    for name in ("structure", "xml", "soffice"):
        if name not in report.gates:
            continue
        passed, message = report.gates[name]
        failed = failed or not passed
        status = "OK" if passed else "FAIL"
        lines.append(f"- {name}: {status} — {message}")
    lines.append("")

    lines.append("## 部件清單")
    lines.extend(_part_listing(path))
    lines.append("")

    lines.append("## 樣式引用")
    undefined, err = _style_reference_issues(path)
    if err is not None:
        lines.append(f"- 略過:{err}")
    elif not undefined:
        lines.append("- OK — 所有樣式引用皆有定義")
    else:
        lines.append("- 發現未定義的樣式引用(引用了但未定義):")
        lines.extend(f"  - `{name}`" for name in undefined)
    lines.append("")

    # Style-reference issues stay informational (they never said FAIL either):
    # only the three gates decide the verdict.
    return "\n".join(lines).rstrip() + "\n", failed


# ---------------------------------------------------------------------------
# diff_docx_odt
# ---------------------------------------------------------------------------


def _convert_docx_to_odt(docx: Path, soffice: Path, outdir: Path) -> tuple[Path | None, str]:
    """Convert ``docx`` to ``.odt`` inside ``outdir`` using an isolated profile.

    Delegates to :func:`odforge.validate.run_soffice_convert`, the single shared
    soffice entry point (captured output, timeout, private-profile isolation).
    Returns (odt-path, message); the path is ``None`` on failure.
    """
    try:
        proc = run_soffice_convert(soffice, docx, "odt", outdir, timeout=_SOFFICE_TIMEOUT)
    except subprocess.TimeoutExpired:
        return None, f"soffice timed out after {_SOFFICE_TIMEOUT}s"
    if proc.returncode != 0:
        stderr = (proc.stderr or proc.stdout or "").strip()
        return None, f"soffice exited {proc.returncode}: {stderr[:500]}"
    odt = outdir / (docx.stem + ".odt")
    if not odt.exists():
        stderr = (proc.stderr or proc.stdout or "").strip()
        return None, f"soffice produced no odt: {stderr[:500]}"
    return odt, "ok"


def _count_docx(docx: Path) -> dict[str, int]:
    """Heuristic element counts from a .docx's word/document.xml."""
    data = _read_part(docx, "word/document.xml")
    if data is None:
        raise ValueError("word/document.xml 缺失")
    # Untrusted input: hardened parse (no external-entity resolution).
    root = safe_fromstring(data)

    paragraphs = len(root.findall(f".//{{{_W_NS}}}p"))
    tables = len(root.findall(f".//{{{_W_NS}}}tbl"))
    # Images: prefer drawings, fall back to legacy VML pictures.
    images = len(root.findall(f".//{{{_W_NS}}}drawing")) or len(
        root.findall(f".//{{{_W_NS}}}pict")
    )
    headings = 0
    for pstyle in root.iter(f"{{{_W_NS}}}pStyle"):
        val = pstyle.get(f"{{{_W_NS}}}val", "")
        if val.startswith("Heading"):
            headings += 1
    return {
        "paragraph": paragraphs,
        "table": tables,
        "image": images,
        "heading": headings,
    }


def _count_odt(odt: Path) -> dict[str, int]:
    """Heuristic element counts from an .odt's content.xml."""
    data = _read_part(odt, "content.xml")
    if data is None:
        raise ValueError("content.xml 缺失")
    # Untrusted input: hardened parse (no external-entity resolution).
    root = safe_fromstring(data)
    return {
        "paragraph": len(root.findall(f".//{{{_TEXT_NS}}}p")),
        "table": len(root.findall(f".//{{{_TABLE_NS}}}table")),
        "image": len(root.findall(f".//{{{_DRAW_NS}}}image")),
        "heading": len(root.findall(f".//{{{_TEXT_NS}}}h")),
    }


# Display label per element key.
_ELEMENT_LABELS = (
    ("paragraph", "段落 (paragraph)"),
    ("table", "表格 (table)"),
    ("image", "圖片 (image)"),
    ("heading", "標題 (heading)"),
)


def diff_docx_odt(docx: Path) -> str:
    """Convert ``docx`` to ``.odt`` and return a heuristic comparison report.

    Never raises. Without LibreOffice, returns an explanatory string. A failed
    conversion yields ``"error: conversion failed: {摘要}"``; any unexpected
    exception (e.g. a stale soffice path raising ``FileNotFoundError`` inside
    ``subprocess.run``) is likewise returned as an ``error:`` string.
    """
    return diff_docx_odt_verdict(docx)[0]


def diff_docx_odt_verdict(docx: Path) -> tuple[str, bool]:
    """``(report, failed)`` — failed only when the comparison could not run.

    Count mismatches are heuristic observations, not failures (they never
    tripped the exit code before either); ``error:`` reports are.
    """
    docx = Path(docx)
    soffice = find_soffice()
    if soffice is None:
        return (
            "error: 此比對需要 LibreOffice(soffice)把 .docx 轉成 .odt,"
            "但在本機找不到 soffice。請安裝 LibreOffice 後再試。"
        ), True

    if not docx.exists():
        return f"error: file not found: {docx}", True

    try:
        report = _diff_report(docx, soffice)
    except Exception as exc:
        return f"error: conversion failed: {type(exc).__name__}: {exc}", True
    return report, report.startswith("error:")


def _diff_report(docx: Path, soffice: Path) -> str:
    """Convert, count and format the comparison (may raise; guarded)."""
    outdir = Path(tempfile.mkdtemp(prefix="odforge-check-diff-"))
    try:
        odt, message = _convert_docx_to_odt(docx, soffice, outdir)
        if odt is None:
            return f"error: conversion failed: {message}"

        try:
            docx_counts = _count_docx(docx)
            odt_counts = _count_odt(odt)
        except (etree.XMLSyntaxError, ValueError) as exc:
            return f"error: conversion failed: 無法解析結構:{exc}"
    finally:
        shutil.rmtree(outdir, ignore_errors=True)

    lines: list[str] = [
        f"# DOCX/ODT 結構比對報告:{docx.name}",
        "",
        "> heuristic structural comparison(啟發式結構比對):以段落/表格/圖片/標題"
        "的計數比較轉檔前後的文件結構,數量差異僅供參考。",
        "",
        "| 元素 | DOCX | ODT |",
        "| --- | ---: | ---: |",
    ]
    for key, label in _ELEMENT_LABELS:
        lines.append(f"| {label} | {docx_counts[key]} | {odt_counts[key]} |")
    lines.append("")

    lines.append("## 差異說明")
    mismatches = [
        (label, docx_counts[key], odt_counts[key])
        for key, label in _ELEMENT_LABELS
        if docx_counts[key] != odt_counts[key]
    ]
    if not mismatches:
        lines.append("- OK — 各類元素數量一致,轉檔結構相符。")
    else:
        for label, dv, ov in mismatches:
            lines.append(f"- {label}:DOCX={dv} 對 ODT={ov},數量不一致。")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"
