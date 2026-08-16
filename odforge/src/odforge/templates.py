"""Deck templates (範本庫): the built-in presets plus the user's own designs.

A *template* is a name and a :class:`~odforge.ir.DesignSpec`. The thirteen
built-in art directions (:data:`odforge.themes.THEMES`) are exposed through the
same shape as user-saved ones so a gallery can list them together, but they are
read-only: deleting or overwriting a preset would break every deck that names it
by id.

User templates are stored as one JSON file inside the sessions directory, which
is where everything else the user owns already lives (so a backup of that folder
keeps their templates too). Writes go through a temp file + ``os.replace`` so an
interrupted save cannot leave a half-written library behind.

Two ways to make one, both of which end at the same ``DesignSpec``:

* hand-picked colours and fonts (validated by ``DesignSpec`` — an unreadable
  palette is rejected by the same WCAG bars the renderer trusts), and
* :func:`odforge.extract.extract_design` on an existing ``.otp``/``.odp``
  template file, which is how a school's official deck becomes a template here.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import List, Optional

from pydantic import Field, field_validator

from odforge.ir import DesignSpec, FontPair, Palette, StrictModel
from odforge.themes import THEME_LABELS, THEMES, theme_scale

# A library is a personal shortlist, not a catalogue. The cap exists so a script
# in a loop cannot grow the file without bound.
MAX_USER_TEMPLATES = 50
MAX_NAME_CHARS = 40

TEMPLATES_FILENAME = "templates.json"


class Template(StrictModel):
    """One entry in the gallery: a named design, built-in or user-owned."""

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=MAX_NAME_CHARS)
    design: DesignSpec
    # Built-ins are read-only and are sent to the API as ``theme: <id>``;
    # user templates travel as a full ``design`` payload.
    builtin: bool = False
    # "builtin" | "custom" (hand-picked) | "extracted" (from an ODF template).
    source: str = "custom"
    created_at: float = 0.0

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("template name must not be blank")
        return value


def design_of_theme(theme_id: str) -> DesignSpec:
    """The built-in preset ``theme_id`` expressed as an editable DesignSpec.

    This is what makes "以內建主題為底改一版" possible in the gallery: the preset
    is a resolved :class:`~odforge.themes.Theme` (four point sizes), and a spec
    needs the tier name those sizes came from.
    """
    theme = THEMES[theme_id]
    return DesignSpec(
        palette=Palette(
            bg=theme.bg,
            surface=theme.surface,
            text=theme.text,
            muted=theme.muted,
            accent=theme.accent,
        ),
        fonts=FontPair(display=theme.font_display, body=theme.font_body),
        scale=theme_scale(theme),
    )


def builtin_templates() -> List[Template]:
    """The thirteen presets, in the order :data:`THEME_LABELS` declares them."""
    return [
        Template(
            id=theme_id,
            name=THEME_LABELS[theme_id],
            design=design_of_theme(theme_id),
            builtin=True,
            source="builtin",
        )
        for theme_id in THEME_LABELS
        if theme_id in THEMES
    ]


def templates_path(sessions_dir: Path) -> Path:
    """Where the user's library lives for a given sessions directory."""
    return Path(sessions_dir) / TEMPLATES_FILENAME


def load_user_templates(path: Path) -> List[Template]:
    """Read the user's templates; an unreadable library reads as empty.

    A corrupt file must not take the whole console down with it — the gallery
    degrades to "built-ins only". Nothing is deleted here: :func:`save_template`
    moves the bad file aside (rather than overwriting it) the next time the user
    saves, so the original is still on disk to look at.
    """
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    templates: List[Template] = []
    for item in data:
        try:
            template = Template.model_validate(item)
        except Exception:  # noqa: BLE001 - one bad row must not lose the rest
            continue
        if template.builtin:
            # A preset id in the user file would shadow the read-only original.
            continue
        templates.append(template)
    return templates


def all_templates(path: Path) -> List[Template]:
    """Built-ins first, then the user's own (newest last)."""
    return builtin_templates() + load_user_templates(path)


def _write(path: Path, templates: List[Template]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        [t.model_dump(mode="json") for t in templates],
        ensure_ascii=False,
        indent=2,
    )
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def save_template(
    path: Path,
    *,
    name: str,
    design: DesignSpec,
    source: str = "custom",
    template_id: Optional[str] = None,
) -> Template:
    """Add a template, or replace the one whose id is ``template_id``.

    Raises ``ValueError`` when the library is full, when ``template_id`` names a
    built-in preset, or when it names a template that no longer exists (editing
    something another tab already deleted must not silently re-create it).
    """
    if template_id is not None and template_id in THEMES:
        raise ValueError("內建範本不可覆寫，請另存新範本。")
    existing = load_user_templates(path)
    if path.is_file() and not existing and path.stat().st_size > 0:
        # Unreadable library + a save on top of it would destroy whatever was
        # there. Keep the original under a new name instead.
        path.replace(path.with_suffix(path.suffix + ".corrupt"))
    now = time.time()
    if template_id is None:
        if len(existing) >= MAX_USER_TEMPLATES:
            raise ValueError(
                f"自訂範本已達上限 {MAX_USER_TEMPLATES} 個，請先刪除不用的範本。"
            )
        template = Template(
            id=f"tpl-{uuid.uuid4().hex[:12]}",
            name=name,
            design=design,
            source=source,
            created_at=now,
        )
        existing.append(template)
        _write(path, existing)
        return template

    for index, current in enumerate(existing):
        if current.id == template_id:
            template = Template(
                id=template_id,
                name=name,
                design=design,
                source=source,
                created_at=current.created_at or now,
            )
            existing[index] = template
            _write(path, existing)
            return template
    raise ValueError("找不到這個範本，可能已被刪除。")


def delete_template(path: Path, template_id: str) -> bool:
    """Remove a user template. Returns False when there was nothing to remove."""
    if template_id in THEMES:
        raise ValueError("內建範本不可刪除。")
    existing = load_user_templates(path)
    remaining = [t for t in existing if t.id != template_id]
    if len(remaining) == len(existing):
        return False
    _write(path, remaining)
    return True


__all__ = [
    "MAX_USER_TEMPLATES",
    "TEMPLATES_FILENAME",
    "Template",
    "all_templates",
    "builtin_templates",
    "delete_template",
    "design_of_theme",
    "load_user_templates",
    "save_template",
    "templates_path",
]
