"""Synthetic draft assets and legacy storage contract tests."""

import io
import json
from pathlib import Path
import shutil
import uuid

from PIL import Image
import pytest

from backend.asset_store import AssetStore
from backend.errors import AppError


def image_bytes(format_name="PNG", size=(8, 8), orientation=None):
    """Encode a synthetic solid image without any production data."""
    output = io.BytesIO()
    options = {}
    if orientation:
        exif = Image.Exif()
        exif[274] = orientation
        options["exif"] = exif
    Image.new("RGB", size, "#00aabb").save(output, format_name, **options)
    return output.getvalue()


def test_reads_legacy_draft_and_preserves_original_bytes(tmp_path):
    shutil.copytree(
        Path(__file__).parent / "fixtures/legacy_data",
        tmp_path,
        dirs_exist_ok=True,
    )
    store = AssetStore(tmp_path)
    draft_id = "11111111-1111-4111-8111-111111111111"
    draft = store.read_draft(draft_id)
    record = draft["images"][0]
    original = tmp_path / "drafts" / draft_id / record["originalName"]
    assert (
        store.read_image(draft_id, record["assetId"])[0]
        == original.read_bytes()
    )
    assert (
        store.read_image(draft_id, record["assetId"], True)[1] == "image/webp"
    )
    job_id = str(uuid.uuid4())
    assert store.copy_to_job(draft_id, job_id) == draft["images"]
    copied = tmp_path / "jobs" / job_id / "originals" / record["originalName"]
    assert copied.read_bytes() == original.read_bytes()
    source_preview = tmp_path / "drafts" / draft_id / record["previewName"]
    copied_preview = (
        tmp_path / "jobs" / job_id / "originals" / record["previewName"]
    )
    assert copied_preview.read_bytes() == source_preview.read_bytes()


def test_rejects_fake_truncated_oversize_and_too_many_images(tmp_path):
    store = AssetStore(tmp_path)
    draft_id = store.create_draft()["draftId"]
    directory = tmp_path / "drafts" / draft_id
    for content in (
        b"fake",
        b"",
        image_bytes()[:40],
        image_bytes("JPEG")[:-20],
        image_bytes("WEBP")[:30],
        bytes(10 * 1024 * 1024 + 1),
        image_bytes("GIF"),
    ):
        with pytest.raises(AppError):
            store.add_image(draft_id, content, "fake.png")
        assert list(directory.iterdir()) == [directory / "draft.json"]
    for _ in range(6):
        store.add_image(draft_id, image_bytes(), "a.png")
    before = set(directory.iterdir())
    with pytest.raises(AppError):
        store.add_image(draft_id, image_bytes(), "seventh.png")
    assert len(store.read_draft(draft_id)["images"]) == 6
    assert set(directory.iterdir()) == before


@pytest.mark.parametrize(
    "format_name,mime",
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")],
)
def test_preview_and_original_contract(tmp_path, format_name, mime):
    store = AssetStore(tmp_path)
    draft_id = store.create_draft()["draftId"]
    content = image_bytes(format_name, (800, 200))
    result = store.add_image(draft_id, content, "x" * 160)
    assert result["mimeType"] == mime
    assert result["previewUrl"] == (
        f"/api/drafts/{draft_id}/images/{result['assetId']}/preview"
    )
    assert store.read_image(draft_id, result["assetId"]) == (content, mime)
    preview, preview_mime = store.read_image(draft_id, result["assetId"], True)
    assert preview_mime == "image/webp"
    assert Image.open(io.BytesIO(preview)).size == (400, 100)
    assert len(store.read_draft(draft_id)["images"][0]["filename"]) == 150
    store.remove_image(draft_id, result["assetId"])
    assert store.read_draft(draft_id)["images"] == []
    assert len(list((tmp_path / "drafts" / draft_id).iterdir())) == 1


def test_exif_orientation(tmp_path):
    store = AssetStore(tmp_path)
    draft_id = store.create_draft()["draftId"]
    content = image_bytes("JPEG", (800, 200), 6)
    asset_id = store.add_image(draft_id, content, "rotated.jpg")["assetId"]
    preview = store.read_image(draft_id, asset_id, True)[0]
    assert Image.open(io.BytesIO(preview)).size == (100, 400)
    assert store.read_image(draft_id, asset_id)[0] == content


