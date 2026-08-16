"""Tests for the deck template library (範本庫) and its API surface.

Two things are being protected here:

* the **library file** — a personal, on-disk store that must survive a corrupt
  read, a concurrent-looking overwrite and a delete, without ever losing data it
  cannot re-derive, and
* the **built-in / user split** — presets are read-only and travel as
  ``theme: <id>``; user templates travel as a full ``design`` payload. Blurring
  that line would let a user shadow a preset id and silently repaint every deck
  that names it.
"""

from __future__ import annotations

import base64
import io
import json
import zipfile
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from odforge import templates as T  # noqa: E402
from odforge import webapi  # noqa: E402
from odforge.ir import DesignSpec, FontPair, Palette  # noqa: E402
from odforge.themes import THEME_LABELS, THEMES  # noqa: E402


def _design(accent: str = "#1A4B8C") -> DesignSpec:
    return DesignSpec(
        palette=Palette(
            bg="#FBF9F4",
            surface="#EFEADD",
            text="#1F2733",
            muted="#5B6470",
            accent=accent,
        ),
        fonts=FontPair(display="Noto Serif TC", body="Noto Sans TC"),
        scale="standard",
    )


@pytest.fixture
def library(tmp_path) -> Path:
    return T.templates_path(tmp_path / "sessions")


# ---------------------------------------------------------------------------
# The library file
# ---------------------------------------------------------------------------


def test_builtin_templates_mirror_the_theme_registry(library):
    builtins = T.builtin_templates()
    assert {t.id for t in builtins} == set(THEMES)
    assert all(t.builtin for t in builtins)
    # The palette a preset shows in the gallery must be the palette it renders.
    academic = next(t for t in builtins if t.id == "academic")
    assert academic.name == THEME_LABELS["academic"]
    assert academic.design.palette.accent == THEMES["academic"].accent
    assert academic.design.fonts.display == THEMES["academic"].font_display


def test_save_load_delete_round_trip(library):
    saved = T.save_template(library, name="校內公版", design=_design("#A3212F"))
    assert saved.id.startswith("tpl-")
    assert not saved.builtin

    loaded = T.load_user_templates(library)
    assert [t.name for t in loaded] == ["校內公版"]
    assert loaded[0].design.palette.accent == "#A3212F"

    assert T.delete_template(library, saved.id) is True
    assert T.load_user_templates(library) == []
    # Deleting again is a no-op, not an error: two tabs can race.
    assert T.delete_template(library, saved.id) is False


def test_saving_with_an_id_overwrites_that_template(library):
    saved = T.save_template(library, name="舊名", design=_design())
    updated = T.save_template(
        library, name="新名", design=_design("#0F766E"), template_id=saved.id
    )
    assert updated.id == saved.id
    assert updated.created_at == saved.created_at  # created_at is not reset
    assert [t.name for t in T.load_user_templates(library)] == ["新名"]


def test_editing_a_deleted_template_is_refused(library):
    saved = T.save_template(library, name="會被刪掉", design=_design())
    T.delete_template(library, saved.id)
    # Silently re-creating it would resurrect something the user deleted.
    with pytest.raises(ValueError, match="找不到"):
        T.save_template(
            library, name="復活", design=_design(), template_id=saved.id
        )


def test_builtin_ids_are_read_only(library):
    with pytest.raises(ValueError, match="不可覆寫"):
        T.save_template(
            library, name="偽裝成內建", design=_design(), template_id="navy"
        )
    with pytest.raises(ValueError, match="不可刪除"):
        T.delete_template(library, "navy")


def test_a_user_row_claiming_to_be_builtin_is_ignored(library):
    # Hand-edited file: a row with builtin=true and a preset id would shadow the
    # real preset in every list, and the real one would never be reachable.
    library.parent.mkdir(parents=True, exist_ok=True)
    library.write_text(
        json.dumps(
            [
                {
                    "id": "navy",
                    "name": "假的海軍藍",
                    "builtin": True,
                    "source": "builtin",
                    "design": _design("#FF0000").model_dump(mode="json"),
                }
            ]
        ),
        encoding="utf-8",
    )
    assert T.load_user_templates(library) == []
    navy = next(t for t in T.all_templates(library) if t.id == "navy")
    assert navy.design.palette.accent == THEMES["navy"].accent


def test_a_corrupt_library_reads_as_empty_and_is_kept_on_next_save(library):
    library.parent.mkdir(parents=True, exist_ok=True)
    library.write_text("{ this is not json", encoding="utf-8")
    # The console must still open.
    assert T.load_user_templates(library) == []
    assert len(T.all_templates(library)) == len(THEMES)

    T.save_template(library, name="新的", design=_design())
    # The unreadable original is moved aside, never overwritten in place.
    assert library.with_suffix(library.suffix + ".corrupt").is_file()
    assert [t.name for t in T.load_user_templates(library)] == ["新的"]


