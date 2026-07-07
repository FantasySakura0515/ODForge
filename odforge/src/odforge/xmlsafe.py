"""Hardened lxml parsing for UNTRUSTED ODF/OOXML XML (single source of truth).

Every ODForge module that parses a file the user points the tool at parses
attacker-controllable XML: an ODF template fed to
:func:`odforge.extract.extract_design`, a document validated by
:func:`odforge.validate.validate_odf`, or a package inspected by
:func:`odforge.check.check_odf`. Such XML must never resolve external entities
(an XXE ``file://`` SYSTEM entity would disclose local files — lxml's
``no_network`` default does NOT block local-file entities) and must bound entity
expansion (billion-laughs DoS). ODF styles/content never legitimately need custom
entity expansion, so disabling it is safe.

:data:`SAFE_PARSER` is the one hardened parser the whole codebase shares:

* ``resolve_entities=False`` — leave entity refs unexpanded (no local-file read),
* ``load_dtd=False`` + ``no_network=True`` — never load an external DTD,
* ``huge_tree=False`` — keep libxml2's built-in entity-expansion / tree-size caps
  so a nested-entity bomb raises instead of exhausting memory.

Use :func:`safe_fromstring` in place of ``lxml.etree.fromstring`` for any bytes
that originate from a user-supplied file.
"""

from __future__ import annotations

import lxml.etree as etree

__all__ = ["SAFE_PARSER", "safe_fromstring"]

SAFE_PARSER = etree.XMLParser(
    resolve_entities=False,
    no_network=True,
    load_dtd=False,
    huge_tree=False,
)


def safe_fromstring(data: bytes) -> etree._Element:
    """Parse untrusted XML ``data`` with the hardened :data:`SAFE_PARSER`.

    Behaves like :func:`lxml.etree.fromstring` for well-formed input, but never
    dereferences external entities and bounds entity expansion. Raises
    :class:`lxml.etree.XMLSyntaxError` on malformed input exactly as the bare
    parser does, so existing ``except XMLSyntaxError`` handlers are unaffected.
    """
    return etree.fromstring(data, SAFE_PARSER)