def test_rejects_pixel_limit_before_decode(tmp_path):
    store = AssetStore(tmp_path)
    draft_id = store.create_draft()["draftId"]
    # A real highly compressible PNG exceeds the decoded 80 million limit.
    content = image_bytes("PNG", (9000, 9000))
    with pytest.raises(AppError):
        store.add_image(draft_id, content, "huge.png")
    assert store.read_draft(draft_id)["images"] == []


def test_uuid_constraints_and_expiry(tmp_path):
    store = AssetStore(tmp_path)
    old = store.create_draft()["draftId"]
    current = store.create_draft()["draftId"]
    path = tmp_path / "drafts" / old / "draft.json"
    draft = json.loads(path.read_text())
    draft["createdAt"] = 1000
    path.write_text(json.dumps(draft))
    unrelated = tmp_path / "drafts" / "unrelated"
    unrelated.mkdir()
    assert store.cleanup_expired(1000 + 86400000) == 0
    assert store.cleanup_expired(1001 + 86400000) == 1
    assert store.read_draft(current)["images"] == []
    assert unrelated.exists()
    for operation in (
        lambda: store.read_draft("../jobs"),
        lambda: store.remove_draft("../jobs"),
        lambda: store.read_image(current, "../secret"),
        lambda: store.copy_to_job(current, "../jobs"),
    ):
        with pytest.raises(AppError):
            operation()
    store.remove_draft(current)
    with pytest.raises(AppError) as error:
        store.read_draft(current)
    assert error.value.status_code == 404


def test_failed_metadata_write_cleans_assets(tmp_path, monkeypatch):
    store = AssetStore(tmp_path)
    draft_id = store.create_draft()["draftId"]
    original_replace = Path.replace

    def fail_metadata(path, target):
        if Path(target).name == "draft.json":
            raise OSError("synthetic disk failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_metadata)
    with pytest.raises(OSError):
        store.add_image(draft_id, image_bytes(), "sample.png")
    assert store.read_draft(draft_id)["images"] == []
    assert len(list((tmp_path / "drafts" / draft_id).iterdir())) == 1


def test_legacy_metadata_cannot_escape_directory(tmp_path):
    store = AssetStore(tmp_path)
    draft_id = store.create_draft()["draftId"]
    result = store.add_image(draft_id, image_bytes(), "sample.png")
    path = tmp_path / "drafts" / draft_id / "draft.json"
    draft = json.loads(path.read_text())
    draft["images"][0]["originalName"] = "../../secret.png"
    path.write_text(json.dumps(draft))
    with pytest.raises(AppError):
        store.read_image(draft_id, result["assetId"])


def test_copy_failure_removes_only_new_assets(tmp_path, monkeypatch):
    store = AssetStore(tmp_path)
    draft_id = store.create_draft()["draftId"]
    store.add_image(draft_id, image_bytes(), "sample.png")
    job_id = str(uuid.uuid4())
    directory = tmp_path / "jobs" / job_id / "originals"
    directory.mkdir(parents=True)
    sentinel = directory / "existing.txt"
    sentinel.write_text("keep")
    original_replace = Path.replace

    def fail_preview(path, target):
        if Path(target).name.endswith(".preview.webp"):
            raise OSError("synthetic copy failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_preview)
    with pytest.raises(OSError):
        store.copy_to_job(draft_id, job_id)
    assert list(directory.iterdir()) == [sentinel]
    assert len(store.read_draft(draft_id)["images"]) == 1


def test_exact_byte_limit_is_accepted(tmp_path):
    store = AssetStore(tmp_path)
    draft_id = store.create_draft()["draftId"]
    png = image_bytes()
    content = png + bytes(10 * 1024 * 1024 - len(png))
    asset_id = store.add_image(draft_id, content, "exact.png")["assetId"]
    assert store.read_image(draft_id, asset_id)[0] == content