def test_one_unparseable_row_does_not_lose_the_others(library):
    good = T.save_template(library, name="好的", design=_design())
    rows = json.loads(library.read_text(encoding="utf-8"))
    rows.append({"id": "tpl-broken", "name": "壞的"})  # no design
    library.write_text(json.dumps(rows), encoding="utf-8")
    assert [t.id for t in T.load_user_templates(library)] == [good.id]


def test_library_is_capped(library, monkeypatch):
    monkeypatch.setattr(T, "MAX_USER_TEMPLATES", 2)
    T.save_template(library, name="一", design=_design())
    T.save_template(library, name="二", design=_design())
    with pytest.raises(ValueError, match="上限"):
        T.save_template(library, name="三", design=_design())


# ---------------------------------------------------------------------------
# HTTP surface
# ---------------------------------------------------------------------------


@pytest.fixture
def app(tmp_path):
    return webapi.create_app(jobs_dir=tmp_path / "jobs")


def test_list_templates_returns_builtins_and_languages(app):
    with TestClient(app) as client:
        body = client.get("/api/templates").json()
    ids = [t["id"] for t in body["templates"]]
    assert set(ids) >= set(THEMES)
    assert all(t["builtin"] for t in body["templates"])
    # The language menu is served from the same registry the generator uses, so
    # the frontend cannot offer a language the backend would reject.
    assert {lang["id"] for lang in body["languages"]} == {"zh-TW", "en", "bilingual"}


def test_create_and_delete_a_template_over_http(app):
    with TestClient(app) as client:
        created = client.post(
            "/api/templates",
            json={"name": "系上公版", "design": _design("#2C6E49").model_dump(mode="json")},
        )
        assert created.status_code == 200
        template_id = created.json()["id"]

        listed = client.get("/api/templates").json()["templates"]
        assert any(t["id"] == template_id and not t["builtin"] for t in listed)

        assert client.delete(f"/api/templates/{template_id}").status_code == 200
        assert client.delete(f"/api/templates/{template_id}").status_code == 404


def test_an_unreadable_palette_is_refused_by_the_api(app):
    # Light grey text on white: the contrast bars in ir.Palette exist so this
    # never reaches a deck. A 422 with the reason beats a grey unreadable slide.
    bad = {
        "palette": {
            "bg": "#FFFFFF",
            "surface": "#FFFFFF",
            "text": "#EEEEEE",
            "muted": "#F5F5F5",
            "accent": "#FAFAFA",
        },
        "fonts": {"display": "Noto Sans TC", "body": "Noto Sans TC"},
        "scale": "standard",
    }
    with TestClient(app) as client:
        response = client.post("/api/templates", json={"name": "看不見", "design": bad})
    assert response.status_code == 422
    assert "contrast" in response.text


def test_builtin_templates_cannot_be_deleted_over_http(app):
    with TestClient(app) as client:
        response = client.delete("/api/templates/navy")
    assert response.status_code == 422
    assert "內建" in response.json()["detail"]


def _odp_bytes() -> bytes:
    """A minimal ODF package extract_design can sample (styles.xml + content)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "mimetype", "application/vnd.oasis.opendocument.presentation"
        )
        archive.writestr(
            "styles.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<office:document-styles '
            'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
            'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
            'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:'
            'xsl-fo-compatible:1.0">'
            "<office:automatic-styles>"
            '<style:style style:name="dp1" style:family="drawing-page">'
            '<style:drawing-page-properties draw:fill="solid" '
            'draw:fill-color="#102A43"/></style:style>'
            '<style:style style:name="T1" style:family="text">'
            '<style:text-properties fo:color="#F0F4F8" '
            'style:font-name="Noto Sans TC"/></style:style>'
            "</office:automatic-styles>"
            "</office:document-styles>",
        )
        archive.writestr(
            "content.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<office:document-content '
            'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"/>',
        )
    return buffer.getvalue()


def test_extract_reads_a_design_out_of_an_uploaded_template(app):
    data_url = (
        "data:application/vnd.oasis.opendocument.presentation-template;base64,"
        + base64.b64encode(_odp_bytes()).decode()
    )
    with TestClient(app) as client:
        response = client.post("/api/templates/extract", json={"data_url": data_url})
    assert response.status_code == 200
    design = response.json()["design"]
    # Whatever it sampled, the result is always a usable, legible spec —
    # extract_design backfills anything it cannot read confidently.
    DesignSpec.model_validate(design)
    # Nothing was saved: extraction is a preview, naming is a separate step.
    with TestClient(app) as client:
        assert all(t["builtin"] for t in client.get("/api/templates").json()["templates"])


def test_a_non_odf_upload_is_refused(app):
    data_url = "data:application/zip;base64," + base64.b64encode(b"not a zip").decode()
    with TestClient(app) as client:
        response = client.post("/api/templates/extract", json={"data_url": data_url})
    assert response.status_code == 422
    assert "ODF" in response.json()["detail"]


def test_a_wrong_mime_upload_is_refused(app):
    data_url = "data:image/png;base64," + base64.b64encode(b"PK\x03\x04").decode()
    with TestClient(app) as client:
        response = client.post("/api/templates/extract", json={"data_url": data_url})
    assert response.status_code == 422
