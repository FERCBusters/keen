"""Download file extensions from untrusted names, storage keys and media types.

Uploaded file names and recorded media types are untrusted. A download keeps
only a short alphanumeric extension: the storage key's when it has one,
otherwise one the media type names.
"""

from types import SimpleNamespace

import pytest
from app.storage import filenames
from app.storage.filenames import (
    download_filename_for_artifact,
    guess_extension,
    safe_download_ext,
)


@pytest.mark.parametrize(
    "ext,expected",
    [
        ("pdf", ".pdf"),
        ("PDF", ".pdf"),
        (".Docx ", ".docx"),
        ("", ""),
        ("tar.gz", ""),
        ("../etc", ""),
        (" pdf", ""),
        ("p" * 11, ""),
    ],
)
def test_an_extension_is_kept_only_as_a_dot_and_up_to_ten_letters_or_digits(
    ext, expected
):
    assert safe_download_ext(ext) == expected


@pytest.mark.parametrize(
    "content_type,key,expected",
    [
        ("image/jpeg", None, ".jpg"),
        ("IMAGE/JPG; name=photo", "", ".jpg"),
        ("image/svg+xml", "evidence/drawing", ".svg"),
        ("application/pdf", None, ".pdf"),
        (None, None, ""),
        ("  ; charset=utf-8", "evidence/blob", ""),
        ("application/x-not-a-registered-type", None, ""),
        ("image/png", "evidence/photo.JPEG", ".jpeg"),
        ("image/png", "evidence/photo.extremelylong", ".png"),
    ],
)
def test_a_download_extension_comes_from_the_key_then_the_media_type(
    content_type, key, expected
):
    assert guess_extension(content_type, key) == expected


def test_an_artifact_download_is_named_for_the_artifact_and_its_media_type():
    artifact = SimpleNamespace(id="42", content_type="image/jpeg")
    assert download_filename_for_artifact(artifact, "diary/attachment-1") == (
        "artifact-42.jpg"
    )
    untyped = SimpleNamespace(id="43", content_type=None)
    assert download_filename_for_artifact(untyped, "diary/attachment-2") == (
        "artifact-43"
    )


def test_jpeg_and_svg_downloads_keep_their_usual_extension_on_any_host(monkeypatch):
    # Host MIME tables differ; some list .jpe or .svgz first.
    host_table = {"image/jpeg": ".jpe", "image/svg+xml": ".svgz", "text/csv": ".csv"}
    monkeypatch.setattr(
        filenames.mimetypes, "guess_extension", lambda ctype: host_table.get(ctype)
    )
    assert guess_extension("image/jpeg", None) == ".jpg"
    assert guess_extension("image/svg+xml", None) == ".svg"
    assert guess_extension("text/csv", None) == ".csv"
