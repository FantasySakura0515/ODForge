"""Three-gate ODF validation.

``validate_odf`` runs up to three independent gates over a generated ODF
file and reports the outcome of each without ever raising:

1. ``"structure"`` - zip opens; first entry is a ZIP_STORED ``mimetype``
   whose value matches the file extension; ``META-INF/manifest.xml`` exists
   and every listed full-path (other than ``"/"``) is present in the zip.
2. ``"xml"`` - every ``.xml`` member parses as well-formed XML via lxml.
3. ``"soffice"`` - only when ``with_soffice=True`` and LibreOffice is found;
   round-trips the file through ``soffice --convert-to pdf`` and checks that
   a PDF is produced.

Depends only on the stdlib, lxml and the mimetype constants from
``package.py`` - never on ``render/*`` or the IR.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from .package import ODP_MIMETYPE, ODS_MIMETYPE, ODT_MIMETYPE

_MANIFEST_NS = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
_MANIFEST_PATH = "META-INF/manifest.xml"

_EXT_MIMETYPE = {
    ".odt": ODT_MIMETYPE,
    ".odp": ODP_MIMETYPE,
    ".ods": ODS_MIMETYPE,
}

_SOFFICE_TIMEOUT = 120


@dataclass
class ValidationReport:
    ok: bool                                # 所有跑過的 gate 都 True
    gates: dict[str, tuple[bool, str]]      # gate 名 → (通過?, 訊息);鍵:"structure" | "xml" | "soffice"


def find_soffice() -> Path | None:
    """Locate the LibreOffice ``soffice`` executable, or ``None``."""
    found = shutil.which("soffice")
    if found:
        return Path(found)
    for candidate in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ):
        p = Path(candidate)
        if p.exists():
            return p
    return None


def _gate_structure(path: Path) -> tuple[bool, str]:
    """Gate 1: zip layout, mimetype-first-stored, extension match, manifest."""
    if not zipfile.is_zipfile(path):
        return False, f"not a valid zip file: {path.name}"
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if not infos:
            return False, "empty zip archive"

        first = infos[0]
        if first.filename != "mimetype":
            return False, f"first entry is {first.filename!r}, expected 'mimetype'"
        if first.compress_type != zipfile.ZIP_STORED:
            return False, "mimetype entry is not stored (ZIP_STORED)"

        mimetype = z.read("mimetype").decode("utf-8", errors="replace").strip()
        expected = _EXT_MIMETYPE.get(path.suffix.lower())
        if expected is None:
            return False, f"unknown extension {path.suffix!r}"
        if mimetype != expected:
            return False, f"mimetype {mimetype!r} does not match extension {path.suffix!r} (expected {expected!r})"

        names = set(z.namelist())
        if _MANIFEST_PATH not in names:
            return False, f"missing {_MANIFEST_PATH}"

        try:
            root = etree.fromstring(z.read(_MANIFEST_PATH))
        except etree.XMLSyntaxError as exc:
            return False, f"manifest is not well-formed XML: {exc}"

        missing = []
        for entry in root.iter(f"{{{_MANIFEST_NS}}}file-entry"):
            full_path = entry.get(f"{{{_MANIFEST_NS}}}full-path")
            if not full_path or full_path == "/":
                continue
            if full_path.endswith("/"):
                # Directory entry: satisfied if any member lives under it.
                if not any(n.startswith(full_path) for n in names):
                    missing.append(full_path)
            elif full_path not in names:
                missing.append(full_path)
        if missing:
            return False, f"manifest lists parts absent from zip: {', '.join(sorted(missing))}"

    return True, "structure ok"


def _gate_xml(path: Path) -> tuple[bool, str]:
    """Gate 2: every .xml member is well-formed."""
    if not zipfile.is_zipfile(path):
        return False, f"not a valid zip file: {path.name}"
    bad = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            if not name.lower().endswith(".xml"):
                continue
            data = z.read(name)
            if not data.strip():
                # Empty placeholder parts (e.g. Configurations2/.../current.xml)
                # are emitted by real ODF producers and carry no content to be
                # malformed.
                continue
            try:
                etree.fromstring(data)
            except etree.XMLSyntaxError as exc:
                bad.append(f"{name}: {exc}")
    if bad:
        return False, "malformed XML in " + "; ".join(bad)
    return True, "all xml well-formed"


def _gate_soffice(path: Path, soffice: Path) -> tuple[bool, str]:
    """Gate 3: round-trip through soffice --convert-to pdf."""
    tmpdir = Path(tempfile.mkdtemp(prefix="odforge-validate-"))
    try:
        proc = subprocess.run(
            [
                str(soffice),
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(tmpdir),
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=_SOFFICE_TIMEOUT,
        )
        if proc.returncode != 0:
            stderr = (proc.stderr or proc.stdout or "").strip()
            return False, f"soffice exited {proc.returncode}: {stderr[:500]}"
        pdf = tmpdir / (path.stem + ".pdf")
        if not pdf.exists():
            stderr = (proc.stderr or proc.stdout or "").strip()
            return False, f"soffice produced no pdf: {stderr[:500]}"
        return True, f"converted to pdf ({pdf.stat().st_size} bytes)"
    except subprocess.TimeoutExpired:
        return False, f"soffice timed out after {_SOFFICE_TIMEOUT}s"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def validate_odf(path: Path, *, with_soffice: bool = False) -> ValidationReport:
    """Validate an ODF file through the structure/xml/(soffice) gates.

    Never raises: any unexpected exception inside a gate becomes a
    ``(False, message)`` result for that gate. Gates run independently so a
    failure in an earlier gate does not stop later gates from reporting.
    """
    path = Path(path)
    gates: dict[str, tuple[bool, str]] = {}

    for name, fn in (("structure", _gate_structure), ("xml", _gate_xml)):
        try:
            gates[name] = fn(path)
        except Exception as exc:  # noqa: BLE001 - gates must never raise
            gates[name] = (False, f"{name} gate raised {type(exc).__name__}: {exc}")

    if with_soffice:
        soffice = find_soffice()
        if soffice is not None:
            try:
                gates["soffice"] = _gate_soffice(path, soffice)
            except Exception as exc:  # noqa: BLE001 - gates must never raise
                gates["soffice"] = (False, f"soffice gate raised {type(exc).__name__}: {exc}")

    ok = all(passed for passed, _ in gates.values())
    return ValidationReport(ok=ok, gates=gates)
