from __future__ import annotations

import base64

import pytest

from odforge.ir import ImageSpec
from odforge.media import (
    AssetBlob,
    MediaError,
    decode_data_uri,
    load_local_image,
    normalize_assets,
    resolve_image,
    validate_image_bytes,
)

PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000154a24f3b0000000049454e44ae426082"
)
DATA_URI = "data:image/png;base64," + base64.b64encode(PNG).decode()


def test_data_uri_is_validated_and_dimensions_are_read():
    blob = decode_data_uri(DATA_URI)
    assert blob.mime_type == "image/png"
    assert blob.extension == ".png"
    assert (blob.width, blob.height) == (1, 1)


def test_declared_mime_must_match_signature():
    with pytest.raises(MediaError, match="signature"):
        validate_image_bytes(PNG, "image/jpeg")


def test_local_and_explicit_asset_resolution(tmp_path):
    path = tmp_path / "hero.png"
    path.write_bytes(PNG)
    blob = load_local_image(path)
    assets = normalize_assets({"hero": path})
    resolved = resolve_image(
        ImageSpec(src="asset://hero", alt="產品情境"), assets
    )
    assert resolved == blob


def test_model_cannot_smuggle_local_path_or_unsafe_asset_id():
    with pytest.raises(ValueError, match="image src"):
        ImageSpec(src="file:///etc/passwd", alt="unsafe")
    with pytest.raises(MediaError, match="unsafe asset id"):
        normalize_assets({"../secret": PNG})


def test_prompt_uses_pluggable_provider():
    expected = AssetBlob(PNG, "image/png", ".png", 1, 1)

    class Provider:
        def generate(self, prompt: str) -> AssetBlob:
            assert prompt == "editorial solar farm"
            return expected

    image = ImageSpec(prompt="editorial solar farm", alt="太陽能場")
    assert resolve_image(image, {}, Provider()) == expected


def test_missing_asset_and_disabled_provider_become_placeholder():
    assert (
        resolve_image(
            ImageSpec(src="asset://missing", alt="缺少素材"),
            {},
        )
        is None
    )
